"""
services/index_service.py
Orquestador de indexación incremental.

Une scanner + hasher + repository para construir y mantener el índice.
Es el servicio más importante de la Fase 1.

Flujo:
1. Escanear carpeta → obtener MediaFile por cada archivo
2. Para cada archivo: ¿necesita reindexarse? (mtime cambió o es nuevo)
3. Si sí → calcular hash
4. Detectar duplicados contra el índice existente
5. Guardar en base de datos
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from core.hasher import media_hash
from core.scanner import MediaFile, scan_folder
from db.database import Database
from db.repository import MediaRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resultado de indexación
# ---------------------------------------------------------------------------

@dataclass
class IndexResult:
    """Resumen del resultado de una indexación."""
    total_scanned: int = 0
    new_indexed: int = 0
    already_indexed: int = 0
    duplicates_found: int = 0
    errors: int = 0
    skipped: int = 0
    duplicate_size_bytes: int = 0

    @property
    def duplicate_size_mb(self) -> float:
        return round(self.duplicate_size_bytes / (1024 * 1024), 2)

    def __str__(self) -> str:
        return (
            f"Escaneados: {self.total_scanned} | "
            f"Nuevos: {self.new_indexed} | "
            f"Ya indexados: {self.already_indexed} | "
            f"Duplicados: {self.duplicates_found} "
            f"({self.duplicate_size_mb} MB) | "
            f"Errores: {self.errors}"
        )


# ---------------------------------------------------------------------------
# Servicio principal
# ---------------------------------------------------------------------------

class IndexService:
    """
    Gestiona la indexación incremental de una carpeta de medios.

    Uso:
        service = IndexService()
        result = service.index_folder(
            folder="/fotos",
            progress_callback=lambda cur, tot, f: print(f"{cur}/{tot} {f}")
        )
        print(result)
    """

    def __init__(self) -> None:
        self.db = Database()

    def index_folder(
        self,
        folder: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> IndexResult:
        """
        Indexa una carpeta de forma incremental.

        Solo procesa archivos nuevos o modificados.
        Los archivos ya indexados sin cambios se saltan.

        Args:
            folder:            Ruta de la carpeta a indexar.
            progress_callback: fn(current, total, filepath)
                               Llamada por cada archivo procesado.
                               Usar para actualizar barra de progreso en UI.

        Returns:
            IndexResult con estadísticas del proceso.
        """
        result = IndexResult()
        self.db.connect()

        try:
            repo = MediaRepository(self.db)

            # Cargar hashes existentes una sola vez al inicio.
            # Evita hacer una query por archivo durante el escaneo.
            # Para 21k archivos esto es un dict de ~21k entradas en RAM — aceptable.
            known_hashes: dict[str, str] = repo.get_all_hashes()
            logger.info(
                f"Iniciando indexación: {folder} "
                f"({len(known_hashes)} registros previos en índice)"
            )

            for media_file in scan_folder(folder, progress_callback):
                result.total_scanned += 1

                # ¿Necesita reindexarse?
                if not repo.needs_reindex(media_file.path, media_file.mtime):
                    result.already_indexed += 1
                    continue

                # Calcular hash
                h = _safe_hash(media_file)

                if h is None:
                    # Error al hashear — registrar igual con error
                    _save_with_error(repo, media_file, "hash_failed")
                    result.errors += 1
                    continue

                hash_type = "ahash" if media_file.is_image else "md5"

                # ¿Es duplicado?
                is_duplicate = h in known_hashes
                duplicate_of = known_hashes[h] if is_duplicate else None

                if is_duplicate:
                    result.duplicates_found += 1
                    result.duplicate_size_bytes += media_file.size_bytes
                    logger.info(
                        f"Duplicado: {media_file.path} "
                        f"↔ {duplicate_of}"
                    )
                else:
                    # Registrar como original en el dict local
                    known_hashes[h] = media_file.path

                # Calcular HD para imágenes
                is_hd = _calculate_hd(media_file) if media_file.is_image else None

                # Guardar en base de datos
                repo.upsert(
                    path=media_file.path,
                    filename=media_file.filename,
                    ext=media_file.ext,
                    size_bytes=media_file.size_bytes,
                    mtime=media_file.mtime,
                    hash_value=h,
                    hash_type=hash_type,
                    is_duplicate=is_duplicate,
                    duplicate_of=duplicate_of,
                    year=media_file.year,
                    media_type="image" if media_file.is_image else "video",
                    is_hd=is_hd,
                )

                result.new_indexed += 1

            # Actualizar fecha de última indexación
            from datetime import datetime
            self.db.set_meta("last_indexed_at", datetime.now().isoformat())
            self.db.set_meta("last_indexed_folder", folder)

            logger.info(f"Indexación completada: {result}")

        except Exception:
            logger.exception("Error inesperado durante la indexación.")
            raise
        finally:
            self.db.close()

        return result

    def clean_index(self) -> int:
        """
        Elimina del índice archivos que ya no existen en disco.
        Llama esto después de borrar duplicados físicamente.
        Retorna cantidad de registros eliminados.
        """
        self.db.connect()
        try:
            repo = MediaRepository(self.db)
            return repo.delete_missing_files()
        finally:
            self.db.close()

    def get_index_stats(self) -> dict:
        """Retorna estadísticas del índice actual."""
        self.db.connect()
        try:
            return self.db.get_stats()
        finally:
            self.db.close()


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _safe_hash(media_file: MediaFile) -> str | None:
    """Calcula hash con manejo de errores. Retorna None si falla."""
    try:
        return media_hash(media_file.path)
    except Exception:
        logger.exception(f"Error calculando hash: {media_file.path}")
        return None


def _save_with_error(repo: MediaRepository, media_file: MediaFile, error: str) -> None:
    """Guarda un archivo en el índice marcado con error."""
    repo.upsert(
        path=media_file.path,
        filename=media_file.filename,
        ext=media_file.ext,
        size_bytes=media_file.size_bytes,
        mtime=media_file.mtime,
        hash_value=None,
        hash_type=None,
        is_duplicate=False,
        duplicate_of=None,
        year=media_file.year,
        media_type="image" if media_file.is_image else "video",
        error=error,
    )


def _calculate_hd(media_file: MediaFile) -> int | None:
    """
    Determina si una imagen es HD (>=1920x1080).
    Retorna 1 (HD), 0 (no HD), o None si no se pudo leer.
    """
    try:
        from PIL import Image as PILImage
        from core.config import HD_MIN_HEIGHT, HD_MIN_WIDTH
        with PILImage.open(media_file.path) as img:
            w, h = img.size
            return 1 if w >= HD_MIN_WIDTH and h >= HD_MIN_HEIGHT else 0
    except Exception:
        return None