"""Utilitários de caminho específicos do Windows.

Inclui suporte a caminhos longos (acima de 260 caracteres) por meio do prefixo
``\\\\?\\`` e a enumeração de unidades de disco disponíveis no sistema.
"""

from __future__ import annotations

import ctypes
import os
import string
import sys
from dataclasses import dataclass
from pathlib import Path

# Prefixo de caminho estendido do Windows (suporta até ~32.767 caracteres).
EXTENDED_PREFIX = "\\\\?\\"
UNC_EXTENDED_PREFIX = "\\\\?\\UNC\\"
DEVICE_PREFIX = "\\\\.\\"


def is_windows() -> bool:
    """Indica se a aplicação está sendo executada no Windows."""
    return os.name == "nt"


def to_extended_path(path: str | os.PathLike[str]) -> str:
    """Converte um caminho para a forma estendida do Windows.

    Necessário para acessar arquivos cujo caminho completo ultrapassa o limite
    histórico de 260 caracteres (``MAX_PATH``). Em sistemas não Windows o
    caminho é devolvido sem alterações.
    """
    text = os.fspath(path)
    if not is_windows() or not text:
        return text
    if text.startswith(EXTENDED_PREFIX) or text.startswith(DEVICE_PREFIX):
        return text
    try:
        # Caminhos estendidos não aceitam ".", ".." ou barras normais.
        absolute = os.path.abspath(text)
    except (OSError, ValueError):
        return text
    if absolute.startswith("\\\\"):
        return UNC_EXTENDED_PREFIX + absolute[2:]
    return EXTENDED_PREFIX + absolute


def from_extended_path(path: str) -> str:
    """Remove o prefixo de caminho estendido, devolvendo o caminho legível."""
    if path.startswith(UNC_EXTENDED_PREFIX):
        return "\\\\" + path[len(UNC_EXTENDED_PREFIX) :]
    if path.startswith(EXTENDED_PREFIX):
        return path[len(EXTENDED_PREFIX) :]
    return path


def app_dir() -> Path:
    """Diretório base da aplicação (funciona também no executável PyInstaller)."""
    if getattr(sys, "frozen", False):  # Executável gerado pelo PyInstaller.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_output_dir() -> Path:
    """Pasta sugerida para gravar os relatórios."""
    candidates = [
        Path.home() / "Documents" / "Relatorios",
        Path.home() / "Documentos" / "Relatorios",
        Path.home() / "Relatorios",
    ]
    for candidate in candidates:
        if candidate.parent.exists():
            return candidate
    return app_dir()


@dataclass(frozen=True, slots=True)
class DriveInfo:
    """Informações básicas de uma unidade de armazenamento."""

    root: str
    label: str
    drive_type: str

    def display(self) -> str:
        """Texto exibido na lista de unidades da interface gráfica."""
        if self.label:
            return f"{self.root}  ({self.label} - {self.drive_type})"
        return f"{self.root}  ({self.drive_type})"


_DRIVE_TYPES = {
    0: "Desconhecido",
    1: "Inválido",
    2: "Removível",
    3: "Disco local",
    4: "Rede",
    5: "CD-ROM",
    6: "Disco RAM",
}


def _volume_label(root: str) -> str:
    """Lê o rótulo do volume via API do Windows (silencioso em caso de falha)."""
    if not is_windows():
        return ""
    buffer = ctypes.create_unicode_buffer(261)
    fs_buffer = ctypes.create_unicode_buffer(261)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(root),
            buffer,
            ctypes.sizeof(buffer) // ctypes.sizeof(ctypes.c_wchar),
            None,
            None,
            None,
            fs_buffer,
            ctypes.sizeof(fs_buffer) // ctypes.sizeof(ctypes.c_wchar),
        )
    except OSError:
        return ""
    return buffer.value if ok else ""


def list_drives() -> list[DriveInfo]:
    """Lista as unidades de armazenamento reconhecidas pelo Windows.

    Inclui discos locais, HDs/SSDs externos, pendrives, cartões de memória e
    unidades de rede mapeadas.
    """
    drives: list[DriveInfo] = []
    if not is_windows():
        return [DriveInfo(root="/", label="", drive_type="Sistema de arquivos")]

    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    except OSError:
        mask = 0

    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        try:
            type_id = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))  # type: ignore[attr-defined]
        except OSError:
            type_id = 0
        if type_id == 1:  # DRIVE_NO_ROOT_DIR
            continue
        drives.append(
            DriveInfo(
                root=root,
                label=_volume_label(root),
                drive_type=_DRIVE_TYPES.get(type_id, "Desconhecido"),
            )
        )
    return drives


def normalize_source(path: str | os.PathLike[str]) -> str:
    """Normaliza a origem informada pelo usuário.

    Aceita ``E:``, ``E:/`` ou ``E:\\`` e devolve sempre a raiz no formato
    ``E:\\``. Demais caminhos são convertidos para o formato absoluto.
    """
    text = os.fspath(path).strip().strip('"')
    if not text:
        return ""
    if is_windows() and len(text) == 2 and text[1] == ":" and text[0].isalpha():
        return text.upper() + "\\"
    absolute = os.path.abspath(text)
    if is_windows() and len(absolute) == 2 and absolute[1] == ":":
        return absolute.upper() + "\\"
    return absolute
