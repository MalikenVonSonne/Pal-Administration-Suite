"""Packaged Pal Admin entry point with a user-local startup log."""

from __future__ import annotations

import logging
from pathlib import Path


def _configure_logging() -> Path:
    log_dir = Path.home() / "AppData" / "Local" / "PalAdmin" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "paladmin.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )
    logging.info("Starting Pal Admin; executable=%s", __file__)
    return log_path


if __name__ == "__main__":
    log_path = _configure_logging()
    try:
        from pal_editor.gui import main

        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        logging.exception("Pal Admin startup failed")
        raise RuntimeError(f"Pal Admin could not start. See {log_path}")
