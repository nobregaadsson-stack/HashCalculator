"""Testes do cálculo de hash (valores conhecidos, blocos, erros e cancelamento)."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from core.hasher import (
    DEFAULT_ALGORITHM,
    UnsupportedAlgorithmError,
    classify_os_error,
    hash_bytes,
    hash_file,
    normalize_algorithms,
)

# Vetores de teste públicos (RFC 1321 / FIPS 180-4).
SHA256_EMPTY = "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855"
MD5_EMPTY = "D41D8CD98F00B204E9800998ECF8427E"
SHA256_ABC = "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD"
MD5_ABC = "900150983CD24FB0D6963F7D28E17F72"
SHA1_ABC = "A9993E364706816ABA3E25717850C26C9CD0D89D"
SHA512_ABC = (
    "DDAF35A193617ABACC417349AE20413112E6FA4E89A97EA20A9EEEE64B55D39A"
    "2192992A274FC1A836BA3C23A3FEEBBD454D4423643CE80E2A9AC94FA54CA49F"
)


def test_sha256_de_arquivo_vazio(tmp_path: Path) -> None:
    """Arquivos vazios devem ser processados e produzir o hash canônico."""
    target = tmp_path / "vazio.bin"
    target.write_bytes(b"")

    outcome = hash_file(target, ["SHA-256", "MD5"])

    assert not outcome.failed
    assert outcome.bytes_read == 0
    assert outcome.digests["SHA-256"] == SHA256_EMPTY
    assert outcome.digests["MD5"] == MD5_EMPTY


def test_hashes_conhecidos_de_abc(tmp_path: Path) -> None:
    """Confere os quatro algoritmos contra vetores de teste públicos."""
    target = tmp_path / "abc.txt"
    target.write_bytes(b"abc")

    outcome = hash_file(target, ["MD5", "SHA-1", "SHA-256", "SHA-512"])

    assert outcome.digests["MD5"] == MD5_ABC
    assert outcome.digests["SHA-1"] == SHA1_ABC
    assert outcome.digests["SHA-256"] == SHA256_ABC
    assert outcome.digests["SHA-512"] == SHA512_ABC


@pytest.mark.parametrize("chunk_size", [1, 7, 4096, 1024 * 1024])
def test_leitura_em_blocos_produz_o_mesmo_hash(tmp_path: Path, chunk_size: int) -> None:
    """O resultado não pode depender do tamanho do bloco de leitura."""
    payload = bytes(range(256)) * 5000  # ~1,25 MB
    target = tmp_path / "dados.bin"
    target.write_bytes(payload)

    esperado = hashlib.sha256(payload).hexdigest().upper()
    outcome = hash_file(target, ["SHA-256"], chunk_size=chunk_size)

    assert outcome.digests["SHA-256"] == esperado
    assert outcome.bytes_read == len(payload)
    # Tamanho autoritativo, lido no momento da abertura do arquivo.
    assert outcome.size_at_open == len(payload)


def test_arquivo_grande_em_multiplos_blocos(tmp_path: Path) -> None:
    """Arquivo maior que o bloco de leitura é processado em várias passagens."""
    chunk = 1024 * 1024
    payload = b"X" * (chunk * 8 + 12345)  # ~8 MB
    target = tmp_path / "grande.bin"
    target.write_bytes(payload)

    blocos: list[int] = []
    outcome = hash_file(target, ["SHA-256"], chunk_size=chunk, on_chunk=blocos.append)

    assert len(blocos) >= 9  # Confirma que houve leitura em blocos.
    assert max(blocos) <= chunk
    assert sum(blocos) == len(payload)
    assert outcome.digests["SHA-256"] == hashlib.sha256(payload).hexdigest().upper()


def test_nome_unicode_e_caracteres_especiais(tmp_path: Path) -> None:
    """Nomes com acentos e símbolos devem funcionar normalmente."""
    target = tmp_path / "acentuação — çãõ (1) [teste] #2.txt"
    target.write_bytes("conteúdo com acentuação".encode("utf-8"))

    outcome = hash_file(target, ["SHA-256"])

    assert not outcome.failed
    assert outcome.digests["SHA-256"] == hash_bytes(
        "conteúdo com acentuação".encode("utf-8"), ["SHA-256"]
    )["SHA-256"]


def test_arquivo_inexistente_nao_levanta_excecao(tmp_path: Path) -> None:
    """Falhas de leitura viram resultado com erro, sem interromper a análise."""
    outcome = hash_file(tmp_path / "nao_existe.bin", ["SHA-256"])

    assert outcome.failed
    assert outcome.error_type == "Arquivo não encontrado"
    assert outcome.status_text().startswith("ERRO - ")


def test_acesso_negado_e_tratado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Erro de permissão é traduzido e não interrompe o processamento."""
    target = tmp_path / "protegido.bin"
    target.write_bytes(b"dados")

    def falha(*_args: object, **_kwargs: object):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("core.hasher.open", falha, raising=False)
    outcome = hash_file(target, ["SHA-256"])

    assert outcome.failed
    assert outcome.error_type == "Acesso negado"


