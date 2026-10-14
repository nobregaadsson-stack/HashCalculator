"""Testes da interface gráfica (executados em modo ``offscreen``).

Estes testes verificam o estado inicial da janela, a formatação do painel de
progresso e — o ponto mais crítico — que os sinais emitidos pela thread de
trabalho são entregues **na thread da interface**. Atualizar widgets a partir de
outra thread provoca falha de acesso à memória no Qt.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

# Deve ser definido antes de qualquer importação do Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 não está instalado.")

from PySide6.QtCore import QObject, Slot  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.models import STAGE_HASHING, ProgressSnapshot  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.result_dialog import build_result_text  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    """Instância única de ``QApplication`` compartilhada pelos testes."""
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture()
def window(app: QApplication) -> MainWindow:
    """Janela principal criada e destruída a cada teste."""
    janela = MainWindow()
    yield janela
    janela.close()
    janela.deleteLater()
    app.processEvents()


def test_estado_inicial(window: MainWindow) -> None:
    """SHA-256 vem marcado e os botões iniciam no estado correto."""
    assert window.algorithm_checkboxes["SHA-256"].isChecked()
    assert not window.algorithm_checkboxes["MD5"].isChecked()
    assert not window.algorithm_checkboxes["SHA-1"].isChecked()
    assert not window.algorithm_checkboxes["SHA-512"].isChecked()
    assert window._selected_algorithms() == ["SHA-256"]

    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert not window.open_report_button.isEnabled()
    assert window.duplicates_checkbox.isChecked()
    assert not window.follow_links_checkbox.isChecked()


def test_selecao_de_multiplos_algoritmos(window: MainWindow) -> None:
    """A seleção múltipla respeita a ordem canônica."""
    window.algorithm_checkboxes["MD5"].setChecked(True)
    window.algorithm_checkboxes["SHA-512"].setChecked(True)
    assert window._selected_algorithms() == ["MD5", "SHA-256", "SHA-512"]


def test_painel_de_progresso(window: MainWindow) -> None:
    """Os contadores são exibidos formatados no padrão brasileiro."""
    snapshot = ProgressSnapshot(
        stage=STAGE_HASHING,
        files_found=34725,
        files_processed=12821,
        files_error=2,
        current_file=r"E:\Videos\video123.mp4",
        bytes_total=604_000_000_000,
        bytes_done=136_800_000_000,
        elapsed_seconds=764.0,
        speed_bps=191_000_000,
    )
    window._on_stage(STAGE_HASHING)
    window._on_progress(snapshot)

    assert window.found_label.text() == "34.725"
    assert window.processed_label.text() == "12.821"
    assert window.errors_label.text() == "2"
    assert window.elapsed_label.text() == "00:12:44"
    assert "GB /" in window.bytes_label.text()
    assert window.progress_bar.value() == int(snapshot.fraction * 1000)
    assert "video123.mp4" in window.current_file_label.text()


def test_bloqueio_de_controles_durante_a_analise(window: MainWindow) -> None:
    """Os campos de configuração ficam desabilitados durante o processamento."""
    window._set_running(True)
    assert not window.start_button.isEnabled()
    assert window.cancel_button.isEnabled()
    assert not window.source_edit.isEnabled()
    assert not window.algorithm_checkboxes["SHA-256"].isEnabled()

    window._set_running(False)
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.source_edit.isEnabled()


def test_origem_invalida_nao_inicia_analise(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uma origem inexistente é recusada antes de iniciar a thread."""
    avisos: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.QMessageBox.warning",
        lambda *args, **kwargs: avisos.append(args[2] if len(args) > 2 else ""),
    )
    window.source_edit.setText(str(tmp_path / "pasta_que_nao_existe"))
    window.output_edit.setText(str(tmp_path))

    assert window._collect_config() is None
    assert avisos and "não existe" in avisos[0]
    assert window._thread is None


