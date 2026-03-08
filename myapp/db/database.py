"""
db/database.py
Responsabilidad única: conexión a SQLite y creación del esquema.

NO hace queries de negocio.
NO sabe nada de imágenes ni hashes.
Solo abre la conexión y garantiza que las tablas existen.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from core.config import DB_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Esquema SQL
# ---------------------------------------------------------------------------

_CREATE_MEDIA_FILES = """
CREATE TABLE IF NOT EXISTS media_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identidad del archivo
    path            TEXT UNIQUE NOT NULL,   -- ruta absoluta (clave natural)
    filename        TEXT NOT NULL,
    ext             TEXT NOT NULL,          -- '.jpg', '.mp4', etc.
    size_bytes      INTEGER,
    mtime           REAL NOT NULL,          -- timestamp float para comparar cambios

    -- Hashing
    hash            TEXT,                   -- aHash para imágenes, MD5 para vídeos
    hash_type       TEXT,                   -- 'ahash' | 'md5' (para saber cómo comparar)

    -- Deduplicación
    is_duplicate    INTEGER NOT NULL DEFAULT 0,  -- 0=original, 1=duplicado
    duplicate_of    TEXT,                        -- path del original si es duplicado

    -- Organización
    year            INTEGER,               -- extraído del mtime
    media_type      TEXT,                  -- 'image' | 'video'
    is_hd           INTEGER,               -- 1=HD, 0=no HD, NULL=no calculado

    -- Control de indexación
    indexed_at      TEXT NOT NULL,         -- ISO datetime del último indexado
    error           TEXT                   -- mensaje si hubo error al procesar
);
"""

_CREATE_INDEX_HASH = """
CREATE INDEX IF NOT EXISTS idx_media_hash
ON media_files(hash);
"""

_CREATE_INDEX_PATH = """
CREATE INDEX IF NOT EXISTS idx_media_path
ON media_files(path);
"""

_CREATE_INDEX_YEAR = """
CREATE INDEX IF NOT EXISTS idx_media_year
ON media_files(year);
"""

_CREATE_INDEX_DUPLICATE = """
CREATE INDEX IF NOT EXISTS idx_media_duplicate
ON media_files(is_duplicate);
"""

# Tabla de metadatos de la propia base de datos
# Útil para versioning del esquema y stats generales
_CREATE_APP_META = """
CREATE TABLE IF NOT EXISTS app_meta (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Clase de conexión
# ---------------------------------------------------------------------------

class Database:
    """
    Gestiona la conexión SQLite y el ciclo de vida del esquema.

    Uso recomendado como context manager:

        with Database() as db:
            db.connection.execute("SELECT ...")

    O como instancia persistente en servicios de larga duración:

        db = Database()
        db.connect()
        # ... usar db.connection ...
        db.close()
    """

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """
        Abre la conexión SQLite y aplica configuración de rendimiento.
        Crea el archivo de base de datos si no existe.
        """
        if self.connection is not None:
            return  # Ya conectado

        logger.info(f"Conectando a base de datos: {self.db_path}")

        # Crear carpeta padre si no existe (ej: data/)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # Necesario para uso en threads secundarios
        )

        # Configuración de rendimiento y seguridad
        self.connection.execute("PRAGMA journal_mode = WAL")
        # WAL (Write-Ahead Logging): permite leer mientras se escribe.
        # Crítico para no bloquear la UI durante indexaciones largas.

        self.connection.execute("PRAGMA synchronous = NORMAL")
        # NORMAL es más rápido que FULL y seguro para nuestro caso de uso.

        self.connection.execute("PRAGMA foreign_keys = ON")
        # Activar claves foráneas (no las usamos aún pero es buena práctica).

        self.connection.execute("PRAGMA cache_size = -32000")
        # Cache de 32 MB en RAM para queries frecuentes sobre 21k registros.

        self.connection.row_factory = sqlite3.Row
        # Permite acceder a columnas por nombre: row['path'] en vez de row[0]

        self._create_schema()
        logger.info("Base de datos lista.")

    def close(self) -> None:
        """Cierra la conexión de forma limpia."""
        if self.connection:
            self.connection.close()
            self.connection = None
            logger.debug("Conexión a base de datos cerrada.")

    # ------------------------------------------------------------------
    # Esquema
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        """
        Crea tablas e índices si no existen.
        Es idempotente: se puede llamar múltiples veces sin error.
        """
        assert self.connection is not None

        with self.connection:
            self.connection.execute(_CREATE_MEDIA_FILES)
            self.connection.execute(_CREATE_INDEX_HASH)
            self.connection.execute(_CREATE_INDEX_PATH)
            self.connection.execute(_CREATE_INDEX_YEAR)
            self.connection.execute(_CREATE_INDEX_DUPLICATE)
            self.connection.execute(_CREATE_APP_META)

            # Registrar versión del esquema
            self.connection.execute("""
                INSERT OR IGNORE INTO app_meta (key, value)
                VALUES ('schema_version', '1')
            """)

        logger.debug("Esquema verificado y actualizado.")

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        """Lee un valor de la tabla app_meta."""
        assert self.connection is not None
        row = self.connection.execute(
            "SELECT value FROM app_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Escribe o actualiza un valor en app_meta."""
        assert self.connection is not None
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                (key, value)
            )

    def get_stats(self) -> dict[str, int]:
        """
        Retorna estadísticas rápidas del índice actual.
        Útil para mostrar en la pantalla principal de la UI.

        Retorna dict con:
            total, images, videos, duplicates, indexed_with_errors
        """
        assert self.connection is not None
        row = self.connection.execute("""
            SELECT
                COUNT(*)                                    AS total,
                SUM(CASE WHEN media_type='image' THEN 1 ELSE 0 END) AS images,
                SUM(CASE WHEN media_type='video' THEN 1 ELSE 0 END) AS videos,
                SUM(is_duplicate)                           AS duplicates,
                SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)  AS errors
            FROM media_files
        """).fetchone()

        return {
            "total":      row["total"]      or 0,
            "images":     row["images"]     or 0,
            "videos":     row["videos"]     or 0,
            "duplicates": row["duplicates"] or 0,
            "errors":     row["errors"]     or 0,
        }