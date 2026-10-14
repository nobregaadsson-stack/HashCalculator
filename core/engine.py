"""Orquestração da análise: varredura, cálculo de hash e geração do relatório.

Este módulo é totalmente independente da interface gráfica (não importa Qt),
o que permite testá-lo isoladamente e reaproveitá-lo em outros contextos.
A comunicação com a camada de apresentação é feita por *callbacks*.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from reports.excel_report import build_report
from utils.logger import current_log_path, get_logger

from .hasher import hash_file, normalize_algorithms
from .models import (
    STAGE_DONE,
    STAGE_HASHING,
    STAGE_REPORT,
    STAGE_SCANNING,
    STATUS_ERROR_PREFIX,
    STATUS_NOT_PROCESSED,
    STATUS_OK,
    STATUS_SKIPPED_PREFIX,
    STATUS_WARNING_MODIFIED,
    AnalysisConfig,
    AnalysisSummary,
    ProgressSnapshot,
    ScanIssue,
    ScannedFile,
)
from .scanner import scan_directory
from .storage import ResultStore, encode_hashes

logger = get_logger(__name__)

# Intervalo mínimo entre atualizações de progresso enviadas à interface.
_PROGRESS_INTERVAL = 0.12
# Janela usada no cálculo da velocidade instantânea de leitura.
_SPEED_WINDOW_SECONDS = 5.0


@dataclass(slots=True)
class EngineCallbacks:
    """Callbacks opcionais acionados durante a análise."""

    on_stage: Callable[[str], None] | None = None
    on_progress: Callable[[ProgressSnapshot], None] | None = None
    on_log: Callable[[str], None] | None = None

    def stage(self, value: str) -> None:
        if self.on_stage is not None:
            self.on_stage(value)

    def progress(self, snapshot: ProgressSnapshot) -> None:
        if self.on_progress is not None:
            self.on_progress(snapshot)

    def log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)


class AnalysisEngine:
    """Executa uma análise completa de forma cancelável."""

    def __init__(
        self,
        config: AnalysisConfig,
        callbacks: EngineCallbacks | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self.config.algorithms = normalize_algorithms(config.algorithms)
        self.callbacks = callbacks or EngineCallbacks()
        self._db_path = db_path
        self._cancel = threading.Event()

        self._stage = STAGE_SCANNING
        self._files_found = 0
        self._files_processed = 0
        self._files_ok = 0
        self._files_error = 0
        self._files_warning = 0
        self._files_skipped = 0
        self._directories = 0
        self._bytes_total = 0
        self._bytes_done = 0
        self._current_file = ""
        self._started_monotonic = 0.0
        self._last_emit = 0.0
        self._samples: deque[tuple[float, int]] = deque()

    # ------------------------------------------------------------------
    # Controle
    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Solicita o cancelamento seguro da análise."""
        if not self._cancel.is_set():
            logger.info("Cancelamento solicitado pelo usuário.")
            self._cancel.set()

    @property
    def cancelled(self) -> bool:
        """Indica se o cancelamento foi solicitado."""
        return self._cancel.is_set()

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------
    def run(self) -> AnalysisSummary:
        """Executa a análise completa e devolve o resumo consolidado."""
        config = self.config
        started_at = datetime.now()
        self._started_monotonic = time.monotonic()

        logger.info("=" * 78)
        logger.info("Início da análise | Origem: %s", config.source)
        logger.info("Algoritmos: %s | Bloco de leitura: %d bytes",
                    config.algorithms_text, config.chunk_size)

        summary = AnalysisSummary(
            source=config.source,
            algorithms=list(config.algorithms),
            started_at=started_at,
            chunk_size=config.chunk_size,
            duplicates_enabled=config.detect_duplicates,
            log_path=current_log_path(),
        )

        store = ResultStore(self._db_path)
        try:
            self._scan_phase(store)
            self._hash_phase(store)
            self._finalize_counters(store, summary)
            self._report_phase(store, summary)
        except Exception as exc:  # pragma: no cover - proteção de última linha
            logger.exception("Falha inesperada durante a análise: %s", exc)
            summary.error_message = str(exc)
            self.callbacks.log(f"ERRO INESPERADO: {exc}")
        finally:
            store.close()

        summary.finished_at = datetime.now()
        summary.elapsed_seconds = time.monotonic() - self._started_monotonic
        summary.cancelled = self._cancel.is_set()

        self._stage = STAGE_DONE
        self.callbacks.stage(STAGE_DONE)
        self._emit_progress(force=True)

        logger.info(
            "Análise %s | Encontrados: %d | Processados: %d | Erros: %d | Avisos: %d",
            summary.status_text,
            summary.files_found,
            summary.files_processed,
            summary.total_errors,
            summary.files_warning,
        )
        logger.info("Relatório: %s", summary.report_path)
        logger.info("=" * 78)
        return summary

    # ------------------------------------------------------------------
    # Fase 1 — varredura
    # ------------------------------------------------------------------
    def _scan_phase(self, store: ResultStore) -> None:
        """Localiza todos os arquivos e registra-os no armazenamento interno."""
        self._stage = STAGE_SCANNING
        self.callbacks.stage(STAGE_SCANNING)
        self.callbacks.log(f"Varrendo: {self.config.source}")

        def on_directory(_path: str) -> None:
            self._directories += 1

        for item in scan_directory(
            self.config.source,
            follow_links=self.config.follow_links,
            cancel_event=self._cancel,
            on_directory=on_directory,
        ):
            if isinstance(item, ScannedFile):
                store.add_file(item)
                self._files_found += 1
                self._bytes_total += item.size
                self._current_file = item.path
            elif isinstance(item, ScanIssue):
                store.add_issue(item)
                logger.warning("Varredura: %s | %s | %s", item.error_type, item.path, item.message)
                self.callbacks.log(f"{item.error_type}: {item.path}")
            self._emit_progress()

        store.flush()
        logger.info(
            "Varredura concluída: %d arquivos, %d diretórios, %d bytes",
            self._files_found,
            self._directories,
            self._bytes_total,
        )
        self.callbacks.log(
            f"Varredura concluída: {self._files_found} arquivo(s) em {self._directories} diretório(s)."
        )
        self._emit_progress(force=True)

    # ------------------------------------------------------------------
    # Fase 2 — cálculo dos hashes
    # ------------------------------------------------------------------
    def _hash_phase(self, store: ResultStore) -> None:
        """Calcula os hashes de todos os arquivos localizados."""
        if self._cancel.is_set():
            return

        self._stage = STAGE_HASHING
        self.callbacks.stage(STAGE_HASHING)

        config = self.config
        primary = config.primary_algorithm

        for batch in store.iter_pending(config.batch_size):
            updates: list[tuple] = []
            for row in batch:
                file_id, path, _name, _size, _mtime, _is_link, skip_reason = row

                if self._cancel.is_set():
                    break

                # Links não seguidos são inventariados, mas não têm hash calculado.
                if skip_reason:
                    self._files_skipped += 1
                    self._files_processed += 1
                    updates.append(
                        (
                            f"{STATUS_SKIPPED_PREFIX}{skip_reason}",
                            None,
                            None,
                            None,
                            None,
                            0,
                            None,
                            file_id,
                        )
                    )
                    continue

                self._current_file = path
                self._emit_progress()

                outcome = hash_file(
                    path,
                    config.algorithms,
                    chunk_size=config.chunk_size,
                    cancel_event=self._cancel,
                    on_chunk=self._on_chunk,
                )

                if outcome.cancelled:
                    break

                status = outcome.status_text()
                if outcome.failed:
                    self._files_error += 1
                    logger.warning("Erro ao processar %s | %s", path, outcome.error_message)
                    self.callbacks.log(f"{outcome.error_type}: {path}")
                elif outcome.modified:
                    self._files_warning += 1
                    logger.warning("Arquivo alterado durante o cálculo: %s", path)
                    self.callbacks.log(f"Arquivo alterado durante o cálculo: {path}")
                else:
                    self._files_ok += 1

                self._files_processed += 1
                updates.append(
                    (
                        status,
                        outcome.error_type,
                        outcome.error_message,
                        encode_hashes(outcome.digests) if outcome.digests else None,
                        outcome.digests.get(primary) if outcome.digests else None,
                        outcome.bytes_read,
                        # Corrige o tamanho quando a varredura devolveu um valor
                        # desatualizado (arquivo com gravação em andamento).
                        outcome.size_at_open or None,
                        file_id,
                    )
                )

            store.save_results(updates)
            self._emit_progress(force=True)
            if self._cancel.is_set():
                break

        if self._cancel.is_set():
            pending = store.mark_remaining_as_not_processed(STATUS_NOT_PROCESSED)
            logger.info("Análise cancelada: %d arquivo(s) não processado(s).", pending)
            self.callbacks.log(f"Análise cancelada. {pending} arquivo(s) não processado(s).")

    def _on_chunk(self, size: int) -> None:
        """Contabiliza os bytes lidos e atualiza o progresso periodicamente."""
        self._bytes_done += size
        self._emit_progress()

    # ------------------------------------------------------------------
    # Fase 3 — consolidação e relatório
    # ------------------------------------------------------------------
    def _finalize_counters(self, store: ResultStore, summary: AnalysisSummary) -> None:
        """Consolida os contadores finais a partir do armazenamento."""
        store.flush()
        summary.files_found = store.count_files()
        summary.files_processed = self._files_processed
        summary.files_ok = self._files_ok
        summary.files_error = self._files_error
        summary.files_warning = self._files_warning
        summary.files_skipped = self._files_skipped
        summary.files_not_processed = store.count_by_status_prefix("NÃO PROCESSADO")
        summary.directories_scanned = self._directories
        summary.scan_issues = store.count_issues()
        summary.bytes_found = store.total_size()
        summary.bytes_processed = self._bytes_done

        if self.config.detect_duplicates:
            groups, files = store.build_duplicate_index()
            summary.duplicate_groups = groups
            summary.duplicate_files = files
            logger.info("Duplicados: %d grupo(s), %d arquivo(s).", groups, files)

    def _report_phase(self, store: ResultStore, summary: AnalysisSummary) -> None:
        """Gera a planilha Excel com todos os resultados obtidos."""
        self._stage = STAGE_REPORT
        self.callbacks.stage(STAGE_REPORT)
        self.callbacks.log("Gerando relatório...")

        summary.cancelled = self._cancel.is_set()
        summary.finished_at = datetime.now()
        summary.elapsed_seconds = time.monotonic() - self._started_monotonic

        report_path = build_report(store, summary, self.config)
        summary.report_path = report_path
        logger.info("Relatório gerado: %s", report_path)
        self.callbacks.log(f"Relatório gerado: {report_path}")

    # ------------------------------------------------------------------
    # Progresso
    # ------------------------------------------------------------------
    def _emit_progress(self, force: bool = False) -> None:
        """Envia o progresso à interface, respeitando o intervalo mínimo."""
        now = time.monotonic()
        if not force and (now - self._last_emit) < _PROGRESS_INTERVAL:
            return
        self._last_emit = now

        elapsed = now - self._started_monotonic
        speed = self._current_speed(now)
        remaining = max(0, self._bytes_total - self._bytes_done)
        eta = remaining / speed if speed > 0 and self._stage == STAGE_HASHING else None

        self.callbacks.progress(
            ProgressSnapshot(
                stage=self._stage,
                files_found=self._files_found,
                files_processed=self._files_processed,
                files_ok=self._files_ok,
                files_error=self._files_error,
                files_warning=self._files_warning,
                files_skipped=self._files_skipped,
                current_file=self._current_file,
                bytes_total=self._bytes_total,
                bytes_done=self._bytes_done,
                elapsed_seconds=elapsed,
                speed_bps=speed,
                eta_seconds=eta,
            )
        )

    def _current_speed(self, now: float) -> float:
        """Velocidade de leitura calculada sobre uma janela móvel de tempo."""
        self._samples.append((now, self._bytes_done))
        while len(self._samples) > 2 and (now - self._samples[0][0]) > _SPEED_WINDOW_SECONDS:
            self._samples.popleft()

        if len(self._samples) < 2:
            return 0.0
        start_time, start_bytes = self._samples[0]
        interval = now - start_time
        if interval <= 0.0:
            return 0.0
        return max(0.0, (self._bytes_done - start_bytes) / interval)


def run_analysis(
    config: AnalysisConfig,
    callbacks: EngineCallbacks | None = None,
) -> AnalysisSummary:
    """Atalho para executar uma análise completa (usado em testes e scripts)."""
    return AnalysisEngine(config, callbacks).run()


__all__ = [
    "AnalysisEngine",
    "EngineCallbacks",
    "run_analysis",
    "STATUS_OK",
    "STATUS_ERROR_PREFIX",
    "STATUS_WARNING_MODIFIED",
]
