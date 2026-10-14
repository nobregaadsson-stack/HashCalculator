"""Configuração comum dos testes (fixtures e ajuste do ``sys.path``)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Permite importar os pacotes da aplicação sem instalá-la.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def sample_tree(tmp_path: Path) -> Path:
    """Cria uma árvore de diretórios com nomes Unicode e arquivos duplicados.

    Estrutura::

        raiz/
        ├── vazio.txt                (0 bytes)
        ├── documentos/
        │   ├── relatório.txt        ("conteudo A")
        │   └── subpasta/
        │       └── acentuação çãõ.txt  ("conteudo A")   <- duplicado
        ├── imagens/
        │   └── foto 01.jpg          ("binario B")
        └── cópia/
            └── relatório.txt        ("conteudo A")      <- duplicado
    """
    root = tmp_path / "raiz"
    (root / "documentos" / "subpasta").mkdir(parents=True)
    (root / "imagens").mkdir()
    (root / "cópia").mkdir()

    (root / "vazio.txt").write_bytes(b"")
    (root / "documentos" / "relatório.txt").write_bytes(b"conteudo A")
    (root / "documentos" / "subpasta" / "acentuação çãõ.txt").write_bytes(b"conteudo A")
    (root / "imagens" / "foto 01.jpg").write_bytes(b"binario B")
    (root / "cópia" / "relatório.txt").write_bytes(b"conteudo A")
    return root
