"""Configuração do log da aplicação.

O log registra o ciclo de vida da análise (início, origem, erros, cancelamento,
geração do relatório e conclusão). Por decisão de projeto, **nunca** é gravado
o conteúdo interno dos arquivos analisados — apenas metadados e resultados.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import app_dir

LOG_FILENAME = "hash_calculator.log"
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s"
_DATE_FORMAT = "%d/%m/%Y %H:%M:%S"

_configured = False
_active_log_path: Path | None = None


def default_log_path() -> Path:
    """Caminho padrão do arquivo de log (ao lado da aplicação/executável)."""
    return app_dir() / LOG_FILENAME


def current_log_path() -> Path:
    """Caminho do log realmente em uso nesta execução."""
    return _active_log_path or default_log_path()


def setup_logging(log_path: Path | None = None, level: int = logging.INFO) -> Path:
    """Configura o log em arquivo (com rotação) e no console.

    Caso a pasta da aplicação não permita gravação (por exemplo, executável em
    ``C:\\Program Files``), o log é redirecionado para a pasta do usuário.

    Returns:
        O caminho efetivamente utilizado para o arquivo de log.
    """
    global _configured, _active_log_path

    target = Path(log_path) if log_path else default_log_path()
    root = logging.getLogger()
    root.setLevel(level)

    if _configured:
        return current_log_path()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler: logging.Handler | None = None
    for candidate in (target, Path.home() / LOG_FILENAME):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                candidate,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            target = candidate
            break
        except OSError:
            continue

    if file_handler is not None:
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    # O console só existe quando a aplicação não foi compilada com --noconsole.
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(level)
        root.addHandler(stream_handler)

    _configured = True
    _active_log_path = target
    return target


def get_logger(name: str) -> logging.Logger:
    """Devolve um logger nomeado para um módulo da aplicação."""
    return logging.getLogger(name)
