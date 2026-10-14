"""Worker Qt que executa a análise fora da thread da interface gráfica.

O processamento roda em uma :class:`QThread` própria. A comunicação com a
janela principal é feita exclusivamente por sinais, garantindo que a interface
permaneça responsiva mesmo durante o cálculo de hashes de arquivos enormes.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from core.engine import AnalysisEngine, EngineCallbacks
from core.models import AnalysisConfig, AnalysisSummary, ProgressSnapshot
from utils.logger import get_logger

logger = get_logger(__name__)


class AnalysisWorker(QObject):
    """Executa :class:`AnalysisEngine` emitindo sinais de progresso."""

    stageChanged = Signal(str)
    progressChanged = Signal(object)  # ProgressSnapshot
    logMessage = Signal(str)
    finished = Signal(object)  # AnalysisSummary
    failed = Signal(str)

    def __init__(self, config: AnalysisConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._engine = AnalysisEngine(
            config,
            EngineCallbacks(
                on_stage=self._emit_stage,
                on_progress=self._emit_progress,
                on_log=self._emit_log,
            ),
        )

    # ------------------------------------------------------------------
    # Callbacks do motor de análise (executados na thread do worker)
    # ------------------------------------------------------------------
    def _emit_stage(self, stage: str) -> None:
        self.stageChanged.emit(stage)

    def _emit_progress(self, snapshot: ProgressSnapshot) -> None:
        self.progressChanged.emit(snapshot)

    def _emit_log(self, message: str) -> None:
        self.logMessage.emit(message)

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------
    @Slot()
    def run(self) -> None:
        """Ponto de entrada da thread: executa a análise completa."""
        try:
            summary: AnalysisSummary = self._engine.run()
        except Exception as exc:  # pragma: no cover - proteção da thread
            logger.exception("Falha na execução da análise")
            self.failed.emit(str(exc))
            return
        self.finished.emit(summary)

    @Slot()
    def cancel(self) -> None:
        """Solicita o cancelamento seguro (pode ser chamado de outra thread)."""
        self._engine.cancel()

    @property
    def cancelled(self) -> bool:
        """Indica se o cancelamento já foi solicitado."""
        return self._engine.cancelled
