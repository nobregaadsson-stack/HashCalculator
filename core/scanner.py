"""Varredura recursiva de pastas, unidades e dispositivos de armazenamento.

A varredura é **iterativa** (usa uma pilha explícita em vez de recursão), o que
evita estouro de pilha em estruturas muito profundas, e trata individualmente os
erros de cada diretório/arquivo para que a análise nunca seja interrompida.

Links simbólicos e *junction points* não são seguidos por padrão, evitando
laços infinitos e contagem duplicada de arquivos.
"""

from __future__ import annotations

import os
import stat as stat_module
import threading
from typing import Callable, Iterator

from utils.paths import to_extended_path

from .hasher import classify_os_error
from .models import ScanIssue, ScannedFile

# Atributo do Windows que identifica links simbólicos e junction points.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

ISSUE_TYPE_LINK = "Link simbólico/Junction"
ISSUE_TYPE_SPECIAL = "Arquivo especial"
ISSUE_TYPE_LOOP = "Laço de diretórios"

SKIP_REASON_LINK = "Link simbólico ou junction não seguido"


def _is_reparse_point(entry: os.DirEntry[str]) -> bool:
    """Identifica links simbólicos, junctions e demais reparse points."""
    try:
        if entry.is_symlink():
            return True
    except OSError:
        pass
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _relative_path(full_path: str, root: str) -> str:
    """Caminho relativo à origem; devolve o caminho completo em caso de falha."""
    try:
        return os.path.relpath(full_path, root)
    except ValueError:
        return full_path


def _make_scanned_file(
    full_path: str,
    name: str,
    root: str,
    info: os.stat_result,
    is_link: bool,
    skip_reason: str | None,
) -> ScannedFile:
    """Monta o registro de um arquivo localizado."""
    return ScannedFile(
        path=full_path,
        name=name,
        ext=os.path.splitext(name)[1].lower(),
        rel_path=_relative_path(full_path, root),
        size=int(getattr(info, "st_size", 0) or 0),
        mtime_ns=int(getattr(info, "st_mtime_ns", 0) or 0),
        is_link=is_link,
        skip_reason=skip_reason,
    )


def scan_directory(
    root: str | os.PathLike[str],
    follow_links: bool = False,
    cancel_event: threading.Event | None = None,
    on_directory: Callable[[str], None] | None = None,
) -> Iterator[ScannedFile | ScanIssue]:
    """Percorre recursivamente ``root`` devolvendo arquivos e ocorrências.

    Args:
        root: Pasta, diretório, raiz de unidade (``E:\\``) ou dispositivo.
        follow_links: Quando ``True``, segue links simbólicos e junctions
            (com proteção contra laços). Padrão: ``False``.
        cancel_event: Evento que interrompe a varredura de forma segura.
        on_directory: Callback chamado a cada diretório efetivamente aberto.

    Yields:
        :class:`ScannedFile` para cada arquivo localizado e :class:`ScanIssue`
        para diretórios inacessíveis, links ignorados e arquivos especiais.
    """
    root_path = os.path.abspath(os.fspath(root))
    pending: list[str] = [root_path]
    visited_dirs: set[str] = set()

    while pending:
        if cancel_event is not None and cancel_event.is_set():
            return

        current = pending.pop()
        if on_directory is not None:
            on_directory(current)

        try:
            with os.scandir(to_extended_path(current)) as iterator:
                for entry in iterator:
                    if cancel_event is not None and cancel_event.is_set():
                        return

                    # O caminho é remontado a partir da origem legível para que
                    # o relatório não exiba o prefixo "\\?\".
                    full_path = os.path.join(current, entry.name)
                    try:
                        yield from _process_entry(
                            entry=entry,
                            full_path=full_path,
                            root=root_path,
                            follow_links=follow_links,
                            pending=pending,
                            visited_dirs=visited_dirs,
                        )
                    except OSError as exc:
                        error_type, description = classify_os_error(exc)
                        yield ScanIssue(
                            path=full_path,
                            name=entry.name,
                            error_type=error_type,
                            message=description,
                        )
        except OSError as exc:
            error_type, description = classify_os_error(exc)
            yield ScanIssue(
                path=current,
                name=os.path.basename(current.rstrip("\\/")) or current,
                error_type=error_type,
                message=f"Não foi possível listar o diretório: {description}",
            )


