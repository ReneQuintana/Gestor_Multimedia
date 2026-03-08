"""
services/organize_service.py
Responsabilidad: organizar archivos por año y tipo usando el índice.

Mejoras respecto al main.py original:
- Usa el índice para no releer metadatos del disco
- Origen y destino separados (idea del proyecto VSCode)
- Actualiza el índice con las nuevas rutas después de mover
- Retorna resultado detallado
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from db.database import Database
from db.repository import MediaRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

@dataclass
class OrganizeResult:
    """Resultado de una operación de organización."""
    moved: int = 0
    skipped: int = 0
    errors: int = 0

    def __str__(self) -> str:
        return (
            f"Movidos: {self.moved} | "
            f"Omitidos: {self.skipped} | "
            f"Errores: {self.errors}"
        )


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------

class OrganizeService:
    """
    Mueve archivos a una estructura organizada por año y tipo.

    Estructura de destino:
        dest_root/
        └── 2023/
            ├── imagenes/
            │   ├── hd/
            │   │   └── jpg/foto.jpg
            │   └── no_hd/
            │       └── png/captura.png
            └── videos/
                └── mp4/clip.mp4

    Origen y destino pueden ser la misma carpeta o carpetas distintas.
    Si son distintas, los archivos se MUEVEN (no se copian).
    """

    def __init__(self) -> None:
        self.db = Database()

    def organize(
        self,
        src_folder: str,
        dest_folder: str,
        progress_callback=None,
    ) -> OrganizeResult:
        """
        Organiza los archivos indexados de src_folder en dest_folder.

        Solo procesa archivos que están en el índice (ya indexados).
        Si un archivo no está indexado, se salta con warning.

        Args:
            src_folder:        Carpeta origen con los archivos a organizar.
            dest_folder:       Carpeta destino donde crear la estructura.
            progress_callback: fn(current, total, filename) para UI.

        Returns:
            OrganizeResult con estadísticas.
        """
        result = OrganizeResult()
        self.db.connect()

        try:
            repo = MediaRepository(self.db)

            # Obtener todos los archivos indexados del origen
            files = self._get_indexed_files_in_folder(repo, src_folder)

            if not files:
                logger.warning(
                    f"No hay archivos indexados en {src_folder}. "
                    f"Ejecuta la indexación primero."
                )
                return result

            total = len(files)
            logger.info(f"Organizando {total} archivos de {src_folder} → {dest_folder}")

            for current, record in enumerate(files, 1):
                src_path = record["path"]

                if progress_callback:
                    progress_callback(current, total, record["filename"])

                # Verificar que el archivo existe
                if not os.path.exists(src_path):
                    logger.warning(f"Archivo no encontrado en disco: {src_path}")
                    result.skipped += 1
                    continue

                # Construir ruta destino
                dest_path = self._build_dest_path(dest_folder, record)

                # Mismo archivo — no mover
                if os.path.abspath(src_path) == os.path.abspath(dest_path):
                    result.skipped += 1
                    continue

                try:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    dest_path = _resolve_conflict(dest_path)
                    shutil.move(src_path, dest_path)
                    logger.info(f"Movido: {src_path} → {dest_path}")

                    # Actualizar índice con la nueva ruta
                    repo.delete_by_path(src_path)
                    repo.upsert(
                        path=dest_path,
                        filename=record["filename"],
                        ext=record["ext"],
                        size_bytes=record["size_bytes"],
                        mtime=record["mtime"],
                        hash_value=record["hash"],
                        hash_type=record["hash_type"],
                        is_duplicate=bool(record["is_duplicate"]),
                        duplicate_of=record["duplicate_of"],
                        year=record["year"],
                        media_type=record["media_type"],
                        is_hd=record["is_hd"],
                    )

                    result.moved += 1

                except Exception:
                    logger.exception(f"Error moviendo: {src_path}")
                    result.errors += 1

        finally:
            self.db.close()

        logger.info(f"Organización completada: {result}")
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_indexed_files_in_folder(
        self, repo: MediaRepository, folder: str
    ) -> list[dict]:
        """
        Retorna todos los registros del índice cuya ruta empieza por folder.
        Esto evita escanear el disco — usa directamente el índice.
        """
        assert self.db.connection is not None
        folder_prefix = os.path.abspath(folder)
        rows = self.db.connection.execute("""
            SELECT * FROM media_files
            WHERE path LIKE ? || '%'
            ORDER BY year, media_type, path
        """, (folder_prefix,)).fetchall()
        return [dict(r) for r in rows]

    def _build_dest_path(self, dest_root: str, record: dict) -> str:
        """
        Construye la ruta destino según el esquema de organización.

        Estructura: dest_root/año/tipo/[hd|no_hd]/formato/archivo
        """
        year = str(record["year"] or "sin_año")
        media_type = record["media_type"] or "otros"
        tipo = "imagenes" if media_type == "image" else "videos"
        formato = record["ext"].lstrip('.')

        parts = [dest_root, year, tipo]

        # Clasificación HD solo para imágenes
        if media_type == "image":
            is_hd = record.get("is_hd")
            calidad = "hd" if is_hd == 1 else "no_hd"
            parts.append(calidad)

        parts.append(formato)
        parts.append(record["filename"])

        return os.path.join(*parts)


# ---------------------------------------------------------------------------
# Helper: resolver conflictos de nombre
# ---------------------------------------------------------------------------

def _resolve_conflict(dest_path: str) -> str:
    """
    Si dest_path ya existe, añade sufijo numérico hasta encontrar nombre libre.
    foto.jpg → foto_1.jpg → foto_2.jpg → ...
    """
    if not os.path.exists(dest_path):
        return dest_path

    base, ext = os.path.splitext(dest_path)
    counter = 1
    while True:
        candidate = f"{base}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1