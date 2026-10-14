"""Núcleo da aplicação: varredura, cálculo de hash, armazenamento e orquestração."""

from __future__ import annotations

from .models import (
    APP_DESCRIPTION,
    APP_NAME,
    APP_VERSION,
    DEFAULT_CHUNK_SIZE,
    AnalysisConfig,
    AnalysisSummary,
    ProgressSnapshot,
)

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "APP_DESCRIPTION",
    "DEFAULT_CHUNK_SIZE",
    "AnalysisConfig",
    "AnalysisSummary",
    "ProgressSnapshot",
]
