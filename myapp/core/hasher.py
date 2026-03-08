"""
core/hasher.py
Funciones de hashing para imágenes y vídeos.

Dos estrategias:
- Imágenes: hash perceptual (aHash) — detecta duplicados aunque cambien
             compresión, metadatos o haya leves diferencias visuales.
- Vídeos:   hash MD5 por contenido — comparación exacta byte a byte.

Ambas degradan a MD5 si Pillow no está disponible o el archivo no se puede abrir.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from core.config import (
    AHASH_SIZE,
    FILE_HASH_CHUNK_SIZE,
    IMAGE_EXTS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Disponibilidad de Pillow
# ---------------------------------------------------------------------------

try:
    from PIL import Image as _PILImage
    _HAVE_PIL = True
except ImportError:
    _PILImage = None  # type: ignore
    _HAVE_PIL = False
    logger.warning(
        "Pillow no está instalado. Las imágenes se hashearán por MD5 "
        "(solo detecta duplicados exactos, no perceptuales)."
    )

# ---------------------------------------------------------------------------
# Hash perceptual (aHash)
# ---------------------------------------------------------------------------

def average_hash(img: "_PILImage.Image", hash_size: int = AHASH_SIZE) -> str:
    """
    Calcula el Average Hash (aHash) de una imagen PIL ya abierta.

    Algoritmo:
    1. Convierte a escala de grises
    2. Redimensiona a hash_size x hash_size
    3. Compara cada pixel con el promedio → bit 1 si mayor, 0 si menor
    4. Devuelve los bits como string hexadecimal

    Retorna string hex de (hash_size² / 4) caracteres.
    Ejemplo con hash_size=8: 16 chars hex = 64 bits.

    Por qué aHash y no MD5 para imágenes:
    - MD5 cambia completamente si cambia 1 solo byte (ej: metadatos EXIF)
    - aHash es robusto ante recompresión, cambios de brillo leve, resize
    - Es suficiente para deduplicación de fotos personales sin GPU
    """
    if hash_size <= 0:
        raise ValueError(f"hash_size debe ser > 0, recibido: {hash_size}")
    if not _HAVE_PIL:
        raise RuntimeError("Pillow no está disponible para calcular aHash.")

    resample = getattr(_PILImage, "Resampling", _PILImage).LANCZOS  # type: ignore[attr-defined]
    small = img.convert("L").resize((hash_size, hash_size), resample=resample)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)

    bits = 0
    for p in pixels:
        bits = (bits << 1) | (1 if p > avg else 0)

    hex_len = (hash_size * hash_size + 3) // 4
    return f"{bits:0{hex_len}x}"


# ---------------------------------------------------------------------------
# Hash MD5 (contenido exacto)
# ---------------------------------------------------------------------------

def file_hash(path: str | Path) -> str | None:
    """
    Calcula el hash MD5 del contenido completo de un archivo.

    Lee en chunks de FILE_HASH_CHUNK_SIZE bytes para no cargar
    archivos grandes (vídeos de GBs) enteros en memoria.

    Retorna string hex de 32 chars, o None si el archivo no se puede leer.
    """
    h = hashlib.md5()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(FILE_HASH_CHUNK_SIZE), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        logger.exception(f"No se pudo leer el archivo para hashear: {path}")
        return None


# ---------------------------------------------------------------------------
# Hash unificado por tipo de archivo
# ---------------------------------------------------------------------------

def media_hash(path: str | Path) -> str | None:
    """
    Punto de entrada principal. Selecciona la estrategia correcta según extensión:
    - Imagen + Pillow disponible → aHash perceptual
    - Imagen sin Pillow          → MD5 (fallback)
    - Vídeo                      → MD5

    Este es el único método que el resto de la app debe llamar.
    """
    ext = Path(path).suffix.lower()

    if ext in IMAGE_EXTS:
        return _image_hash(path)
    else:
        return file_hash(path)


def _image_hash(path: str | Path) -> str | None:
    """
    Intenta calcular aHash. Si falla (archivo corrupto, formato no soportado
    por Pillow como algunos HEIC), degrada a MD5.
    """
    if not _HAVE_PIL:
        return file_hash(path)

    try:
        with _PILImage.open(path) as img:  # type: ignore[union-attr]
            return average_hash(img)
    except Exception:
        logger.warning(
            f"aHash falló para {path}, usando MD5 como fallback."
        )
        return file_hash(path)


# ---------------------------------------------------------------------------
# Utilidad: comparar dos hashes perceptuales
# ---------------------------------------------------------------------------

def hamming_distance(hash1: str, hash2: str) -> int:
    """
    Calcula la distancia de Hamming entre dos hashes hex.
    Útil para detectar imágenes SIMILARES (no solo idénticas).

    Distancia 0  → idénticas
    Distancia <= 5 → muy similares (duplicados probables)
    Distancia > 10 → imágenes distintas

    Ejemplo de uso futuro:
        if hamming_distance(h1, h2) <= 5:
            # marcar como duplicado probable
    """
    if len(hash1) != len(hash2):
        raise ValueError(
            f"Los hashes deben tener la misma longitud: {len(hash1)} vs {len(hash2)}"
        )
    # Convertir hex → int → XOR → contar bits en 1
    diff = int(hash1, 16) ^ int(hash2, 16)
    return bin(diff).count('1')