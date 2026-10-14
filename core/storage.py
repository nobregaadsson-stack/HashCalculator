"""Armazenamento temporário dos resultados em banco SQLite.

Motivação: a aplicação precisa suportar unidades com milhões de arquivos. Manter
todos os resultados em memória antes de gerar a planilha consumiria vários
gigabytes de RAM. Por isso os registros são gravados em um banco SQLite
temporário (criado na pasta de arquivos temporários e removido ao final), e o
relatório é montado lendo esse banco em blocos.

O banco também viabiliza a detecção de hashes duplicados por meio de uma
agregação SQL, sem carregar todos os hashes na memória.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from utils.logger import get_logger

from .models import ScanIssue, ScannedFile

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    ext           TEXT    NOT NULL,
    rel_path      TEXT    NOT NULL,
    size          INTEGER NOT NULL,
    mtime_ns      INTEGER NOT NULL,
    is_link       INTEGER NOT NULL DEFAULT 0,
    skip_reason   TEXT,
    status        TEXT,
    error_type    TEXT,
    error_message TEXT,
    hashes        TEXT,
    primary_hash  TEXT,
    bytes_read    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_issues (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    name       TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    hash       TEXT PRIMARY KEY,
    quantidade INTEGER NOT NULL,
    total_size INTEGER NOT NULL
);
"""

# Quantidade de registros acumulados antes de gravar no banco.
_INSERT_BUFFER = 2000


