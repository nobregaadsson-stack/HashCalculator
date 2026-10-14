"""Geração do relatório final em formato ``.xlsx`` (openpyxl).

O relatório contém quatro tipos de aba:

* **Arquivos** — inventário completo, uma linha por arquivo;
* **Resumo** — dados consolidados da análise;
* **Erros** — falhas de leitura e ocorrências da varredura;
* **Hashes Duplicados** — arquivos com hash idêntico (opcional).

A planilha é escrita em modo ``write_only``: as linhas são gravadas em fluxo,
sem manter todo o conteúdo em memória, o que permite gerar relatórios com
centenas de milhares de linhas.
"""

from __future__ import annotations

import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core.models import (
    APP_DESCRIPTION,
    APP_NAME,
    STATUS_ERROR_PREFIX,
    STATUS_OK,
    STATUS_SKIPPED_PREFIX,
    STATUS_WARNING_MODIFIED,
    AnalysisConfig,
    AnalysisSummary,
)
from core.storage import ResultStore, decode_hashes
from utils.formatter import (
    clean_cell,
    format_datetime,
    format_duration,
    format_int,
    format_size,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Limite de linhas de uma planilha do Excel (1.048.576, incluindo o cabeçalho).
EXCEL_MAX_ROWS = 1_048_576
MAX_DATA_ROWS_PER_SHEET = EXCEL_MAX_ROWS - 1

SHEET_FILES = "Arquivos"
SHEET_SUMMARY = "Resumo"
SHEET_ERRORS = "Erros"
SHEET_DUPLICATES = "Hashes Duplicados"

TEXT_FORMAT = "@"
INT_FORMAT = "#,##0"

# ---------------------------------------------------------------------------
# Estilos reutilizados (o openpyxl deduplica estilos idênticos no arquivo)
# ---------------------------------------------------------------------------
_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)

_TITLE_FONT = Font(name="Calibri", size=13, bold=True, color="1F4E78")
_LABEL_FONT = Font(name="Calibri", size=11, bold=True)
_MONO_FONT = Font(name="Consolas", size=10)

_STATUS_OK_FONT = Font(name="Calibri", size=11, color="1F6F3D")
_STATUS_ERROR_FONT = Font(name="Calibri", size=11, bold=True, color="B00020")
_STATUS_WARNING_FONT = Font(name="Calibri", size=11, bold=True, color="B35C00")
_STATUS_NEUTRAL_FONT = Font(name="Calibri", size=11, color="606060")

_GROUP_FILL = PatternFill("solid", fgColor="DDEBF7")

# Largura das colunas de hash por algoritmo (o SHA-512 tem 128 caracteres).
_HASH_WIDTHS = {"MD5": 36, "SHA-1": 44, "SHA-256": 68, "SHA-512": 80}


def _hash_columns(algorithms: Sequence[str]) -> list[str]:
    """Títulos das colunas de hash.

    Com um único algoritmo, o título é simplesmente ``Hash``. Com vários, cada
    algoritmo recebe a sua própria coluna (``Hash SHA-256``, ``Hash MD5``...).
    """
    if len(algorithms) == 1:
        return ["Hash"]
    return [f"Hash {name}" for name in algorithms]


def _styled(worksheet, value: object, *, font=None, fill=None, number_format=None, align=None):
    """Cria uma célula formatada compatível com o modo ``write_only``."""
    cell = WriteOnlyCell(worksheet, value=value)
    if font is not None:
        cell.font = font
    if fill is not None:
        cell.fill = fill
    if number_format is not None:
        cell.number_format = number_format
    if align is not None:
        cell.alignment = align
    return cell


def _header_row(worksheet, titles: Iterable[str]) -> list:
    """Monta a linha de cabeçalho com o estilo padrão."""
    return [
        _styled(worksheet, title, font=_HEADER_FONT, fill=_HEADER_FILL, align=_HEADER_ALIGN)
        for title in titles
    ]


def _apply_widths(worksheet, widths: Sequence[int]) -> None:
    """Define a largura das colunas (deve ser feito antes de gravar as linhas)."""
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width


def _status_font(status: str) -> Font:
    """Seleciona a cor do texto conforme o status do arquivo."""
    if status == STATUS_OK:
        return _STATUS_OK_FONT
    if status.startswith(STATUS_ERROR_PREFIX):
        return _STATUS_ERROR_FONT
    if status.startswith("ATENÇÃO"):
        return _STATUS_WARNING_FONT
    return _STATUS_NEUTRAL_FONT


