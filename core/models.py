"""Modelos de dados e constantes compartilhadas pela aplicação."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

APP_NAME = "HashCalculator"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = "Cálculo de hash de arquivos com geração de relatório em Excel"

# Tamanho padrão do bloco de leitura (4 MB), configurável pelo usuário.
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
MIN_CHUNK_SIZE = 64 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024

# Quantidade de arquivos processados entre gravações no armazenamento interno.
DEFAULT_BATCH_SIZE = 200

# ---------------------------------------------------------------------------
# Textos de status utilizados na planilha
# ---------------------------------------------------------------------------
STATUS_OK = "OK"
STATUS_ERROR_PREFIX = "ERRO - "
STATUS_WARNING_MODIFIED = "ATENÇÃO - Arquivo possivelmente alterado durante o cálculo do hash"
STATUS_SKIPPED_PREFIX = "IGNORADO - "
STATUS_NOT_PROCESSED = "NÃO PROCESSADO - Análise cancelada pelo usuário"

ANALYSIS_STATUS_COMPLETED = "CONCLUÍDA"
ANALYSIS_STATUS_CANCELLED = "CANCELADA PELO USUÁRIO"

# Etapas da análise (exibidas na interface gráfica).
STAGE_SCANNING = "Localizando arquivos"
STAGE_HASHING = "Calculando hashes"
STAGE_REPORT = "Gerando relatório"
STAGE_DONE = "Concluído"


@dataclass(slots=True)
class AnalysisConfig:
    """Parâmetros de uma análise solicitada pelo usuário."""

    source: str
    algorithms: list[str]
    output_path: Path
    chunk_size: int = DEFAULT_CHUNK_SIZE
    detect_duplicates: bool = True
    follow_links: bool = False
    batch_size: int = DEFAULT_BATCH_SIZE

    @property
    def primary_algorithm(self) -> str:
        """Algoritmo principal, usado na detecção de duplicados."""
        return self.algorithms[0] if self.algorithms else ""

    @property
    def algorithms_text(self) -> str:
        """Algoritmos selecionados em formato legível."""
        return ", ".join(self.algorithms)


@dataclass(slots=True)
class ScannedFile:
    """Arquivo localizado durante a varredura, antes do cálculo do hash."""

    path: str
    name: str
    ext: str
    rel_path: str
    size: int
    mtime_ns: int
    is_link: bool = False
    skip_reason: str | None = None


@dataclass(slots=True)
class ScanIssue:
    """Problema (ou aviso) encontrado durante a varredura de diretórios."""

    path: str
    name: str
    error_type: str
    message: str


@dataclass(slots=True)
class HashOutcome:
    """Resultado do cálculo de hash de um único arquivo."""

    digests: dict[str, str] = field(default_factory=dict)
    bytes_read: int = 0
    #: Tamanho informado pelo sistema no momento da abertura do arquivo. É mais
    #: confiável do que o tamanho obtido na varredura, pois o Windows pode
    #: devolver um valor desatualizado para arquivos com gravação em andamento.
    size_at_open: int = 0
    cancelled: bool = False
    modified: bool = False
    error_type: str | None = None
    error_message: str | None = None

    @property
    def failed(self) -> bool:
        """Indica se ocorreu erro durante o processamento."""
        return self.error_type is not None

    def status_text(self) -> str:
        """Texto que será gravado na coluna ``Status`` da planilha."""
        if self.failed:
            return f"{STATUS_ERROR_PREFIX}{self.error_type}"
        if self.cancelled:
            return STATUS_NOT_PROCESSED
        if self.modified:
            return STATUS_WARNING_MODIFIED
        return STATUS_OK


@dataclass(slots=True)
class ProgressSnapshot:
    """Fotografia do andamento da análise, enviada à interface gráfica."""

    stage: str = STAGE_SCANNING
    files_found: int = 0
    files_processed: int = 0
    files_ok: int = 0
    files_error: int = 0
    files_warning: int = 0
    files_skipped: int = 0
    current_file: str = ""
    bytes_total: int = 0
    bytes_done: int = 0
    elapsed_seconds: float = 0.0
    speed_bps: float = 0.0
    eta_seconds: float | None = None

    @property
    def fraction(self) -> float:
        """Progresso entre 0.0 e 1.0, calculado preferencialmente por bytes."""
        if self.bytes_total > 0:
            return min(1.0, self.bytes_done / self.bytes_total)
        if self.files_found > 0:
            return min(1.0, self.files_processed / self.files_found)
        return 0.0


@dataclass(slots=True)
class AnalysisSummary:
    """Resumo consolidado da análise, gravado na aba ``Resumo``."""

    source: str = ""
    algorithms: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float = 0.0
    files_found: int = 0
    files_processed: int = 0
    files_ok: int = 0
    files_error: int = 0
    files_warning: int = 0
    files_skipped: int = 0
    files_not_processed: int = 0
    directories_scanned: int = 0
    scan_issues: int = 0
    bytes_found: int = 0
    bytes_processed: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    duplicates_enabled: bool = True
    chunk_size: int = DEFAULT_CHUNK_SIZE
    cancelled: bool = False
    report_path: Path | None = None
    log_path: Path | None = None
    app_version: str = APP_VERSION
    error_message: str | None = None

    @property
    def status_text(self) -> str:
        """Situação final da análise."""
        return ANALYSIS_STATUS_CANCELLED if self.cancelled else ANALYSIS_STATUS_COMPLETED

    @property
    def total_errors(self) -> int:
        """Total de erros (arquivos com falha + falhas de varredura)."""
        return self.files_error + self.scan_issues
