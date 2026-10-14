"""Cálculo de hash de arquivos com leitura em blocos.

Características principais:

* Leitura em blocos (padrão de 4 MB), sem carregar o arquivo inteiro na RAM;
* Suporte a vários algoritmos simultâneos em **uma única leitura** do arquivo;
* Abertura estritamente em modo binário de leitura (``rb``), sem qualquer
  alteração de conteúdo, atributos ou datas;
* Detecção de alteração do arquivo durante o cálculo (tamanho e ``mtime``);
* Cancelamento seguro entre blocos;
* Tradução das exceções do sistema operacional para mensagens em português.
"""

from __future__ import annotations

import errno
import hashlib
import os
import threading
from typing import Callable, Iterable, Mapping, Sequence

from utils.paths import to_extended_path

from .models import DEFAULT_CHUNK_SIZE, HashOutcome

# Nomes exibidos ao usuário -> identificadores aceitos por ``hashlib``.
SUPPORTED_ALGORITHMS: Mapping[str, str] = {
    "MD5": "md5",
    "SHA-1": "sha1",
    "SHA-256": "sha256",
    "SHA-512": "sha512",
}

# Ordem canônica usada nas colunas da planilha e na interface.
ALGORITHM_ORDER: tuple[str, ...] = ("MD5", "SHA-1", "SHA-256", "SHA-512")

DEFAULT_ALGORITHM = "SHA-256"

# Os digests são gravados em maiúsculas (conforme especificação do relatório).
UPPERCASE_DIGESTS = True

# Limite de leitura para arquivos cujo tamanho informado pelo sistema é zero,
# mas que ainda assim devolvem dados (situação rara, típica de arquivos
# virtuais). Evita leitura sem fim mantendo o processamento previsível.
_UNKNOWN_SIZE_LIMIT = 1024 * 1024 * 1024  # 1 GiB

# Códigos de erro do Windows traduzidos para mensagens objetivas.
_WINERROR_MESSAGES: Mapping[int, str] = {
    2: "Arquivo não encontrado",
    3: "Caminho não encontrado",
    4: "Não foi possível abrir o arquivo (limite do sistema)",
    5: "Acesso negado",
    15: "Unidade não encontrada",
    19: "Mídia protegida contra gravação",
    21: "Dispositivo não está pronto",
    23: "Erro de dados (CRC) na leitura",
    27: "Setor não encontrado no dispositivo",
    32: "Arquivo em uso por outro processo",
    33: "Arquivo bloqueado por outro processo",
    53: "Caminho de rede não encontrado",
    59: "Erro inesperado de rede",
    64: "O nome de rede especificado não está mais disponível",
    112: "Espaço insuficiente no dispositivo",
    123: "Nome de arquivo ou caminho inválido",
    145: "Diretório não está vazio",
    206: "Caminho excessivamente longo",
    433: "Dispositivo desconectado ou inacessível",
    1006: "O volume foi alterado externamente",
    1117: "Erro de E/S no dispositivo",
    1167: "Dispositivo desconectado",
    1392: "Arquivo ou diretório corrompido e ilegível",
}

# Códigos POSIX (usados como alternativa quando não há ``winerror``).
_ERRNO_MESSAGES: Mapping[int, str] = {
    errno.EACCES: "Acesso negado",
    errno.EPERM: "Operação não permitida",
    errno.ENOENT: "Arquivo não encontrado",
    errno.EISDIR: "O caminho é um diretório",
    errno.ENAMETOOLONG: "Nome ou caminho excessivamente longo",
    errno.EIO: "Erro de leitura no dispositivo",
    errno.EBUSY: "Arquivo ou dispositivo em uso",
    errno.ENODEV: "Dispositivo não disponível",
    errno.ENXIO: "Dispositivo não configurado",
    errno.EMFILE: "Muitos arquivos abertos simultaneamente",
    errno.ELOOP: "Excesso de links simbólicos no caminho",
    errno.EINVAL: "Argumento inválido para leitura do arquivo",
}


class UnsupportedAlgorithmError(ValueError):
    """Algoritmo de hash solicitado que não é suportado pela aplicação."""


def normalize_algorithms(algorithms: Iterable[str] | None) -> list[str]:
    """Valida e ordena a lista de algoritmos selecionados.

    Remove duplicidades, mantém a ordem canônica e devolve ``["SHA-256"]``
    quando nenhum algoritmo válido é informado.
    """
    if not algorithms:
        return [DEFAULT_ALGORITHM]

    selected: set[str] = set()
    for item in algorithms:
        if item is None:
            continue
        name = str(item).strip().upper()
        # Aceita variações como "sha256" ou "sha_256".
        name = name.replace("_", "-")
        if name not in SUPPORTED_ALGORITHMS:
            compact = name.replace("-", "")
            matches = [key for key in SUPPORTED_ALGORITHMS if key.replace("-", "") == compact]
            if not matches:
                raise UnsupportedAlgorithmError(f"Algoritmo não suportado: {item!r}")
            name = matches[0]
        selected.add(name)

    if not selected:
        return [DEFAULT_ALGORITHM]
    return [name for name in ALGORITHM_ORDER if name in selected]


