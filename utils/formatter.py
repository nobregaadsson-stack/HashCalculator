"""Funções de formatação de valores no padrão brasileiro (pt-BR).

Este módulo não depende de nenhuma biblioteca externa e é utilizado tanto pela
interface gráfica quanto pelo gerador de relatórios em Excel.
"""

from __future__ import annotations

import re
from datetime import datetime

# Unidades binárias (base 1024) usadas na formatação de tamanhos.
_BINARY_UNITS: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB", "PB", "EB")

# Caracteres de controle que o formato XLSX (XML) não aceita em células.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Limite máximo de caracteres de uma célula do Excel.
EXCEL_MAX_CELL_LENGTH = 32767


def format_size(num_bytes: int | float | None, decimals: int = 2) -> str:
    """Converte uma quantidade de bytes em texto legível (ex.: ``1,47 MB``).

    A conversão usa base binária (1 KB = 1024 bytes) e separador decimal
    vírgula, conforme o padrão brasileiro.
    """
    if num_bytes is None:
        return ""
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return ""

    negative = value < 0
    value = abs(value)
    index = 0
    while value >= 1024.0 and index < len(_BINARY_UNITS) - 1:
        value /= 1024.0
        index += 1

    if index == 0:
        text = f"{int(value)} {_BINARY_UNITS[index]}"
    else:
        text = f"{value:.{decimals}f}".replace(".", ",") + f" {_BINARY_UNITS[index]}"
    return f"-{text}" if negative else text


def format_int(value: int | float | None) -> str:
    """Formata um número inteiro com ponto como separador de milhar."""
    if value is None:
        return ""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return ""


def format_duration(seconds: float | None) -> str:
    """Formata uma duração em segundos como ``HH:MM:SS``."""
    if seconds is None:
        return "00:00:00"
    try:
        total = int(max(0.0, float(seconds)))
    except (TypeError, ValueError):
        return "00:00:00"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_datetime(value: datetime | None) -> str:
    """Formata data/hora como ``dd/mm/aaaa HH:MM:SS``."""
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y %H:%M:%S")


def format_speed(bytes_per_second: float | None) -> str:
    """Formata uma velocidade de leitura (ex.: ``183,00 MB/s``)."""
    if not bytes_per_second or bytes_per_second <= 0:
        return "0 B/s"
    return f"{format_size(bytes_per_second)}/s"


def format_percent(fraction: float | None, decimals: int = 1) -> str:
    """Formata uma fração (0.0 a 1.0) como percentual pt-BR."""
    if fraction is None:
        return "0,0%"
    value = max(0.0, min(1.0, float(fraction))) * 100.0
    return f"{value:.{decimals}f}".replace(".", ",") + "%"


def build_report_filename(prefix: str = "Relatorio_Hashes", when: datetime | None = None) -> str:
    """Gera o nome do relatório com data/hora, evitando sobrescrita acidental.

    Exemplo: ``Relatorio_Hashes_2026-07-31_143520.xlsx``.
    """
    moment = when or datetime.now()
    return f"{prefix}_{moment:%Y-%m-%d_%H%M%S}.xlsx"


def strip_control_chars(text: str) -> str:
    """Remove caracteres de controle inválidos para o formato XLSX."""
    return _CONTROL_CHARS_RE.sub("", text)


def clean_cell(value: object) -> str:
    """Prepara um valor textual para ser gravado em uma célula do Excel."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = strip_control_chars(text)
    if len(text) > EXCEL_MAX_CELL_LENGTH:
        text = text[: EXCEL_MAX_CELL_LENGTH - 3] + "..."
    return text


def elide_middle(text: str, max_length: int = 90) -> str:
    """Encurta um texto longo pelo meio, preservando início e fim.

    Utilizado para exibir caminhos extensos na interface gráfica.
    """
    if max_length <= 5 or len(text) <= max_length:
        return text
    head = (max_length - 3) // 2
    tail = max_length - 3 - head
    return f"{text[:head]}...{text[-tail:]}"
