from __future__ import annotations

import os
import sys
from pathlib import Path

from app import APP_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    return Path(sys.executable if is_frozen() else sys.argv[0]).resolve()


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return project_root()


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def user_data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME}"


def logs_dir() -> Path:
    return user_data_root() / "logs"


def data_dir() -> Path:
    return user_data_root() / "data"


def exports_dir() -> Path:
    return user_data_root() / "exports"


def settings_dir() -> Path:
    return user_data_root() / "settings"


def ensure_user_directories() -> None:
    for path in (logs_dir(), data_dir(), exports_dir(), settings_dir()):
        path.mkdir(parents=True, exist_ok=True)