def test_nenhum_algoritmo_selecionado(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """É obrigatório selecionar ao menos um algoritmo."""
    avisos: list[str] = []
    monkeypatch.setattr(
        "gui.main_window.QMessageBox.warning",
        lambda *args, **kwargs: avisos.append(args[1] if len(args) > 1 else ""),
    )
    window.algorithm_checkboxes["SHA-256"].setChecked(False)
    window.source_edit.setText(str(tmp_path))
    window.output_edit.setText(str(tmp_path))

    assert window._collect_config() is None
    assert avisos and "algoritmo" in avisos[0].lower()


class _Observador(QObject):
    """Recebe os mesmos sinais do worker para verificar a thread de execução."""

    def __init__(self) -> None:
        super().__init__()
        self.threads: set[int] = set()
        self.progressos = 0
        self.resumo = None
        self.concluido = False

    @Slot(object)
    def on_progress(self, _snapshot: object) -> None:
        self.progressos += 1
        self.threads.add(threading.get_ident())

    @Slot(object)
    def on_finished(self, resumo: object) -> None:
        self.threads.add(threading.get_ident())
        self.resumo = resumo
        self.concluido = True


def test_analise_completa_pela_interface(
    app: QApplication, window: MainWindow, sample_tree: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executa uma análise real pela GUI e valida a segurança entre threads."""
    # Evita o diálogo modal de resultado.
    monkeypatch.setattr("gui.main_window.ResultDialog.exec", lambda self: 0)

    destino = tmp_path / "saida"
    window.source_edit.setText(str(sample_tree))
    window.output_edit.setText(str(destino))

    observador = _Observador()
    window._start_analysis()
    assert window._thread is not None and window._thread.isRunning()
    assert not window.start_button.isEnabled()

    window._worker.progressChanged.connect(observador.on_progress)
    window._worker.finished.connect(observador.on_finished)

    limite = time.monotonic() + 120
    while not observador.concluido and time.monotonic() < limite:
        app.processEvents()
        time.sleep(0.005)

    assert observador.concluido, "A análise não terminou dentro do tempo previsto."
    # Os handlers precisam executar na thread da interface (segurança do Qt).
    assert observador.threads == {threading.get_ident()}

    resumo = observador.resumo
    assert resumo.files_found == 5
    assert resumo.files_processed == 5
    assert resumo.report_path is not None and Path(resumo.report_path).exists()

    # A interface volta ao estado inicial após a conclusão.
    app.processEvents()
    assert window.start_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert window.open_report_button.isEnabled()
    assert window._thread is None and window._worker is None
    assert "ANÁLISE CONCLUÍDA" in build_result_text(resumo)


def test_cancelamento_pela_interface(
    app: QApplication, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O botão CANCELAR interrompe a análise e grava os resultados parciais."""
    from PySide6.QtWidgets import QMessageBox

    origem = tmp_path / "origem"
    origem.mkdir()
    for indice in range(60):
        (origem / f"arquivo_{indice}.bin").write_bytes(os.urandom(1024) * 200)  # 200 KB

    monkeypatch.setattr("gui.main_window.ResultDialog.exec", lambda self: 0)
    monkeypatch.setattr(
        "gui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    window.source_edit.setText(str(origem))
    window.output_edit.setText(str(tmp_path / "saida"))

    observador = _Observador()
    window._start_analysis()
    window._worker.finished.connect(observador.on_finished)

    # Cancela assim que a fase de cálculo começar.
    inicio = time.monotonic()
    while time.monotonic() - inicio < 0.15:
        app.processEvents()
        time.sleep(0.005)
    window._cancel_analysis()

    limite = time.monotonic() + 120
    while not observador.concluido and time.monotonic() < limite:
        app.processEvents()
        time.sleep(0.005)

    resumo = observador.resumo
    assert resumo is not None and resumo.cancelled
    assert resumo.files_found == 60
    assert Path(resumo.report_path).exists()
    app.processEvents()
    assert window.start_button.isEnabled()
    assert window._thread is None