def _process_entry(
    entry: os.DirEntry[str],
    full_path: str,
    root: str,
    follow_links: bool,
    pending: list[str],
    visited_dirs: set[str],
) -> Iterator[ScannedFile | ScanIssue]:
    """Classifica uma entrada de diretório (arquivo, pasta, link ou especial)."""
    is_reparse = _is_reparse_point(entry)

    # --- Links simbólicos e junctions não seguidos --------------------------
    if is_reparse and not follow_links:
        try:
            target_is_dir = entry.is_dir(follow_symlinks=True)
        except OSError:
            target_is_dir = False

        if target_is_dir:
            # Diretório: apenas registrado, para não percorrer o mesmo conteúdo
            # duas vezes nem entrar em laços infinitos.
            yield ScanIssue(
                path=full_path,
                name=entry.name,
                error_type=ISSUE_TYPE_LINK,
                message="Diretório ignorado por ser link simbólico ou junction point",
            )
            return

        # Arquivo: entra no inventário, porém sem cálculo de hash.
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            info = os.stat_result((0,) * 10)
        yield _make_scanned_file(full_path, entry.name, root, info, True, SKIP_REASON_LINK)
        return

    # --- Diretórios ---------------------------------------------------------
    if entry.is_dir(follow_symlinks=follow_links):
        if follow_links:
            # Proteção contra laços: um mesmo diretório real só é visitado uma vez.
            try:
                real = os.path.realpath(full_path)
            except OSError:
                real = full_path
            key = os.path.normcase(real)
            if key in visited_dirs:
                yield ScanIssue(
                    path=full_path,
                    name=entry.name,
                    error_type=ISSUE_TYPE_LOOP,
                    message=f"Diretório já visitado através de outro caminho ({real})",
                )
                return
            visited_dirs.add(key)
        pending.append(full_path)
        return

    # --- Arquivos comuns ----------------------------------------------------
    if entry.is_file(follow_symlinks=follow_links):
        info = entry.stat(follow_symlinks=follow_links)
        yield _make_scanned_file(full_path, entry.name, root, info, is_reparse, None)
        return

    # --- Arquivos especiais (dispositivos, pipes, sockets) ------------------
    try:
        mode = entry.stat(follow_symlinks=False).st_mode
        kind = stat_module.filemode(mode)
    except OSError:
        kind = "desconhecido"
    yield ScanIssue(
        path=full_path,
        name=entry.name,
        error_type=ISSUE_TYPE_SPECIAL,
        message=f"Item ignorado por não ser um arquivo comum (modo: {kind})",
    )


def validate_source(source: str | os.PathLike[str]) -> tuple[bool, str]:
    """Valida a origem selecionada antes de iniciar a análise.

    Returns:
        ``(True, "")`` quando a origem é válida ou ``(False, motivo)``.
    """
    text = os.fspath(source).strip()
    if not text:
        return False, "Nenhuma pasta ou unidade foi selecionada."

    native = to_extended_path(text)
    try:
        if not os.path.exists(native):
            return False, f"A origem selecionada não existe ou está indisponível:\n{text}"
        if not os.path.isdir(native):
            return False, f"A origem selecionada não é uma pasta ou unidade:\n{text}"
        # Confirma que o diretório pode ser aberto para leitura.
        with os.scandir(native) as iterator:
            next(iterator, None)
    except OSError as exc:
        _, description = classify_os_error(exc)
        return False, f"Não foi possível acessar a origem selecionada:\n{text}\n\n{description}"
    return True, ""
