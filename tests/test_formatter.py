"""Testes das funções de formatação e dos utilitários de caminho."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from utils.formatter import (
    build_report_filename,
    clean_cell,
    elide_middle,
    format_datetime,
    format_duration,
    format_int,
    format_percent,
    format_size,
    format_speed,
    strip_control_chars,
)
from utils.paths import normalize_source, to_extended_path


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1,00 KB"),
        (1_542_234, "1,47 MB"),
        (4_829_345, "4,61 MB"),
        (1024**3, "1,00 GB"),
        (1024**4, "1,00 TB"),
    ],
)
def test_format_size(valor: int, esperado: str) -> None:
    """Tamanhos são formatados em KB/MB/GB/TB com vírgula decimal."""
    assert format_size(valor) == esperado


def test_format_size_valores_invalidos() -> None:
    """Valores nulos ou inválidos não quebram a formatação."""
    assert format_size(None) == ""
    assert format_size("abc") == ""


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [(0, "00:00:00"), (59, "00:00:59"), (969, "00:16:09"), (6157, "01:42:37"), (-5, "00:00:00")],
)
def test_format_duration(segundos: int, esperado: str) -> None:
    """Durações são exibidas no formato HH:MM:SS."""
    assert format_duration(segundos) == esperado


def test_format_int() -> None:
    """Inteiros usam ponto como separador de milhar."""
    assert format_int(18742) == "18.742"
    assert format_int(0) == "0"
    assert format_int(1_000_000) == "1.000.000"


def test_format_datetime_e_speed() -> None:
    """Data/hora e velocidade seguem o padrão brasileiro."""
    assert format_datetime(datetime(2026, 7, 31, 14, 21, 35)) == "31/07/2026 14:21:35"
    assert format_datetime(None) == ""
    assert format_speed(0) == "0 B/s"
    assert format_speed(183 * 1024 * 1024) == "183,00 MB/s"


def test_format_percent() -> None:
    """Percentuais são limitados à faixa de 0 a 100."""
    assert format_percent(0.5) == "50,0%"
    assert format_percent(1.5) == "100,0%"
    assert format_percent(None) == "0,0%"


def test_build_report_filename() -> None:
    """O nome do relatório inclui data e hora para evitar sobrescrita."""
    nome = build_report_filename(when=datetime(2026, 7, 31, 14, 35, 20))
    assert nome == "Relatorio_Hashes_2026-07-31_143520.xlsx"


def test_limpeza_de_texto_para_excel() -> None:
    """Caracteres de controle são removidos e textos longos truncados."""
    assert strip_control_chars("a\x00b\x07c") == "abc"
    assert clean_cell(None) == ""
    assert len(clean_cell("x" * 40000)) == 32767


def test_elide_middle() -> None:
    """Caminhos longos são encurtados pelo meio para exibição."""
    texto = "C:\\" + "pasta\\" * 40 + "arquivo.txt"
    reduzido = elide_middle(texto, 50)
    assert len(reduzido) == 50
    assert "..." in reduzido
    assert elide_middle("curto", 50) == "curto"


@pytest.mark.skipif(os.name != "nt", reason="Comportamento específico do Windows.")
def test_caminho_estendido() -> None:
    """Caminhos absolutos recebem o prefixo de caminho longo do Windows."""
    assert to_extended_path("C:\\temp\\arquivo.txt") == "\\\\?\\C:\\temp\\arquivo.txt"
    assert to_extended_path("\\\\?\\C:\\ja_convertido") == "\\\\?\\C:\\ja_convertido"
    assert to_extended_path("\\\\servidor\\compartilhamento\\a.txt").startswith("\\\\?\\UNC\\")


@pytest.mark.skipif(os.name != "nt", reason="Comportamento específico do Windows.")
def test_normalize_source() -> None:
    """Unidades informadas como ``E:`` viram ``E:\\``."""
    assert normalize_source("e:") == "E:\\"
    assert normalize_source("E:\\") == "E:\\"
    assert normalize_source(' "C:\\Windows" ') == "C:\\Windows"
