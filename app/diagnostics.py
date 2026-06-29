from __future__ import annotations

import socket
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion

from app import APP_NAME, APP_VERSION
from app.paths import executable_path, is_frozen, user_data_root
from telemetry import SOURCE_LABELS
from telemetry.assetto_corsa import MAP_NAMES as AC_MAP_NAMES
from telemetry.assetto_corsa_competizione import MAP_NAMES as ACC_MAP_NAMES
from telemetry.windows_shared_memory import shared_memory_page_available


def run_diagnostics(port: int = 20777) -> int:
    lines = collect_diagnostics(port)
    for line in lines:
        print(line)
    return 0


def collect_diagnostics(port: int = 20777) -> list[str]:
    udp_status = can_bind_udp_port(port)
    return [
        f"{APP_NAME} {APP_VERSION}",
        f"Build mode: {'frozen executable' if is_frozen() else 'source'}",
        f"Executable: {executable_path()}",
        f"User data: {user_data_root()}",
        f"Python: {sys.version.replace(chr(10), ' ')}",
        f"Qt: {qVersion()}",
        f"PySide6: {pyside_version}",
        f"Telemetry sources: {', '.join(SOURCE_LABELS.values())}",
        f"UDP port {port}: {udp_status}",
        f"Assetto Corsa shared memory: {shared_pages_status(AC_MAP_NAMES)}",
        f"ACC shared memory: {shared_pages_status(ACC_MAP_NAMES)}",
    ]


def can_bind_udp_port(port: int) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("0.0.0.0", port))
    except OSError as error:
        return f"unavailable ({error})"
    return "available"


def shared_pages_status(map_names: dict[str, str]) -> str:
    available = [
        label for label, name in map_names.items() if shared_memory_page_available(name)
    ]
    if not available:
        return "not available"
    return "available: " + ", ".join(available)
