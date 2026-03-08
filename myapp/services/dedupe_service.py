"""
services/dedupe_service.py
Responsabilidad: gestionar duplicados usando el índice de la base de datos.

A diferencia del main.py original que escaneaba en tiempo real,
este servicio consulta el índice ya construido.
Resultado: la deduplicación es instantánea sin importar cuántas fotos haya.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from db.database import Database
from db.repository import MediaRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class DuplicatePair:
    """Representa un par original/duplicado."""
    original_path: str
    original_filename: str
    original_size: int
    duplicate_path: str
    duplicate_filename: str
    duplicate_size: int

    @property
    def size_saved_mb(self) -> float:
        """Espacio que se liberaría al borrar el duplicado."""
        return round(self.duplicate_size / (1024 * 1024), 2)


@dataclass
class DedupeResult:
    """Resultado de una operación de borrado de duplicados."""
    deleted: int = 0
    errors: int = 0
    bytes_freed: int = 0

    @property
    def mb_freed(self) -> float:
        return round(self.bytes_freed / (1024 * 1024), 2)

    def __str__(self) -> str:
        return (
            f"Eliminados: {self.deleted} archivos "
            f"({self.mb_freed} MB liberados) | "
            f"Errores: {self.errors}"
        )


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------

class DedupeService:
    """
    Consulta duplicados del índice y gestiona su eliminación.

    Flujo típico:
        service = DedupeService()
        pairs = service.get_duplicate_pairs()   # Lista para mostrar en UI
        result = service.delete_duplicates(     # Borrar los seleccionados
            [pair.duplicate_path for pair in pairs]
        )
    """

    def __init__(self) -> None:
        self.db = Database()

    def get_duplicate_pairs(self) -> list[DuplicatePair]:
        """
        Retorna todos los pares original/duplicado del índice.
        Instantáneo — solo consulta la DB, no escanea disco.

        Retorna lista vacía si no hay duplicados o el índice está vacío.
        """
        self.db.connect()
        try:
            repo = MediaRepository(self.db)
            rows = repo.get_duplicates()

            pairs = []
            for row in rows:
                pairs.append(DuplicatePair(
                    original_path=row["orig_path"],
                    original_filename=row["orig_filename"],
                    original_size=row["orig_size"] or 0,
                    duplicate_path=row["dup_path"],
                    duplicate_filename=row["dup_filename"],
                    duplicate_size=row["dup_size"] or 0,
                ))

            logger.info(f"Duplicados encontrados en índice: {len(pairs)}")
            return pairs

        finally:
            self.db.close()

    def get_summary(self) -> dict:
        """
        Retorna resumen de duplicados sin traer todos los pares.
        Útil para mostrar en pantalla principal antes de entrar a la vista de duplicados.

        Retorna:
            {count: N, total_size_bytes: N, total_size_mb: N}
        """
        self.db.connect()
        try:
            repo = MediaRepository(self.db)
            count = repo.get_duplicate_count()
            size = repo.get_total_duplicate_size()
            return {
                "count": count,
                "total_size_bytes": size,
                "total_size_mb": round(size / (1024 * 1024), 2),
            }
        finally:
            self.db.close()

    def delete_duplicates(self, paths_to_delete: list[str]) -> DedupeResult:
        """
        Elimina físicamente los archivos indicados y los limpia del índice.

        Args:
            paths_to_delete: Lista de rutas absolutas a eliminar.
                             Deben ser duplicados — no originales.

        Returns:
            DedupeResult con estadísticas del borrado.

        IMPORTANTE: Solo acepta archivos marcados como is_duplicate=1 en el índice.
        Si se intenta borrar un original, se salta con warning.
        """
        result = DedupeResult()

        if not paths_to_delete:
            logger.warning("delete_duplicates llamado con lista vacía.")
            return result

        self.db.connect()
        try:
            repo = MediaRepository(self.db)

            for path in paths_to_delete:
                # Verificar que realmente es un duplicado en el índice
                record = repo.get_by_path(path)
                if record and not record["is_duplicate"]:
                    logger.warning(
                        f"Intento de borrar un ORIGINAL ignorado: {path}"
                    )
                    continue

                size = record["size_bytes"] if record else 0

                # Borrar físicamente
                try:
                    os.remove(path)
                    result.deleted += 1
                    result.bytes_freed += size or 0
                    logger.info(f"Eliminado: {path}")
                except FileNotFoundError:
                    logger.warning(f"Archivo ya no existe en disco: {path}")
                    result.deleted += 1  # Contarlo como éxito — ya no está
                except OSError:
                    logger.exception(f"No se pudo eliminar: {path}")
                    result.errors += 1
                    continue

                # Limpiar del índice
                repo.delete_by_path(path)

        finally:
            self.db.close()

        logger.info(f"Borrado completado: {result}")
        return result