class ResultStore:
    """Armazena, de forma incremental e em disco, os resultados da análise."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        if db_path is None:
            handle, temp_name = tempfile.mkstemp(prefix="hashcalc_", suffix=".db")
            os.close(handle)
            os.unlink(temp_name)  # O SQLite cria o arquivo novamente.
            db_path = temp_name
        self.db_path = Path(db_path)

        self._conn = sqlite3.connect(str(self.db_path))
        # Banco descartável: prioriza velocidade em vez de durabilidade.
        self._conn.execute("PRAGMA journal_mode = MEMORY")
        self._conn.execute("PRAGMA synchronous = OFF")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        self._file_buffer: list[tuple] = []
        self._issue_buffer: list[tuple] = []
        self._closed = False

    # ------------------------------------------------------------------
    # Escrita — fase de varredura
    # ------------------------------------------------------------------
    def add_file(self, item: ScannedFile) -> None:
        """Registra um arquivo localizado durante a varredura."""
        self._file_buffer.append(
            (
                item.path,
                item.name,
                item.ext,
                item.rel_path,
                item.size,
                item.mtime_ns,
                1 if item.is_link else 0,
                item.skip_reason,
            )
        )
        if len(self._file_buffer) >= _INSERT_BUFFER:
            self._flush_files()

    def add_issue(self, item: ScanIssue) -> None:
        """Registra um problema encontrado durante a varredura."""
        self._issue_buffer.append((item.path, item.name, item.error_type, item.message))
        if len(self._issue_buffer) >= _INSERT_BUFFER:
            self._flush_issues()

    def flush(self) -> None:
        """Grava no banco tudo o que estiver em buffer."""
        self._flush_files()
        self._flush_issues()

    def _flush_files(self) -> None:
        if not self._file_buffer:
            return
        self._conn.executemany(
            "INSERT INTO files (path, name, ext, rel_path, size, mtime_ns, is_link,"
            " skip_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            self._file_buffer,
        )
        self._conn.commit()
        self._file_buffer.clear()

    def _flush_issues(self) -> None:
        if not self._issue_buffer:
            return
        self._conn.executemany(
            "INSERT INTO scan_issues (path, name, error_type, message) VALUES (?, ?, ?, ?)",
            self._issue_buffer,
        )
        self._conn.commit()
        self._issue_buffer.clear()

    # ------------------------------------------------------------------
    # Escrita — fase de cálculo de hash
    # ------------------------------------------------------------------
    def iter_pending(self, batch_size: int = 200) -> Iterator[list[tuple]]:
        """Percorre os arquivos ainda não processados, em lotes.

        A leitura é feita por faixa de ``id`` (chave primária), o que mantém o
        consumo de memória constante mesmo com milhões de registros.
        """
        last_id = 0
        while True:
            rows = self._conn.execute(
                "SELECT id, path, name, size, mtime_ns, is_link, skip_reason"
                " FROM files WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, batch_size),
            ).fetchall()
            if not rows:
                return
            last_id = rows[-1][0]
            yield rows

    def save_results(self, results: Sequence[tuple]) -> None:
        """Grava os resultados de um lote de arquivos.

        Cada item deve seguir a ordem::

            (status, error_type, error_message, hashes_json, primary_hash,
             bytes_read, size_override, file_id)

        ``size_override`` corrige o tamanho obtido na varredura quando o sistema
        devolveu um valor desatualizado; ``None`` preserva o valor original.
        """
        if not results:
            return
        self._conn.executemany(
            "UPDATE files SET status = ?, error_type = ?, error_message = ?,"
            " hashes = ?, primary_hash = ?, bytes_read = ?,"
            " size = COALESCE(?, size) WHERE id = ?",
            results,
        )
        self._conn.commit()

    def mark_remaining_as_not_processed(self, status: str) -> int:
        """Marca os arquivos pendentes (análise cancelada) e devolve a quantidade."""
        cursor = self._conn.execute(
            "UPDATE files SET status = ? WHERE status IS NULL", (status,)
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------
    # Consultas agregadas
    # ------------------------------------------------------------------
    def count_files(self) -> int:
        """Quantidade total de arquivos localizados."""
        self.flush()
        return int(self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])

    def total_size(self) -> int:
        """Soma dos tamanhos de todos os arquivos localizados."""
        self.flush()
        value = self._conn.execute("SELECT COALESCE(SUM(size), 0) FROM files").fetchone()[0]
        return int(value or 0)

    def count_issues(self) -> int:
        """Quantidade de problemas registrados na varredura."""
        self.flush()
        return int(self._conn.execute("SELECT COUNT(*) FROM scan_issues").fetchone()[0])

    def count_by_status_prefix(self, prefix: str) -> int:
        """Conta arquivos cujo status inicia com o prefixo informado."""
        self.flush()
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM files WHERE status LIKE ?", (f"{prefix}%",)
            ).fetchone()[0]
        )

    def build_duplicate_index(self) -> tuple[int, int]:
        """Calcula os grupos de arquivos com hash idêntico.

        Returns:
            ``(quantidade de grupos, quantidade de arquivos duplicados)``.
        """
        self.flush()
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_primary_hash ON files(primary_hash)")
        self._conn.execute("DELETE FROM duplicate_groups")
        self._conn.execute(
            "INSERT INTO duplicate_groups (hash, quantidade, total_size)"
            " SELECT primary_hash, COUNT(*), SUM(size) FROM files"
            " WHERE primary_hash IS NOT NULL AND primary_hash <> ''"
            " GROUP BY primary_hash HAVING COUNT(*) > 1"
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(quantidade), 0) FROM duplicate_groups"
        ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    # ------------------------------------------------------------------
    # Leitura para o relatório
    # ------------------------------------------------------------------
    def iter_file_rows(self) -> Iterator[tuple]:
        """Percorre todos os arquivos na ordem de localização (streaming)."""
        self.flush()
        cursor = self._conn.execute(
            "SELECT id, name, ext, path, rel_path, size, status, hashes, skip_reason"
            " FROM files ORDER BY id"
        )
        cursor.arraysize = 500
        while True:
            rows = cursor.fetchmany()
            if not rows:
                return
            yield from rows

    def iter_error_rows(self) -> Iterator[tuple]:
        """Percorre erros de arquivos e de varredura para a aba ``Erros``."""
        self.flush()
        cursor = self._conn.execute(
            "SELECT name, path, error_type, error_message FROM files"
            " WHERE error_type IS NOT NULL AND error_type <> '' ORDER BY id"
        )
        cursor.arraysize = 500
        while True:
            rows = cursor.fetchmany()
            if not rows:
                break
            yield from rows

        cursor = self._conn.execute(
            "SELECT name, path, error_type, message FROM scan_issues ORDER BY id"
        )
        cursor.arraysize = 500
        while True:
            rows = cursor.fetchmany()
            if not rows:
                return
            yield from rows

    def iter_duplicate_rows(self) -> Iterator[tuple]:
        """Percorre os arquivos duplicados agrupados por hash.

        Yields tuplas ``(hash, quantidade, total_size, name, path, rel_path, size)``
        já ordenadas por grupo.
        """
        self.flush()
        cursor = self._conn.execute(
            "SELECT d.hash, d.quantidade, d.total_size, f.name, f.path, f.rel_path, f.size"
            " FROM duplicate_groups d JOIN files f ON f.primary_hash = d.hash"
            " ORDER BY d.total_size DESC, d.hash, f.path"
        )
        cursor.arraysize = 500
        while True:
            rows = cursor.fetchmany()
            if not rows:
                return
            yield from rows

    # ------------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------------
    def close(self, remove_file: bool = True) -> None:
        """Fecha a conexão e (opcionalmente) remove o banco temporário."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover - encerramento tolerante a falhas
            logger.debug("Falha ao fechar o banco temporário", exc_info=True)
        if remove_file:
            try:
                self.db_path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - arquivo pode estar em uso
                logger.debug("Não foi possível remover o banco temporário %s", self.db_path)

    def __enter__(self) -> "ResultStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def encode_hashes(digests: dict[str, str]) -> str:
    """Serializa o dicionário de hashes para gravação no banco."""
    return json.dumps(digests, ensure_ascii=False)


def decode_hashes(payload: str | None) -> dict[str, str]:
    """Recupera o dicionário de hashes gravado no banco."""
    if not payload:
        return {}
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def iter_chunks(items: Iterable[tuple], size: int) -> Iterator[list[tuple]]:
    """Agrupa um iterável em listas de tamanho fixo."""
    chunk: list[tuple] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
