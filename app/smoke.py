from __future__ import annotations

from app.paths import data_dir, ensure_user_directories, logs_dir, resource_path
from telemetry import SOURCE_TYPES
from telemetry.lap_storage import LapStorage


def run_smoke_test() -> int:
    ensure_user_directories()
    if not logs_dir().is_dir() or not data_dir().is_dir():
        raise RuntimeError("User data directories were not created.")
    if not resource_path("resources").exists():
        raise RuntimeError(f"Bundled resources directory was not found: {resource_path('resources')}")

    for source_id, source_class in SOURCE_TYPES.items():
        source = source_class()
        try:
            if source.is_running():
                raise RuntimeError(f"Telemetry source started during construction: {source_id}")
        finally:
            source.stop()

    storage = LapStorage()
    storage.load_laps()
    storage.load_session_summaries()

    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(reset_layout=True)
    try:
        if window.windowTitle() != "Racing Telemetry":
            raise RuntimeError("Main window title did not initialize.")
        for panel_id in ("imported_sessions", "comparison_graphs", "laps"):
            if panel_id not in window.panel_registry.ids():
                raise RuntimeError(f"Expected panel is not registered: {panel_id}")
        window.show()
        app.processEvents()
    finally:
        window.close()
        app.processEvents()
    return 0