# ---------------------------------------------------------------------------
# Aba "Arquivos"
# ---------------------------------------------------------------------------
def _write_files_sheets(workbook: Workbook, store: ResultStore, config: AnalysisConfig) -> int:
    """Grava o inventário de arquivos, dividindo em abas se exceder o limite.

    Returns:
        A quantidade de linhas de dados gravadas.
    """
    algorithms = list(config.algorithms)
    hash_titles = _hash_columns(algorithms)
    titles = [
        "Nº",
        "Nome do Arquivo",
        "Extensão",
        "Caminho Completo",
        "Caminho Relativo",
        "Tamanho (Bytes)",
        "Tamanho Formatado",
        "Algoritmo",
        *hash_titles,
        "Status",
    ]
    widths = [
        8, 42, 12, 78, 56, 18, 18, 18,
        *[_HASH_WIDTHS.get(name, 68) for name in algorithms],
        46,
    ]
    algorithms_text = config.algorithms_text

    sheet_index = 1
    worksheet = workbook.create_sheet(SHEET_FILES)
    _apply_widths(worksheet, widths)
    worksheet.freeze_panes = "A2"
    worksheet.append(_header_row(worksheet, titles))

    rows_in_sheet = 0
    total_rows = 0

    for row in store.iter_file_rows():
        (_id, name, ext, path, rel_path, size, status, hashes_json, _skip) = row
        status_text = status or ""
        digests = decode_hashes(hashes_json)

        if rows_in_sheet >= MAX_DATA_ROWS_PER_SHEET:
            # O Excel não aceita mais de 1.048.576 linhas por planilha.
            _finish_table(worksheet, len(titles), rows_in_sheet)
            sheet_index += 1
            worksheet = workbook.create_sheet(f"{SHEET_FILES} ({sheet_index})")
            _apply_widths(worksheet, widths)
            worksheet.freeze_panes = "A2"
            worksheet.append(_header_row(worksheet, titles))
            rows_in_sheet = 0

        total_rows += 1
        rows_in_sheet += 1

        cells: list = [
            total_rows,
            clean_cell(name),
            _styled(worksheet, clean_cell(ext), number_format=TEXT_FORMAT),
            _styled(worksheet, clean_cell(path), number_format=TEXT_FORMAT),
            _styled(worksheet, clean_cell(rel_path), number_format=TEXT_FORMAT),
            _styled(worksheet, int(size or 0), number_format=INT_FORMAT),
            format_size(size),
            algorithms_text,
        ]
        for algorithm in algorithms:
            cells.append(
                _styled(
                    worksheet,
                    digests.get(algorithm, ""),
                    font=_MONO_FONT,
                    number_format=TEXT_FORMAT,
                )
            )
        cells.append(_styled(worksheet, clean_cell(status_text), font=_status_font(status_text)))
        worksheet.append(cells)

    _finish_table(worksheet, len(titles), rows_in_sheet)
    return total_rows


def _finish_table(worksheet, column_count: int, data_rows: int) -> None:
    """Aplica o autofiltro à faixa efetivamente preenchida."""
    last_column = get_column_letter(column_count)
    worksheet.auto_filter.ref = f"A1:{last_column}{max(1, data_rows + 1)}"


