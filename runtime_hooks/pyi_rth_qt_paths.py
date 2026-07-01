from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    candidates = [
        root / "PySide6" / "Qt" / "plugins",
        root / "_internal" / "PySide6" / "Qt" / "plugins",
        Path(sys.executable).resolve().parent / "_internal" / "PySide6" / "Qt" / "plugins",
    ]
    plugin_paths = [str(path) for path in candidates if path.exists()]
    if plugin_paths:
        existing = os.environ.get("QT_PLUGIN_PATH")
        os.environ["QT_PLUGIN_PATH"] = os.pathsep.join(plugin_paths + ([existing] if existing else []))