def test_cancelamento_interrompe_o_calculo(tmp_path: Path) -> None:
    """O cancelamento é observado entre os blocos de leitura."""
    target = tmp_path / "cancelar.bin"
    target.write_bytes(b"Y" * (512 * 1024))

    evento = threading.Event()
    evento.set()  # Já cancelado antes de iniciar.

    outcome = hash_file(target, ["SHA-256"], chunk_size=4096, cancel_event=evento)

    assert outcome.cancelled
    assert not outcome.digests
    assert outcome.status_text() == "NÃO PROCESSADO - Análise cancelada pelo usuário"


def test_cancelamento_no_meio_do_arquivo(tmp_path: Path) -> None:
    """O cancelamento acionado durante a leitura interrompe o arquivo atual."""
    target = tmp_path / "meio.bin"
    target.write_bytes(b"Z" * (256 * 1024))

    evento = threading.Event()
    lidos: list[int] = []

    def ao_ler(size: int) -> None:
        lidos.append(size)
        if len(lidos) == 2:
            evento.set()

    outcome = hash_file(target, ["SHA-256"], chunk_size=4096, cancel_event=evento, on_chunk=ao_ler)

    assert outcome.cancelled
    assert 0 < outcome.bytes_read < 256 * 1024


def test_deteccao_de_alteracao_durante_o_calculo(tmp_path: Path) -> None:
    """Alteração do arquivo durante a leitura gera o status de atenção."""
    tamanho = 32 * 1024
    target = tmp_path / "mutante.bin"
    target.write_bytes(b"A" * tamanho)

    def ao_ler(_size: int) -> None:
        # Simula outro processo gravando no arquivo durante o cálculo.
        with open(target, "ab") as handle:
            handle.write(b"B" * 1024)

    outcome = hash_file(target, ["SHA-256"], chunk_size=4096, on_chunk=ao_ler)

    assert not outcome.failed
    assert outcome.modified
    assert outcome.status_text().startswith("ATENÇÃO")
    # A leitura é limitada ao tamanho original: o hash corresponde ao arquivo
    # como ele estava no início do cálculo.
    assert outcome.bytes_read == tamanho
    assert outcome.digests["SHA-256"] == hashlib.sha256(b"A" * tamanho).hexdigest().upper()


def test_arquivo_em_crescimento_nao_gera_laco_infinito(tmp_path: Path) -> None:
    """Arquivo que cresce mais rápido do que é lido não trava a análise."""
    target = tmp_path / "crescendo.log"
    target.write_bytes(b"L" * 8192)

    def ao_ler(size: int) -> None:
        with open(target, "ab") as handle:
            handle.write(b"L" * size * 2)  # Cresce o dobro do que foi lido.

    outcome = hash_file(target, ["SHA-256"], chunk_size=1024, on_chunk=ao_ler)

    assert outcome.bytes_read == 8192
    assert outcome.modified


def test_arquivo_reduzido_durante_a_leitura(tmp_path: Path) -> None:
    """Se o arquivo encolher durante a leitura, o resultado é sinalizado."""
    target = tmp_path / "encolhendo.bin"
    target.write_bytes(b"C" * (16 * 1024))

    def ao_ler(_size: int) -> None:
        with open(target, "r+b") as handle:
            handle.truncate(4096)

    outcome = hash_file(target, ["SHA-256"], chunk_size=4096, on_chunk=ao_ler)

    assert not outcome.failed
    assert outcome.modified
    assert outcome.bytes_read < 16 * 1024


def test_normalizacao_de_algoritmos() -> None:
    """A seleção de algoritmos é validada e ordenada de forma canônica."""
    assert normalize_algorithms(None) == [DEFAULT_ALGORITHM]
    assert normalize_algorithms([]) == [DEFAULT_ALGORITHM]
    assert normalize_algorithms(["sha256"]) == ["SHA-256"]
    assert normalize_algorithms(["SHA-512", "md5", "MD5"]) == ["MD5", "SHA-512"]
    with pytest.raises(UnsupportedAlgorithmError):
        normalize_algorithms(["sha3-256"])


def test_classificacao_de_erro_do_sistema() -> None:
    """Exceções do sistema são traduzidas para mensagens em português."""
    tipo, descricao = classify_os_error(PermissionError(13, "Permission denied"))
    assert tipo == "Acesso negado"
    assert "13" in descricao
