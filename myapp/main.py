"""
main.py
Punto de entrada de la aplicación.

Responsabilidades de este archivo:
1. Configurar el logging global
2. Lanzar la UI

TODO Fase 2: reemplazar el bloque de UI por la app Flet.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from core.config import LOG_FORMAT, LOG_LEVEL, LOG_PATH


def _setup_logging() -> None:
    """
    Configura el logging global una sola vez al arrancar.
    Cada módulo usa logging.getLogger(__name__) — todos heredan esta config.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format=LOG_FORMAT,
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Iniciando Buscador de Duplicados")

    # TODO Fase 2: importar y lanzar UI Flet
    # Por ahora lanzar la UI Tkinter existente como puente
    try:
        from ui.app import launch
        launch()
    except ImportError:
        logger.warning("UI no implementada aún. Ejecutando modo consola de prueba.")
        _run_cli_demo()


def _run_cli_demo() -> None:
    """
    Demo de consola para probar los servicios sin UI.
    Útil durante el desarrollo de la Fase 1.
    """
    from services.index_service import IndexService

    if len(sys.argv) < 2:
        print("Uso: python main.py <carpeta_a_indexar>")
        print("Ejemplo: python main.py C:\\fotos")
        return

    folder = sys.argv[1]
    print(f"\nIndexando: {folder}")
    print("-" * 50)

    service = IndexService()

    def on_progress(current: int, total: int, filepath: str) -> None:
        pct = int(current / total * 100) if total > 0 else 0
        print(f"\r[{pct:3d}%] {current}/{total} — {Path(filepath).name}", end="", flush=True)

    result = service.index_folder(folder, progress_callback=on_progress)

    print(f"\n\n{'=' * 50}")
    print(f"Resultado: {result}")

    stats = service.get_index_stats()
    print(f"\nÍndice actual:")
    print(f"  Total archivos : {stats['total']}")
    print(f"  Imágenes       : {stats['images']}")
    print(f"  Vídeos         : {stats['videos']}")
    print(f"  Duplicados     : {stats['duplicates']}")
    print(f"  Con errores    : {stats['errors']}")


if __name__ == "__main__":
    main()