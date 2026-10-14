"""Testes da varredura recursiva de diretórios."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.models import ScanIssue, ScannedFile
from core.scanner import scan_directory, validate_source


def _files(root: Path, **kwargs: object) -> list[ScannedFile]:
    return [item for item in scan_directory(root, **kwargs) if isinstance(item, ScannedFile)]


def _issues(root: Path, **kwargs: object) -> list[ScanIssue]:
    return [item for item in scan_directory(root, **kwargs) if isinstance(item, ScanIssue)]


def test_percorre_diretorios_aninhados(sample_tree: Path) -> None:
    """Todos os arquivos de todos os subdiretórios devem ser localizados."""
    encontrados = _files(sample_tree)

    assert len(encontrados) == 5
    nomes = sorted(item.name for item in encontrados)
    assert nomes == sorted(
        [
            "vazio.txt",
            "relatório.txt",
            "acentuação çãõ.txt",
            "foto 01.jpg",
            "relatório.txt",
        ]
    )


def test_metadados_do_arquivo(sample_tree: Path) -> None:
    """Nome, extensão, tamanho e caminho relativo são preenchidos corretamente."""
    encontrados = {item.rel_path: item for item in _files(sample_tree)}
    chave = os.path.join("imagens", "foto 01.jpg")

    item = encontrados[chave]
    assert item.name == "foto 01.jpg"
    assert item.ext == ".jpg"
    assert item.size == len(b"binario B")
    assert item.path.endswith(chave)
    assert item.mtime_ns > 0


def test_arquivo_vazio_e_incluido(sample_tree: Path) -> None:
    """Arquivos de tamanho zero também entram no inventário."""
    vazios = [item for item in _files(sample_tree) if item.size == 0]
    assert len(vazios) == 1
    assert vazios[0].name == "vazio.txt"


def test_diretorio_inacessivel_nao_interrompe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Erro ao listar um diretório vira ocorrência; a varredura continua."""
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "a.txt").write_bytes(b"a")
    (tmp_path / "negado").mkdir()
    (tmp_path / "negado" / "b.txt").write_bytes(b"b")

    scandir_original = os.scandir

    def scandir_falho(path):  # type: ignore[no-untyped-def]
        if "negado" in str(path):
            raise PermissionError(13, "Permission denied")
        return scandir_original(path)

    monkeypatch.setattr("core.scanner.os.scandir", scandir_falho)

    resultado = list(scan_directory(tmp_path))
    arquivos = [item for item in resultado if isinstance(item, ScannedFile)]
    ocorrencias = [item for item in resultado if isinstance(item, ScanIssue)]

    assert [item.name for item in arquivos] == ["a.txt"]
    assert len(ocorrencias) == 1
    assert ocorrencias[0].error_type == "Acesso negado"


def test_cancelamento_interrompe_a_varredura(sample_tree: Path) -> None:
    """O evento de cancelamento encerra a varredura imediatamente."""
    import threading

    evento = threading.Event()
    evento.set()

    assert list(scan_directory(sample_tree, cancel_event=evento)) == []


@pytest.mark.skipif(os.name != "nt", reason="Junction points são específicos do Windows.")
def test_junction_point_nao_e_seguido(tmp_path: Path) -> None:
    """Junctions não são percorridas por padrão (evita laços e duplicidade)."""
    alvo = tmp_path / "alvo"
    alvo.mkdir()
    (alvo / "dado.txt").write_bytes(b"conteudo")

    link = tmp_path / "atalho"
    resultado = os.system(f'mklink /J "{link}" "{alvo}" >nul 2>&1')
    if resultado != 0 or not link.exists():
        pytest.skip("Não foi possível criar o junction point neste ambiente.")

    arquivos = _files(tmp_path)
    ocorrencias = _issues(tmp_path)

    # O arquivo é contado apenas uma vez (pelo caminho real).
    assert len(arquivos) == 1
    assert any("junction" in item.message.lower() for item in ocorrencias)


@pytest.mark.skipif(os.name != "nt", reason="Teste específico de caminhos longos do Windows.")
def test_caminho_longo_e_processado(tmp_path: Path) -> None:
    """Caminhos acima de 260 caracteres são acessados via prefixo estendido."""
    from utils.paths import to_extended_path

    profundo = tmp_path
    for _ in range(12):
        profundo = profundo / ("pasta_" + "x" * 20)
    try:
        os.makedirs(to_extended_path(profundo), exist_ok=True)
        alvo = profundo / ("arquivo_" + "y" * 40 + ".txt")
        with open(to_extended_path(alvo), "wb") as handle:
            handle.write(b"conteudo longo")
    except OSError:
        pytest.skip("O sistema de arquivos não permitiu criar o caminho longo.")

    assert len(str(alvo)) > 260
    arquivos = _files(tmp_path)
    assert len(arquivos) == 1
    assert arquivos[0].size == len(b"conteudo longo")


def test_validacao_da_origem(tmp_path: Path) -> None:
    """A origem é validada antes do início da análise."""
    valido, _ = validate_source(tmp_path)
    assert valido

    valido, mensagem = validate_source("")
    assert not valido and "Nenhuma" in mensagem

    valido, mensagem = validate_source(tmp_path / "inexistente")
    assert not valido and "não existe" in mensagem

    arquivo = tmp_path / "arquivo.txt"
    arquivo.write_bytes(b"x")
    valido, mensagem = validate_source(arquivo)
    assert not valido and "não é uma pasta" in mensagem