# ---------------------------------------------------------------------------
# Aba "Resumo"
# ---------------------------------------------------------------------------
def _write_summary_sheet(
    workbook: Workbook,
    summary: AnalysisSummary,
    config: AnalysisConfig,
    report_path: Path,
) -> None:
    """Grava a aba com os dados consolidados da análise."""
    worksheet = workbook.create_sheet(SHEET_SUMMARY)
    _apply_widths(worksheet, [46, 96])
    worksheet.freeze_panes = "A2"
    worksheet.append(_header_row(worksheet, ["Item", "Valor"]))

    def section(title: str) -> None:
        worksheet.append([_styled(worksheet, title, font=_TITLE_FONT), ""])

    def line(label: str, value: object, *, mono: bool = False) -> None:
        worksheet.append(
            [
                _styled(worksheet, label, font=_LABEL_FONT),
                _styled(
                    worksheet,
                    clean_cell(value),
                    font=_MONO_FONT if mono else None,
                    number_format=TEXT_FORMAT,
                ),
            ]
        )

    section("IDENTIFICAÇÃO DA ANÁLISE")
    line("Aplicação:", f"{APP_NAME} — {APP_DESCRIPTION}")
    line("Versão da aplicação:", summary.app_version)
    line("Status da análise:", summary.status_text)
    line("Origem analisada:", summary.source, mono=True)
    line("Data/hora de início:", format_datetime(summary.started_at))
    line("Data/hora de término:", format_datetime(summary.finished_at))
    line("Tempo total:", format_duration(summary.elapsed_seconds))
    line("Algoritmo utilizado:", ", ".join(summary.algorithms))
    line("Bloco de leitura:", format_size(summary.chunk_size))

    section("RESULTADOS")
    line("Quantidade de arquivos encontrados:", format_int(summary.files_found))
    line("Quantidade de arquivos processados:", format_int(summary.files_processed))
    line("Arquivos com hash calculado (OK):", format_int(summary.files_ok))
    line("Quantidade de erros:", format_int(summary.total_errors))
    line("  • Erros no cálculo do hash:", format_int(summary.files_error))
    line("  • Ocorrências na varredura:", format_int(summary.scan_issues))
    line("Arquivos com aviso de alteração:", format_int(summary.files_warning))
    line("Arquivos ignorados (links/junctions):", format_int(summary.files_skipped))
    line("Arquivos não processados:", format_int(summary.files_not_processed))
    line("Diretórios percorridos:", format_int(summary.directories_scanned))

    section("VOLUME DE DADOS")
    line("Tamanho total encontrado:", format_size(summary.bytes_found))
    line("Tamanho total processado:", format_size(summary.bytes_processed))
    line("Tamanho total processado (bytes):", format_int(summary.bytes_processed))
    if summary.elapsed_seconds > 0:
        line(
            "Velocidade média:",
            f"{format_size(summary.bytes_processed / summary.elapsed_seconds)}/s",
        )

    section("ARQUIVOS DUPLICADOS")
    line("Detecção de duplicados:", "Ativada" if summary.duplicates_enabled else "Desativada")
    if summary.duplicates_enabled:
        line("Grupos de hashes duplicados:", format_int(summary.duplicate_groups))
        line("Arquivos com hash repetido:", format_int(summary.duplicate_files))

    section("AMBIENTE E ARQUIVOS GERADOS")
    line("Arquivo de relatório:", str(report_path), mono=True)
    line("Arquivo de log:", str(summary.log_path or ""), mono=True)
    line("Computador:", platform.node())
    line("Usuário do sistema:", os.environ.get("USERNAME") or os.environ.get("USER") or "")
    line("Sistema operacional:", f"{platform.system()} {platform.release()} ({platform.version()})")
    line("Python:", platform.python_version())
    line("Relatório gerado em:", format_datetime(datetime.now()))

    if summary.cancelled:
        worksheet.append(["", ""])
        worksheet.append(
            [
                _styled(
                    worksheet,
                    "STATUS DA ANÁLISE: CANCELADA PELO USUÁRIO",
                    font=_STATUS_ERROR_FONT,
                ),
                _styled(
                    worksheet,
                    "Os resultados abaixo referem-se apenas aos arquivos já processados.",
                    font=_STATUS_WARNING_FONT,
                ),
            ]
        )
    if summary.error_message:
        worksheet.append(
            [
                _styled(worksheet, "FALHA DURANTE A ANÁLISE:", font=_STATUS_ERROR_FONT),
                clean_cell(summary.error_message),
            ]
        )


# ---------------------------------------------------------------------------
# Aba "Erros"
# ---------------------------------------------------------------------------
def _write_errors_sheet(workbook: Workbook, store: ResultStore) -> int:
    """Grava a aba de erros e ocorrências. Devolve a quantidade de linhas."""
    worksheet = workbook.create_sheet(SHEET_ERRORS)
    titles = ["Nº", "Arquivo", "Caminho", "Tipo do erro", "Descrição"]
    _apply_widths(worksheet, [8, 42, 84, 36, 74])
    worksheet.freeze_panes = "A2"
    worksheet.append(_header_row(worksheet, titles))

    count = 0
    for name, path, error_type, message in store.iter_error_rows():
        count += 1
        if count > MAX_DATA_ROWS_PER_SHEET:
            break
        worksheet.append(
            [
                count,
                clean_cell(name),
                _styled(worksheet, clean_cell(path), number_format=TEXT_FORMAT),
                _styled(worksheet, clean_cell(error_type), font=_STATUS_ERROR_FONT),
                clean_cell(message),
            ]
        )

    if count == 0:
        worksheet.append(["", "Nenhum erro registrado nesta análise.", "", "", ""])
    _finish_table(worksheet, len(titles), count)
    return count


