"""
core/config.py
Configuración global de la aplicación.
Todas las constantes y rutas se definen aquí — nunca hardcodeadas en otros módulos.
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas base
# ---------------------------------------------------------------------------

# Raíz del proyecto (dos niveles arriba de este archivo: core/ -> myapp/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Carpeta de datos de la app (base de datos, logs)
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Base de datos SQLite
DB_PATH = DATA_DIR / "media_index.db"

# Log de la aplicación
LOG_PATH = DATA_DIR / "app.log"

# Carpeta inicial para el selector de archivos
DEFAULT_MEDIA_DIR = Path(r"C:\myapp")

# ---------------------------------------------------------------------------
# Extensiones soportadas
# ---------------------------------------------------------------------------

IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.heic', '.webp'})
VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm'})
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# ---------------------------------------------------------------------------
# Parámetros de hashing
# ---------------------------------------------------------------------------

# Tamaño del hash perceptual (8 = 64 bits, buen balance velocidad/precisión)
AHASH_SIZE = 8

# Tamaño de chunk para lectura de archivos en MD5
FILE_HASH_CHUNK_SIZE = 8192

# ---------------------------------------------------------------------------
# Parámetros de clasificación HD
# ---------------------------------------------------------------------------

HD_MIN_WIDTH  = 1920
HD_MIN_HEIGHT = 1080

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FORMAT  = "%(asctime)s %(levelname)s [%(module)s] %(message)s"
LOG_LEVEL   = "INFO"