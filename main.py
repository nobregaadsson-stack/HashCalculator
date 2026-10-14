"""HashCalculator — ponto de entrada da aplicação.

Uso:
    python main.py            # abre a interface gráfica
    python main.py --version  # exibe a versão e encerra

O cálculo dos hashes é sempre executado em uma thread separada; esta função
apenas inicializa o log e o laço de eventos da interface.
"""

from __future__ import annotations

import argparse
import faulthandler
import sys
import traceback
from pathlib import Path
from types import TracebackType

# Garante que os pacotes internos sejam encontrados também quando a aplicação
# é executada a partir de um executável gerado pelo PyInstaller.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.models import APP_DESCRIPTION, APP_NAME, APP_VERSION  # noqa: E402
from utils.logger import get_logger, setup_logging  # noqa: E402

logger = get_logger("main")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Interpreta os argumentos de linha de comando."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{APP_NAME} — {APP_DESCRIPTION}",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
    )
    parser.add_argument(
        "--log",
        metavar="ARQUIVO",
        help="Caminho alternativo para o arquivo de log.",
    )
    return parser.parse_args(argv)


def _install_excepthook() -> None:
    """Registra no log qualquer exceção não tratada da aplicação."""

    def handler(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Exceção não tratada:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )

    sys.excepthook = handler


def main(argv: list[str] | None = None) -> int:
    """Inicializa o log e abre a janela principal."""
    args = _parse_args(argv)
    log_path = setup_logging(Path(args.log) if args.log else None)
    _install_excepthook()

    # Facilita o diagnóstico de travamentos graves (gera stack trace no log).
    try:
        faulthandler.enable()
    except (AttributeError, RuntimeError, ValueError):  # pragma: no cover
        pass

    logger.info("Iniciando %s %s | Log: %s", APP_NAME, APP_VERSION, log_path)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - dependência ausente
        message = (
            "A biblioteca PySide6 não está instalada.\n\n"
            "Instale as dependências com:\n"
            "    pip install -r requirements.txt"
        )
        logger.critical(message)
        print(message, file=sys.stderr)
        return 2

    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
