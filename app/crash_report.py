from __future__ import annotations

import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path

from app import APP_NAME, APP_VERSION
from app.paths import data_dir, ensure_user_directories, executable_path, exports_dir, is_frozen, logs_dir, settings_dir


def write_crash_report(error: BaseException) -> Path:
    ensure_user_directories()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = logs_dir() / f"crash-{timestamp}.log"
    traceback_text = "".join(traceback.format_exception(error))

    directories = {
        "logs": logs_dir(),
        "data": data_dir(),
        "settings": settings_dir(),
        "exports": exports_dir(),
    }
    directory_status = []
    for name, directory in directories.items():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            writable_probe = directory / ".write-test"
            writable_probe.write_text("ok", encoding="utf-8")
            writable_probe.unlink(missing_ok=True)
            status = "available,writable"
        except Exception as directory_error:
            status = f"unavailable: {directory_error}"
        directory_status.append(f"{name}: {directory} [{status}]")

    lines = [
        f"{APP_NAME} crash report",
        f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"Version: {APP_VERSION}",
        f"Windows: {platform.platform()}",
        f"Architecture: {platform.machine()} ({platform.architecture()[0]})",
        f"Frozen: {is_frozen()}",
        f"Executable: {executable_path()}",
        f"Python: {sys.version.replace(chr(10), ' ')}",
        f"sys._MEIPASS: {getattr(sys, '_MEIPASS', '')}",
        "Directories:",
        *directory_status,
        "",
        "Traceback:",
        traceback_text,
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
