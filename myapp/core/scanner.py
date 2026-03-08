"""
core/scanner.py
Responsabilidad única: recorrer el sistema de archivos y detectar archivos multimedia.

NO calcula hashes.
NO mueve archivos.
NO habla con la base de datos.
Solo encuentra archivos y extrae su metadata básica.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generator

from core.config import IMAGE_EXTS, MEDIA_EXTS, VIDEO_EXTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modelo de datos: MediaFile
# ---------------------------------------------------------------------------

@dataclass
class MediaFile:
    """
    Representa un archivo multimedia encontrado en el sistema de archivos.
    Es un objeto de datos puro — sin lógica de negocio.

    Se usa como unidad de trabajo entre scanner → indexer → deduper.
    """
    path: str               # Ruta absoluta completa
    filename: str           # Solo el nombre: "foto.jpg"
    ext: str                # Extensión en minúsculas: ".jpg"
    size_bytes: int         # Tamaño en bytes
    mtime: float            # Timestamp de modificación (para indexación incremental)
    is_image: bool          # True si es imagen, False si es vídeo
    year: int               # Año extraído del mtime (para organización)

    @property
    def is_video(self) -> bool:
        return not self.is_image

    @property
    def mtime_dt(self) -> datetime:
        """Convierte mtime float a datetime legible."""
        return datetime.fromtimestamp(self.mtime)

    @property
    def format_name(self) -> str:
        """Extensión sin punto: 'jpg', 'mp4', etc."""
        return self.ext.lstrip('.')


# ---------------------------------------------------------------------------
# Función principal de escaneo
# ---------------------------------------------------------------------------

def scan_folder(
    root: str | Path,
    progress_callback: callable | None = None,
) -> Generator[MediaFile, None, None]:
    """
    Generador que recorre root recursivamente y yields MediaFile
    por cada archivo multimedia encontrado.

    Usar como generador (no lista) permite procesar 21k+ archivos
    sin cargarlos todos en memoria a la vez.

    Args:
        root:              Carpeta raíz a escanear.
        progress_callback: Función opcional fn(current, total, filepath)
                           para actualizar UI. Se llama por cada archivo
                           multimedia encontrado (no por cada archivo del disco).

    Yields:
        MediaFile por cada imagen o vídeo encontrado.

    Ejemplo de uso:
        for media_file in scan_folder("/fotos"):
            print(media_file.path, media_file.year)
    """
    root = Path(root)

    if not root.exists():
        logger.error(f"La carpeta no existe: {root}")
        return

    if not root.is_dir():
        logger.error(f"La ruta no es una carpeta: {root}")
        return

    # Contar total primero para progress_callback con porcentaje real
    total = _count_media_files(root)
    current = 0

    logger.info(f"Iniciando escaneo: {root} ({total} archivos multimedia estimados)")

    for dirpath, dirnames, filenames in os.walk(root):
        # Ordenar para escaneo reproducible (mismo orden cada vez)
        dirnames.sort()
        filenames.sort()

        for filename in filenames:
            ext = Path(filename).suffix.lower()

            if ext not in MEDIA_EXTS:
                continue

            filepath = os.path.join(dirpath, filename)

            try:
                stat = os.stat(filepath)
                mtime = stat.st_mtime
                size_bytes = stat.st_size
                year = datetime.fromtimestamp(mtime).year
                is_image = ext in IMAGE_EXTS

                media_file = MediaFile(
                    path=filepath,
                    filename=filename,
                    ext=ext,
                    size_bytes=size_bytes,
                    mtime=mtime,
                    is_image=is_image,
                    year=year,
                )

                current += 1

                if progress_callback:
                    progress_callback(current, total, filepath)

                yield media_file

            except OSError:
                logger.warning(f"No se pudo leer metadata de: {filepath}")
                continue

    logger.info(f"Escaneo completado: {current} archivos encontrados en {root}")


# ---------------------------------------------------------------------------
# Estadísticas de una carpeta
# ---------------------------------------------------------------------------

@dataclass
class FolderStats:
    """Resumen estadístico de una carpeta escaneada."""
    total_files: int = 0
    total_images: int = 0
    total_videos: int = 0
    total_size_bytes: int = 0
    years: set[int] = field(default_factory=set)
    extensions: dict[str, int] = field(default_factory=dict)

    @property
    def total_size_mb(self) -> float:
        return round(self.total_size_bytes / (1024 * 1024), 2)

    @property
    def total_size_gb(self) -> float:
        return round(self.total_size_bytes / (1024 * 1024 * 1024), 3)

    def __str__(self) -> str:
        return (
            f"Archivos: {self.total_files} "
            f"(imágenes: {self.total_images}, vídeos: {self.total_videos}) | "
            f"Tamaño: {self.total_size_gb} GB | "
            f"Años: {sorted(self.years)}"
        )


def get_folder_stats(root: str | Path) -> FolderStats:
    """
    Recorre la carpeta y devuelve estadísticas sin calcular hashes.
    Útil para mostrar un resumen antes de iniciar la indexación.

    Ejemplo de uso en UI:
        stats = get_folder_stats("/fotos")
        print(f"Encontradas {stats.total_images} imágenes ({stats.total_size_gb} GB)")
    """
    stats = FolderStats()

    for media_file in scan_folder(root):
        stats.total_files += 1
        stats.total_size_bytes += media_file.size_bytes
        stats.years.add(media_file.year)

        ext = media_file.ext
        stats.extensions[ext] = stats.extensions.get(ext, 0) + 1

        if media_file.is_image:
            stats.total_images += 1
        else:
            stats.total_videos += 1

    return stats


# ---------------------------------------------------------------------------
# Helper interno
# ---------------------------------------------------------------------------

def _count_media_files(root: Path) -> int:
    """
    Cuenta archivos multimedia sin procesarlos.
    Se usa para calcular el porcentaje real de progreso.
    En carpetas grandes puede tardar 1-2 segundos — aceptable.
    """
    count = 0
    for _, _, filenames in os.walk(root):
        for filename in filenames:
            if Path(filename).suffix.lower() in MEDIA_EXTS:
                count += 1
    return count