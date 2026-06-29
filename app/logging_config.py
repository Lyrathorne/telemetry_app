from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

from app import APP_NAME, APP_VERSION
from app.paths import ensure_user_directories, executable_path, is_frozen, logs_dir


LOG_FILE_NAME = "racing_telemetry.log"


def configure_logging() -> Path:
    ensure_user_directories()
    log_path = logs_dir() / LOG_FILE_NAME

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if not getattr(sys, "frozen", False):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    logging.info("%s %s starting", APP_NAME, APP_VERSION)
    logging.info("Executable: %s", executable_path())
    logging.info("Working directory: %s", Path.cwd())
    logging.info("Python: %s", sys.version.replace("\n", " "))
    logging.info("Operating system: %s", platform.platform())
    logging.info("Frozen: %s", is_frozen())
    return log_path