def build_hashers(algorithms: Sequence[str]) -> dict[str, "hashlib._Hash"]:
    """Cria um objeto ``hashlib`` novo para cada algoritmo selecionado.

    Um conjunto novo é criado por arquivo — objetos ``hashlib`` são acumulativos
    e não podem ser reaproveitados entre arquivos diferentes.
    """
    return {name: hashlib.new(SUPPORTED_ALGORITHMS[name]) for name in algorithms}


def classify_os_error(exc: OSError) -> tuple[str, str]:
    """Traduz uma exceção do sistema em ``(tipo do erro, descrição)``."""
    winerror = getattr(exc, "winerror", None)
    if winerror and winerror in _WINERROR_MESSAGES:
        error_type = _WINERROR_MESSAGES[winerror]
    elif exc.errno in _ERRNO_MESSAGES:
        error_type = _ERRNO_MESSAGES[exc.errno]
    elif isinstance(exc, PermissionError):
        error_type = "Acesso negado"
    elif isinstance(exc, FileNotFoundError):
        error_type = "Arquivo não encontrado"
    elif isinstance(exc, IsADirectoryError):
        error_type = "O caminho é um diretório"
    else:
        error_type = "Erro de leitura"

    description = (exc.strerror or str(exc) or error_type).strip()
    code = winerror if winerror else exc.errno
    if code:
        description = f"{description} (código {code})"
    return error_type, description


def hash_file(
    path: str | os.PathLike[str],
    algorithms: Sequence[str],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    cancel_event: threading.Event | None = None,
    on_chunk: Callable[[int], None] | None = None,
) -> HashOutcome:
    """Calcula o(s) hash(es) de um arquivo lendo-o em blocos.

    Args:
        path: Caminho completo do arquivo.
        algorithms: Algoritmos a calcular (nomes de ``SUPPORTED_ALGORITHMS``).
        chunk_size: Tamanho do bloco de leitura, em bytes.
        cancel_event: Evento consultado entre blocos para cancelamento seguro.
        on_chunk: Callback opcional que recebe a quantidade de bytes lidos em
            cada bloco (usado para atualizar o progresso da interface).

    Returns:
        Um :class:`HashOutcome` — nunca levanta exceção de E/S: falhas são
        registradas no próprio resultado para que a análise prossiga.
    """
    outcome = HashOutcome()
    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE

    hashers = build_hashers(list(algorithms))
    native_path = to_extended_path(path)

    try:
        stat_before = os.stat(native_path)
        outcome.size_at_open = int(stat_before.st_size)
        buffer = bytearray(chunk_size)
        view = memoryview(buffer)

        # A leitura é limitada ao tamanho observado na abertura do arquivo.
        # Isso garante que o hash corresponda ao arquivo como ele estava no
        # início do cálculo e impede laço infinito em arquivos que crescem
        # continuamente durante a leitura (logs, gravações em andamento).
        remaining = stat_before.st_size if stat_before.st_size > 0 else _UNKNOWN_SIZE_LIMIT

        # buffering=0 evita uma cópia intermediária: lemos direto no buffer.
        with open(native_path, "rb", buffering=0) as handle:
            while remaining > 0:
                if cancel_event is not None and cancel_event.is_set():
                    outcome.cancelled = True
                    return outcome

                read = handle.readinto(view[: min(chunk_size, remaining)])
                if not read:
                    break  # Fim do arquivo (ou arquivo reduzido durante a leitura).

                block = view[:read]
                for digest in hashers.values():
                    digest.update(block)
                outcome.bytes_read += read
                remaining -= read
                if on_chunk is not None:
                    on_chunk(read)

        stat_after = os.stat(native_path)
    except OSError as exc:
        error_type, description = classify_os_error(exc)
        outcome.error_type = error_type
        outcome.error_message = description
        return outcome
    except MemoryError:
        outcome.error_type = "Memória insuficiente"
        outcome.error_message = (
            "Não foi possível alocar o bloco de leitura. Reduza o tamanho do bloco."
        )
        return outcome

    # Verificação de integridade: o arquivo mudou enquanto era lido?
    outcome.modified = (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
        or outcome.bytes_read != stat_before.st_size
    )

    outcome.digests = {
        name: (digest.hexdigest().upper() if UPPERCASE_DIGESTS else digest.hexdigest())
        for name, digest in hashers.items()
    }
    return outcome


def hash_bytes(data: bytes, algorithms: Sequence[str]) -> dict[str, str]:
    """Calcula hashes de um conteúdo em memória (usado em testes e validações)."""
    hashers = build_hashers(list(algorithms))
    for digest in hashers.values():
        digest.update(data)
    return {
        name: (digest.hexdigest().upper() if UPPERCASE_DIGESTS else digest.hexdigest())
        for name, digest in hashers.items()
    }
