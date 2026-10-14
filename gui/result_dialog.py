"""Janela de resultado exibida ao término da análise."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.models import AnalysisSummary
from utils.formatter import format_duration, format_int, format_size


def build_result_text(summary: AnalysisSummary) -> str:
    """Monta o texto do resumo final apresentado ao usuário."""
    title = "ANÁLISE CONCLUÍDA" if not summary.cancelled else "ANÁLISE CANCELADA PELO USUÁRIO"
    lines = [
        title,
        "",
        f"Arquivos encontrados: {format_int(summary.files_found)}",
        f"Arquivos processados: {format_int(summary.files_processed)}",
        f"Erros: {format_int(summary.total_errors)}",
    ]
    if summary.files_warning:
        lines.append(f"Avisos de alteração durante o cálculo: {format_int(summary.files_warning)}")
    if summary.files_skipped:
        lines.append(f"Arquivos ignorados (links/junctions): {format_int(summary.files_skipped)}")
    if summary.cancelled and summary.files_not_processed:
        lines.append(f"Arquivos não processados: {format_int(summary.files_not_processed)}")
    lines.extend(
        [
            f"Dados processados: {format_size(summary.bytes_processed)}",
            f"Tempo total: {format_duration(summary.elapsed_seconds)}",
        ]
    )
    if summary.duplicates_enabled:
        lines.append(
            f"Hashes duplicados: {format_int(summary.duplicate_groups)} grupo(s), "
            f"{format_int(summary.duplicate_files)} arquivo(s)"
        )
    lines.extend(["", "Relatório:", str(summary.report_path or "(não gerado)")])
    return "\n".join(lines)


class ResultDialog(QDialog):
    """Diálogo final com as ações previstas na especificação."""

    #: Resultado indicando que o usuário deseja iniciar uma nova análise.
    NEW_ANALYSIS = 2

    def __init__(self, summary: AnalysisSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary = summary
        self.setWindowTitle("Resultado da análise")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)

        heading = QLabel(
            "ANÁLISE CONCLUÍDA" if not summary.cancelled else "ANÁLISE CANCELADA PELO USUÁRIO"
        )
        heading_font = QFont()
        heading_font.setPointSize(13)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading.setStyleSheet("color: #1F6F3D;" if not summary.cancelled else "color: #B35C00;")
        layout.addWidget(heading)

        details = QTextEdit()
        details.setReadOnly(True)
        details.setFont(QFont("Consolas", 10))
        details.setPlainText(build_result_text(summary))
        details.setMinimumHeight(240)
        layout.addWidget(details)

        buttons = QHBoxLayout()
        self.open_file_button = QPushButton("ABRIR PLANILHA")
        self.open_folder_button = QPushButton("ABRIR PASTA")
        self.new_analysis_button = QPushButton("NOVA ANÁLISE")
        self.close_button = QPushButton("FECHAR")
        self.close_button.setToolTip("Fecha esta janela e mantém a aplicação aberta.")

        report_exists = bool(summary.report_path and Path(summary.report_path).exists())
        self.open_file_button.setEnabled(report_exists)
        self.open_folder_button.setEnabled(report_exists)

        for button in (
            self.open_file_button,
            self.open_folder_button,
            self.new_analysis_button,
            self.close_button,
        ):
            button.setMinimumHeight(34)
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.new_analysis_button.clicked.connect(lambda: self.done(self.NEW_ANALYSIS))
        self.close_button.clicked.connect(self.reject)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        # Botão auxiliar invisível para permitir fechar com a tecla Esc.
        self._box = QDialogButtonBox()
        self._box.hide()