# ---------------------------------------------------------------------------
# Aba "Hashes Duplicados"
# ---------------------------------------------------------------------------
def _write_duplicates_sheet(
    workbook: Workbook, store: ResultStore, config: AnalysisConfig, summary: AnalysisSummary
) -> None:
    """Grava os grupos de arquivos que possuem hash idêntico."""
    worksheet = workbook.create_sheet(SHEET_DUPLICATES)
    titles = [
        "Grupo",
        f"Hash ({config.primary_algorithm})",
        "Qtd. no Grupo",
        "Nº no Grupo",
        "Nome do Arquivo",
        "Caminho Completo",
        "Caminho Relativo",
        "Tamanho (Bytes)",
        "Tamanho Formatado",
    ]
    widths = [10, _HASH_WIDTHS.get(config.primary_algorithm, 68), 14, 14, 40, 76, 54, 18, 18]
    _apply_widths(worksheet, widths)
    worksheet.freeze_panes = "A2"
    worksheet.append(_header_row(worksheet, titles))

    if not config.detect_duplicates:
        worksheet.append(
            ["", "A detecção de arquivos duplicados foi desativada pelo usuário.", "", "", "", "", "", "", ""]
        )
        _finish_table(worksheet, len(titles), 1)
        return

    current_hash: str | None = None
    group_number = 0
    position = 0
    rows = 0

    for hash_value, quantity, _total_size, name, path, rel_path, size in store.iter_duplicate_rows():
        if hash_value != current_hash:
            current_hash = hash_value
            group_number += 1
            position = 0
        position += 1
        rows += 1
        if rows > MAX_DATA_ROWS_PER_SHEET:
            break

        fill = _GROUP_FILL if group_number % 2 else None
        worksheet.append(
            [
                _styled(worksheet, group_number, fill=fill),
                _styled(worksheet, hash_value, font=_MONO_FONT, fill=fill, number_format=TEXT_FORMAT),
                _styled(worksheet, int(quantity), fill=fill),
                _styled(worksheet, position, fill=fill),
                _styled(worksheet, clean_cell(name), fill=fill),
                _styled(worksheet, clean_cell(path), fill=fill, number_format=TEXT_FORMAT),
                _styled(worksheet, clean_cell(rel_path), fill=fill, number_format=TEXT_FORMAT),
                _styled(worksheet, int(size or 0), fill=fill, number_format=INT_FORMAT),
                _styled(worksheet, format_size(size), fill=fill),
            ]
        )

    if rows == 0:
        message = (
            "Nenhum arquivo duplicado foi encontrado."
            if summary.files_processed
            else "Nenhum arquivo foi processado."
        )
        worksheet.append(["", message, "", "", "", "", "", "", ""])
        rows = 1
    _finish_table(worksheet, len(titles), rows)


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------
def resolve_output_path(output_path: str | os.PathLike[str]) -> Path:
    """Garante que o diretório de destino exista e devolve o caminho final."""
    path = Path(output_path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build_report(
    store: ResultStore,
    summary: AnalysisSummary,
    config: AnalysisConfig,
) -> Path:
    """Monta e grava o arquivo ``.xlsx`` com todos os resultados da análise.

    Returns:
        O caminho do relatório efetivamente gravado.
    """
    report_path = resolve_output_path(config.output_path)

    # Se o arquivo já existir (execuções no mesmo segundo), gera um nome livre.
    if report_path.exists():
        stem, suffix, parent = report_path.stem, report_path.suffix, report_path.parent
        counter = 2
        while report_path.exists() and counter < 1000:
            report_path = parent / f"{stem}_{counter}{suffix}"
            counter += 1

    workbook = Workbook(write_only=True)
    workbook.properties.creator = APP_NAME
    workbook.properties.title = "Relatório de hashes de arquivos"
    workbook.properties.description = APP_DESCRIPTION

    try:
        rows = _write_files_sheets(workbook, store, config)
        _write_summary_sheet(workbook, summary, config, report_path)
        errors = _write_errors_sheet(workbook, store)
        _write_duplicates_sheet(workbook, store, config, summary)
        workbook.save(report_path)
    finally:
        workbook.close()

    logger.info(
        "Relatório gravado (%d linha(s) de arquivos, %d de erros): %s",
        rows,
        errors,
        report_path,
    )
    return report_path
