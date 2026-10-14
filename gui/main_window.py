"""Janela principal da aplicação (PySide6).

A interface reúne a seleção da origem, dos algoritmos, do destino do relatório,
os controles de execução e o painel de progresso. Todo o processamento pesado é
delegado a :class:`gui.worker.AnalysisWorker`, executado em uma ``QThread``
separada — a interface nunca calcula hashes na thread principal.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.hasher import ALGORITHM_ORDER, DEFAULT_ALGORITHM
from core.models import (
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    DEFAULT_CHUNK_SIZE,
    STAGE_DONE,
    STAGE_HASHING,
    STAGE_REPORT,
    STAGE_SCANNING,
    AnalysisConfig,
    AnalysisSummary,
    ProgressSnapshot,
)
from core.scanner import validate_source
from utils.formatter import (
    build_report_filename,
    elide_middle,
    format_duration,
    format_int,
    format_size,
    format_speed,
)
from utils.logger import current_log_path, get_logger
from utils.paths import default_output_dir, list_drives, normalize_source

from .result_dialog import ResultDialog
from .worker import AnalysisWorker

logger = get_logger(__name__)

# Opções de tamanho de bloco de leitura oferecidas ao usuário.
CHUNK_OPTIONS: tuple[tuple[str, int], ...] = (
    ("1 MB", 1 * 1024 * 1024),
    ("4 MB (padrão)", 4 * 1024 * 1024),
    ("8 MB", 8 * 1024 * 1024),
    ("16 MB", 16 * 1024 * 1024),
    ("32 MB", 32 * 1024 * 1024),
)

_MAX_LOG_LINES = 400


class MainWindow(QMainWindow):
    """Janela principal do HashCalculator."""

    def __init__(self) -> None:
        super().__init__()
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None
        self._last_summary: AnalysisSummary | None = None
        self._cancel_requested = False

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} — {APP_DESCRIPTION}")
        self.setMinimumSize(940, 800)
        self.setWindowIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon) or QIcon()
        )

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_algorithms_group())
        layout.addWidget(self._build_output_group())
        layout.addWidget(self._build_controls())
        layout.addWidget(self._build_progress_group(), stretch=1)
        self.setCentralWidget(central)

        self._build_menu()
        self.statusBar().showMessage("Pronto. Selecione a origem e clique em INICIAR ANÁLISE.")
        self._update_report_preview()

    # ==================================================================
    # Construção da interface
    # ==================================================================
    def _build_source_group(self) -> QGroupBox:
        """Seção de seleção da pasta, unidade ou dispositivo de origem."""
        group = QGroupBox("1. Origem — pasta, unidade de disco ou dispositivo")
        grid = QGridLayout(group)

        grid.addWidget(QLabel("Pasta/Unidade:"), 0, 0)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(r"Exemplo: E:\  ou  C:\Users\Fulano\Documentos")
        self.source_edit.setClearButtonEnabled(True)
        grid.addWidget(self.source_edit, 0, 1)

        self.select_source_button = QPushButton("Selecionar...")
        self.select_source_button.clicked.connect(self._select_source)
        grid.addWidget(self.select_source_button, 0, 2)

        grid.addWidget(QLabel("Unidades detectadas:"), 1, 0)
        self.drives_combo = QComboBox()
        self.drives_combo.setMinimumWidth(320)
        self.drives_combo.currentIndexChanged.connect(self._on_drive_selected)
        grid.addWidget(self.drives_combo, 1, 1)

        self.refresh_drives_button = QPushButton("Atualizar")
        self.refresh_drives_button.setToolTip(
            "Relê as unidades conectadas (pendrives, HDs e SSDs externos, cartões)."
        )
        self.refresh_drives_button.clicked.connect(self._reload_drives)
        grid.addWidget(self.refresh_drives_button, 1, 2)

        grid.setColumnStretch(1, 1)
        self._reload_drives()
        return group

    def _build_algorithms_group(self) -> QGroupBox:
        """Seção de escolha dos algoritmos e das opções de processamento."""
        group = QGroupBox("2. Algoritmos e opções")
        outer = QVBoxLayout(group)

        algorithms_row = QHBoxLayout()
        algorithms_row.addWidget(QLabel("Algoritmo:"))
        self.algorithm_checkboxes: dict[str, QCheckBox] = {}
        # SHA-256 é o padrão; é possível marcar vários algoritmos ao mesmo tempo.
        for name in ("SHA-256", "SHA-512", "SHA-1", "MD5"):
            checkbox = QCheckBox(name)
            checkbox.setChecked(name == DEFAULT_ALGORITHM)
            self.algorithm_checkboxes[name] = checkbox
            algorithms_row.addWidget(checkbox)
        algorithms_row.addStretch(1)
        outer.addLayout(algorithms_row)

        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Bloco de leitura:"))
        self.chunk_combo = QComboBox()
        for label, value in CHUNK_OPTIONS:
            self.chunk_combo.addItem(label, value)
        self.chunk_combo.setCurrentIndex(
            next(i for i, (_, v) in enumerate(CHUNK_OPTIONS) if v == DEFAULT_CHUNK_SIZE)
        )
        self.chunk_combo.setToolTip(
            "Quantidade de dados lida por vez. Blocos maiores podem acelerar a\n"
            "leitura de arquivos muito grandes, consumindo um pouco mais de RAM."
        )
        options_row.addWidget(self.chunk_combo)
        options_row.addSpacing(20)

        self.duplicates_checkbox = QCheckBox("Identificar arquivos duplicados (mesmo hash)")
        self.duplicates_checkbox.setChecked(True)
        options_row.addWidget(self.duplicates_checkbox)
        options_row.addSpacing(20)

        self.follow_links_checkbox = QCheckBox("Seguir links simbólicos e junction points")
        self.follow_links_checkbox.setToolTip(
            "Desativado por padrão para evitar laços de diretórios e contagem duplicada."
        )
        options_row.addWidget(self.follow_links_checkbox)
        options_row.addStretch(1)
        outer.addLayout(options_row)
        return group

    def _build_output_group(self) -> QGroupBox:
        """Seção de escolha da pasta de destino do relatório."""
        group = QGroupBox("3. Destino do relatório")
        grid = QGridLayout(group)

        grid.addWidget(QLabel("Salvar relatório em:"), 0, 0)
        self.output_edit = QLineEdit(str(default_output_dir()))
        self.output_edit.setClearButtonEnabled(True)
        self.output_edit.textChanged.connect(self._update_report_preview)
        grid.addWidget(self.output_edit, 0, 1)

        self.select_output_button = QPushButton("Selecionar...")
        self.select_output_button.clicked.connect(self._select_output)
        grid.addWidget(self.select_output_button, 0, 2)

        grid.addWidget(QLabel("Arquivo gerado:"), 1, 0)
        self.report_preview_label = QLabel()
        self.report_preview_label.setStyleSheet("color: #555;")
        self.report_preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        grid.addWidget(self.report_preview_label, 1, 1, 1, 2)

        grid.setColumnStretch(1, 1)
        return group

    def _build_controls(self) -> QWidget:
        """Botões de controle da análise."""
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)

        self.start_button = QPushButton("INICIAR ANÁLISE")
        self.start_button.setMinimumHeight(42)
        self.start_button.setStyleSheet("font-weight: bold;")
        self.start_button.clicked.connect(self._start_analysis)

        self.cancel_button = QPushButton("CANCELAR")
        self.cancel_button.setMinimumHeight(42)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_analysis)

        self.open_report_button = QPushButton("ABRIR RELATÓRIO")
        self.open_report_button.setMinimumHeight(42)
        self.open_report_button.setEnabled(False)
        self.open_report_button.clicked.connect(self._open_report_file)

        for button in (self.start_button, self.cancel_button, self.open_report_button):
            row.addWidget(button)
        return container

    def _build_progress_group(self) -> QGroupBox:
        """Painel com o andamento detalhado da análise."""
        group = QGroupBox("4. Progresso da análise")
        layout = QVBoxLayout(group)

        grid = QGridLayout()
        self.stage_label = self._value_label("Aguardando início")
        self.found_label = self._value_label("0")
        self.processed_label = self._value_label("0")
        self.errors_label = self._value_label("0")
        self.warnings_label = self._value_label("0")
        self.bytes_label = self._value_label("0 B / 0 B")
        self.speed_label = self._value_label("0 B/s")
        self.elapsed_label = self._value_label("00:00:00")
        self.eta_label = self._value_label("--:--:--")

        fields = (
            ("Etapa:", self.stage_label),
            ("Arquivos encontrados:", self.found_label),
            ("Arquivos processados:", self.processed_label),
            ("Erros:", self.errors_label),
            ("Avisos:", self.warnings_label),
            ("Dados processados:", self.bytes_label),
            ("Velocidade:", self.speed_label),
            ("Tempo decorrido:", self.elapsed_label),
            ("Tempo restante (est.):", self.eta_label),
        )
        for index, (caption, widget) in enumerate(fields):
            row, column = divmod(index, 3)
            label = QLabel(caption)
            label.setStyleSheet("color: #444;")
            grid.addWidget(label, row, column * 2)
            grid.addWidget(widget, row, column * 2 + 1)
        for column in (1, 3, 5):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid)

        layout.addWidget(QLabel("Arquivo atual:"))
        self.current_file_label = QLabel("—")
        self.current_file_label.setFont(QFont("Consolas", 9))
        self.current_file_label.setStyleSheet("color: #1F4E78;")
        self.current_file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.current_file_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setMinimumHeight(26)
        layout.addWidget(self.progress_bar)

        layout.addWidget(QLabel("Ocorrências:"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(_MAX_LOG_LINES)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setPlaceholderText("As ocorrências da análise aparecerão aqui.")
        layout.addWidget(self.log_view, stretch=1)
        return group

    @staticmethod
    def _value_label(text: str) -> QLabel:
        """Cria um rótulo de valor (negrito) para o painel de progresso."""
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _build_menu(self) -> None:
        """Monta a barra de menus com ações auxiliares."""
        menu = self.menuBar().addMenu("&Arquivo")

        open_log = QAction("Abrir arquivo de &log", self)
        open_log.triggered.connect(self._open_log_file)
        menu.addAction(open_log)

        menu.addSeparator()
        quit_action = QAction("Sai&r", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("A&juda")
        about = QAction("&Sobre", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    # ==================================================================
    # Ações de seleção
    # ==================================================================
    def _reload_drives(self) -> None:
        """Recarrega a lista de unidades conectadas."""
        self.drives_combo.blockSignals(True)
        self.drives_combo.clear()
        self.drives_combo.addItem("— selecione uma unidade —", "")
        for drive in list_drives():
            self.drives_combo.addItem(drive.display(), drive.root)
        self.drives_combo.blockSignals(False)

    @Slot(int)
    def _on_drive_selected(self, index: int) -> None:
        """Preenche o campo de origem com a unidade escolhida."""
        root = self.drives_combo.itemData(index)
        if root:
            self.source_edit.setText(root)

    @Slot()
    def _select_source(self) -> None:
        """Abre o seletor de pastas (permite escolher a raiz de uma unidade)."""
        start = self.source_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Selecione a pasta, unidade ou dispositivo a analisar",
            start,
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
        )
        if chosen:
            self.source_edit.setText(normalize_source(chosen))

    @Slot()
    def _select_output(self) -> None:
        """Abre o seletor da pasta de destino do relatório."""
        start = self.output_edit.text().strip() or str(default_output_dir())
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Selecione a pasta onde o relatório será salvo",
            start,
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self.output_edit.setText(str(Path(chosen)))

    def _update_report_preview(self) -> None:
        """Mostra o nome do arquivo que será gerado (com data e hora)."""
        folder = self.output_edit.text().strip() or str(default_output_dir())
        self.report_preview_label.setText(
            str(Path(folder) / build_report_filename())
            + "   (a data e a hora são atualizadas no início da análise)"
        )

    # ==================================================================
    # Execução da análise
    # ==================================================================
    def _selected_algorithms(self) -> list[str]:
        """Algoritmos marcados, na ordem canônica."""
        return [name for name in ALGORITHM_ORDER if self.algorithm_checkboxes[name].isChecked()]

    def _collect_config(self) -> AnalysisConfig | None:
        """Valida os campos da interface e monta a configuração da análise."""
        source = normalize_source(self.source_edit.text())
        valid, message = validate_source(source)
        if not valid:
            QMessageBox.warning(self, "Origem inválida", message)
            return None

        algorithms = self._selected_algorithms()
        if not algorithms:
            QMessageBox.warning(
                self,
                "Nenhum algoritmo selecionado",
                "Selecione ao menos um algoritmo de hash para iniciar a análise.",
            )
            return None

        folder = Path(self.output_edit.text().strip() or str(default_output_dir()))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".hashcalculator_write_test_{os.getpid()}"
            probe.touch()
            probe.unlink()
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Destino inválido",
                f"Não foi possível gravar na pasta de destino:\n{folder}\n\n{exc}",
            )
            return None

        return AnalysisConfig(
            source=source,
            algorithms=algorithms,
            output_path=folder / build_report_filename(),
            chunk_size=int(self.chunk_combo.currentData()),
            detect_duplicates=self.duplicates_checkbox.isChecked(),
            follow_links=self.follow_links_checkbox.isChecked(),
        )

    @Slot()
    def _start_analysis(self) -> None:
        """Inicia a análise em uma thread dedicada."""
        if self._thread is not None:
            return

        config = self._collect_config()
        if config is None:
            return

        self._cancel_requested = False
        self._last_summary = None
        self.log_view.clear()
        self._append_log(f"Origem: {config.source}")
        self._append_log(f"Algoritmos: {config.algorithms_text}")
        self._append_log(f"Relatório: {config.output_path}")
        self._set_running(True)
        self.progress_bar.setValue(0)

        self._thread = QThread(self)
        self._worker = AnalysisWorker(config)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.stageChanged.connect(self._on_stage)
        self._worker.progressChanged.connect(self._on_progress)
        self._worker.logMessage.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    @Slot()
    def _cancel_analysis(self) -> None:
        """Pede confirmação e solicita o cancelamento seguro."""
        if self._worker is None:
            return
        answer = QMessageBox.question(
            self,
            "Cancelar análise",
            "Deseja realmente interromper a análise?\n\n"
            "Os resultados já obtidos serão gravados no relatório e a análise será\n"
            "registrada como CANCELADA PELO USUÁRIO.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._cancel_requested = True
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("CANCELANDO...")
        self.statusBar().showMessage("Cancelando: finalizando o arquivo atual e gravando o relatório...")
        self._append_log("Cancelamento solicitado pelo usuário.")
        self._worker.cancel()

    def _set_running(self, running: bool) -> None:
        """Habilita/desabilita os controles conforme o estado da análise."""
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.cancel_button.setText("CANCELAR")
        for widget in (
            self.source_edit,
            self.select_source_button,
            self.drives_combo,
            self.refresh_drives_button,
            self.output_edit,
            self.select_output_button,
            self.chunk_combo,
            self.duplicates_checkbox,
            self.follow_links_checkbox,
            *self.algorithm_checkboxes.values(),
        ):
            widget.setEnabled(not running)

    # ==================================================================
    # Sinais do worker
    # ==================================================================
    @Slot(str)
    def _on_stage(self, stage: str) -> None:
        """Atualiza a etapa atual exibida na interface."""
        self.stage_label.setText(stage)
        self.statusBar().showMessage(f"{stage}...")
        if stage == STAGE_SCANNING:
            self.progress_bar.setRange(0, 0)  # Barra indeterminada durante a varredura.
        elif stage in (STAGE_HASHING, STAGE_REPORT, STAGE_DONE):
            self.progress_bar.setRange(0, 1000)

    @Slot(object)
    def _on_progress(self, snapshot: ProgressSnapshot) -> None:
        """Atualiza os contadores e a barra de progresso."""
        self.found_label.setText(format_int(snapshot.files_found))
        self.processed_label.setText(format_int(snapshot.files_processed))
        self.errors_label.setText(format_int(snapshot.files_error))
        self.warnings_label.setText(format_int(snapshot.files_warning))
        self.bytes_label.setText(
            f"{format_size(snapshot.bytes_done)} / {format_size(snapshot.bytes_total)}"
        )
        self.speed_label.setText(format_speed(snapshot.speed_bps))
        self.elapsed_label.setText(format_duration(snapshot.elapsed_seconds))
        self.eta_label.setText(
            format_duration(snapshot.eta_seconds) if snapshot.eta_seconds is not None else "--:--:--"
        )
        self.current_file_label.setText(elide_middle(snapshot.current_file or "—", 110))
        if snapshot.stage != STAGE_SCANNING:
            self.progress_bar.setValue(int(snapshot.fraction * 1000))

    @Slot(str)
    def _append_log(self, message: str) -> None:
        """Acrescenta uma linha ao painel de ocorrências."""
        self.log_view.appendPlainText(message)

    @Slot(object)
    def _on_finished(self, summary: AnalysisSummary) -> None:
        """Trata o término da análise e apresenta o resultado."""
        self._last_summary = summary
        self._teardown_thread()
        self._set_running(False)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000 if not summary.cancelled else self.progress_bar.value())
        self.stage_label.setText(STAGE_DONE)
        self.open_report_button.setEnabled(bool(summary.report_path))
        self.statusBar().showMessage(
            f"Análise {summary.status_text.lower()} — relatório: {summary.report_path}"
        )

        dialog = ResultDialog(summary, self)
        dialog.open_file_button.clicked.connect(self._open_report_file)
        dialog.open_folder_button.clicked.connect(self._open_report_folder)
        if dialog.exec() == ResultDialog.NEW_ANALYSIS:
            self._reset_for_new_analysis()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        """Trata falhas inesperadas ocorridas na thread de análise."""
        self._teardown_thread()
        self._set_running(False)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.stage_label.setText("Falha")
        self._append_log(f"FALHA: {message}")
        QMessageBox.critical(
            self,
            "Falha na análise",
            f"Ocorreu um erro inesperado durante a análise:\n\n{message}\n\n"
            f"Consulte o arquivo de log para mais detalhes:\n{current_log_path()}",
        )

    def _teardown_thread(self) -> None:
        """Encerra a thread de trabalho de forma ordenada."""
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10_000)
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def _reset_for_new_analysis(self) -> None:
        """Prepara a interface para uma nova análise."""
        self.progress_bar.setValue(0)
        self.log_view.clear()
        self.current_file_label.setText("—")
        for label in (self.found_label, self.processed_label, self.errors_label, self.warnings_label):
            label.setText("0")
        self.bytes_label.setText("0 B / 0 B")
        self.speed_label.setText("0 B/s")
        self.elapsed_label.setText("00:00:00")
        self.eta_label.setText("--:--:--")
        self.stage_label.setText("Aguardando início")
        self._update_report_preview()
        self.statusBar().showMessage("Pronto para uma nova análise.")

    # ==================================================================
    # Abertura de arquivos gerados
    # ==================================================================
    def _open_path(self, path: Path, select: bool = False) -> None:
        """Abre no Windows apenas arquivos **gerados pela aplicação**.

        Por segurança, nenhum arquivo analisado é executado ou aberto pelo
        aplicativo associado — somente o relatório e a pasta que o contém.
        """
        try:
            if select and os.name == "nt":
                subprocess.Popen(["explorer", f"/select,{path}"])  # noqa: S603,S607
                return
            if hasattr(os, "startfile"):
                os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
            else:  # pragma: no cover - caminho não utilizado no Windows
                subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
        except OSError as exc:
            QMessageBox.warning(
                self, "Não foi possível abrir", f"Falha ao abrir:\n{path}\n\n{exc}"
            )

    @Slot()
    def _open_report_file(self) -> None:
        """Abre a planilha gerada no aplicativo padrão do sistema."""
        if self._last_summary is None or not self._last_summary.report_path:
            return
        path = Path(self._last_summary.report_path)
        if not path.exists():
            QMessageBox.warning(self, "Relatório não encontrado", f"O arquivo não existe:\n{path}")
            return
        self._open_path(path)

    @Slot()
    def _open_report_folder(self) -> None:
        """Abre o Explorer na pasta do relatório, com o arquivo selecionado."""
        if self._last_summary is None or not self._last_summary.report_path:
            return
        path = Path(self._last_summary.report_path)
        if path.exists():
            self._open_path(path, select=True)
        else:
            self._open_path(path.parent)

    @Slot()
    def _open_log_file(self) -> None:
        """Abre o arquivo de log da aplicação."""
        log_path = current_log_path()
        if log_path.exists():
            self._open_path(log_path)
        else:
            QMessageBox.information(
                self, "Log", f"O arquivo de log ainda não foi criado:\n{log_path}"
            )

    @Slot()
    def _show_about(self) -> None:
        """Exibe informações sobre a aplicação."""
        QMessageBox.about(
            self,
            f"Sobre o {APP_NAME}",
            f"<b>{APP_NAME} {APP_VERSION}</b><br>{APP_DESCRIPTION}<br><br>"
            "Calcula MD5, SHA-1, SHA-256 e SHA-512 de todos os arquivos de uma pasta, "
            "unidade ou dispositivo e gera um relatório completo em Excel.<br><br>"
            "Os arquivos analisados são abertos <b>somente para leitura</b> e nunca são "
            "executados, movidos, renomeados ou alterados.<br><br>"
            f"Log da aplicação:<br><code>{current_log_path()}</code>",
        )

    # ==================================================================
    # Encerramento
    # ==================================================================
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (assinatura do Qt)
        """Impede o fechamento acidental durante uma análise em andamento."""
        if self._thread is not None and self._thread.isRunning():
            answer = QMessageBox.question(
                self,
                "Análise em andamento",
                "Uma análise está em execução.\n\nDeseja realmente interromper e sair?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self._worker is not None:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(15_000)
        logger.info("Aplicação encerrada pelo usuário.")
        QApplication.processEvents()
        event.accept()
