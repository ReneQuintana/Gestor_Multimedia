"""
db/repository.py
Responsabilidad única: todas las queries SQL de negocio.

NO sabe cómo calcular hashes.
NO sabe cómo recorrer carpetas.
Solo habla con la base de datos.

Patrón Repository: centraliza el acceso a datos en un solo lugar.
Si en el futuro cambias SQLite por DuckDB o PostgreSQL,
solo tocas este archivo.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from db.database import Database

logger = logging.getLogger(__name__)


class MediaRepository:
    """
    CRUD y queries para la tabla media_files.

    Recibe una instancia de Database ya conectada.
    Ejemplo de uso:

        with Database() as db:
            repo = MediaRepository(db)
            repo.upsert(media_file, hash_value, hash_type)
            duplicates = repo.get_duplicates()
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------

    def upsert(
        self,
        path: str,
        filename: str,
        ext: str,
        size_bytes: int,
        mtime: float,
        hash_value: str | None,
        hash_type: str | None,
        is_duplicate: bool,
        duplicate_of: str | None,
        year: int,
        media_type: str,
        is_hd: int | None = None,
        error: str | None = None,
    ) -> None:
        """
        Inserta o actualiza un archivo en el índice.
        INSERT OR REPLACE garantiza idempotencia — se puede llamar
        múltiples veces para el mismo path sin duplicar registros.
        """
        assert self.db.connection is not None
        now = datetime.now().isoformat()

        with self.db.connection:
            self.db.connection.execute("""
                INSERT OR REPLACE INTO media_files (
                    path, filename, ext, size_bytes, mtime,
                    hash, hash_type,
                    is_duplicate, duplicate_of,
                    year, media_type, is_hd,
                    indexed_at, error
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
            """, (
                path, filename, ext, size_bytes, mtime,
                hash_value, hash_type,
                1 if is_duplicate else 0, duplicate_of,
                year, media_type, is_hd,
                now, error,
            ))

    def mark_as_duplicate(self, path: str, original_path: str) -> None:
        """Marca un archivo existente como duplicado de otro."""
        assert self.db.connection is not None
        with self.db.connection:
            self.db.connection.execute("""
                UPDATE media_files
                SET is_duplicate = 1, duplicate_of = ?
                WHERE path = ?
            """, (original_path, path))

    def delete_by_path(self, path: str) -> None:
        """Elimina un registro del índice (no borra el archivo físico)."""
        assert self.db.connection is not None
        with self.db.connection:
            self.db.connection.execute(
                "DELETE FROM media_files WHERE path = ?", (path,)
            )

    def delete_missing_files(self) -> int:
        """
        Elimina del índice los archivos que ya no existen en disco.
        Útil para limpiar el índice después de borrar duplicados.
        Retorna cantidad de registros eliminados.
        """
        import os
        assert self.db.connection is not None

        rows = self.db.connection.execute(
            "SELECT path FROM media_files"
        ).fetchall()

        deleted = 0
        for row in rows:
            if not os.path.exists(row["path"]):
                self.delete_by_path(row["path"])
                deleted += 1
                logger.debug(f"Eliminado del índice (no existe en disco): {row['path']}")

        if deleted:
            logger.info(f"Limpieza del índice: {deleted} registros eliminados.")

        return deleted

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        """
        Busca un archivo por su ruta absoluta.
        Retorna dict con todos los campos o None si no existe.
        Usado por el indexador para saber si ya está indexado.
        """
        assert self.db.connection is not None
        row = self.db.connection.execute(
            "SELECT * FROM media_files WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_hash(self, hash_value: str) -> list[dict[str, Any]]:
        """
        Retorna todos los archivos con el mismo hash.
        Si hay más de uno → son duplicados.
        """
        assert self.db.connection is not None
        rows = self.db.connection.execute(
            "SELECT * FROM media_files WHERE hash = ?", (hash_value,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_duplicates(self) -> list[dict[str, Any]]:
        """
        Retorna todos los archivos marcados como duplicados,
        junto con la ruta de su original.
        Usado por la UI para mostrar la lista de duplicados.
        """
        assert self.db.connection is not None
        rows = self.db.connection.execute("""
            SELECT
                dup.path        AS dup_path,
                dup.filename    AS dup_filename,
                dup.size_bytes  AS dup_size,
                orig.path       AS orig_path,
                orig.filename   AS orig_filename,
                orig.size_bytes AS orig_size
            FROM media_files dup
            JOIN media_files orig ON dup.duplicate_of = orig.path
            WHERE dup.is_duplicate = 1
            ORDER BY orig.path, dup.path
        """).fetchall()
        return [dict(r) for r in rows]

    def get_all_hashes(self) -> dict[str, str]:
        """
        Retorna {hash: path} de todos los originales indexados.
        Usado durante la indexación incremental para detectar
        nuevos duplicados contra el índice existente.
        """
        assert self.db.connection is not None
        rows = self.db.connection.execute("""
            SELECT hash, path FROM media_files
            WHERE is_duplicate = 0 AND hash IS NOT NULL
        """).fetchall()
        return {row["hash"]: row["path"] for row in rows}

    def get_by_year(self, year: int) -> list[dict[str, Any]]:
        """Retorna todos los archivos de un año específico."""
        assert self.db.connection is not None
        rows = self.db.connection.execute(
            "SELECT * FROM media_files WHERE year = ? ORDER BY mtime",
            (year,)
        ).fetchall()
        return [dict(r) for r in rows]

    def needs_reindex(self, path: str, mtime: float) -> bool:
        """
        Verifica si un archivo necesita reindexarse.
        Retorna True si:
        - No existe en el índice, O
        - Su mtime cambió (fue modificado desde la última indexación)

        Este es el corazón de la indexación incremental:
        solo rehashea lo que realmente cambió.
        """
        existing = self.get_by_path(path)
        if existing is None:
            return True  # No está en el índice
        if existing["mtime"] != mtime:
            return True  # Fue modificado
        if existing["hash"] is None and existing["error"] is None:
            return True  # Quedó sin hash por algún motivo
        return False

    # ------------------------------------------------------------------
    # Queries de resumen
    # ------------------------------------------------------------------

    def count_by_year(self) -> list[dict[str, Any]]:
        """
        Retorna cantidad de archivos agrupados por año.
        Útil para mostrar un timeline en la UI.
        """
        assert self.db.connection is not None
        rows = self.db.connection.execute("""
            SELECT
                year,
                COUNT(*) AS total,
                SUM(CASE WHEN media_type='image' THEN 1 ELSE 0 END) AS images,
                SUM(CASE WHEN media_type='video' THEN 1 ELSE 0 END) AS videos
            FROM media_files
            WHERE year IS NOT NULL
            GROUP BY year
            ORDER BY year DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_duplicate_count(self) -> int:
        """Retorna total de duplicados detectados."""
        assert self.db.connection is not None
        row = self.db.connection.execute(
            "SELECT COUNT(*) AS n FROM media_files WHERE is_duplicate = 1"
        ).fetchone()
        return row["n"] or 0

    def get_total_duplicate_size(self) -> int:
        """Retorna tamaño total en bytes de todos los duplicados."""
        assert self.db.connection is not None
        row = self.db.connection.execute("""
            SELECT COALESCE(SUM(size_bytes), 0) AS total
            FROM media_files
            WHERE is_duplicate = 1
        """).fetchone()
        return row["total"]