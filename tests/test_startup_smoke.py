import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app import paths
from telemetry import SOURCE_TYPES
from telemetry.base import SourceState
from telemetry.f1_2018 import F12018TelemetrySource
from ui.main_window import MainWindow


def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


class StartupSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        app()

    def test_important_modules_import(self) -> None:
        import main
        import telemetry.assetto_corsa
        import telemetry.assetto_corsa_competizione
        import telemetry.demo
        import telemetry.f1_2018
        import ui.main_window

        self.assertTrue(callable(main.main))

    def test_source_objects_do_not_connect_during_construction(self) -> None:
        for source_class in SOURCE_TYPES.values():
            source = source_class()
            self.assertFalse(source.is_running())
            self.assertEqual(source.state(), SourceState.STOPPED)
            source.stop()
            source.stop()

    def test_main_window_can_be_instantiated_offscreen(self) -> None:
        window = MainWindow()
        self.assertEqual(window.windowTitle(), "Racing Telemetry")
        window.close()

    def test_f1_udp_bind_failure_sets_error_state(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as blocker:
            blocker.bind(("0.0.0.0", 0))
            port = blocker.getsockname()[1]
            source = F12018TelemetrySource(port=port)
            errors: list[str] = []
            source.error_occurred.connect(errors.append)

            source.start()

            self.assertFalse(source.is_running())
            self.assertEqual(source.state(), SourceState.ERROR)
            self.assertTrue(errors)
            source.stop()
            source.stop()

    def test_resource_paths_work_in_source_and_frozen_modes(self) -> None:
        source_path = paths.resource_path("main.py")
        self.assertEqual(source_path.name, "main.py")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sys, "frozen", True, create=True), patch.object(
                sys, "_MEIPASS", tmpdir, create=True
            ):
                self.assertEqual(paths.bundle_root(), Path(tmpdir).resolve())
                self.assertEqual(paths.resource_path("resources").parent, Path(tmpdir).resolve())

    def test_application_data_directories_can_be_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmpdir}):
                paths.ensure_user_directories()
                self.assertTrue(paths.logs_dir().is_dir())
                self.assertTrue(paths.data_dir().is_dir())
                self.assertTrue(paths.exports_dir().is_dir())
                self.assertTrue(paths.settings_dir().is_dir())


if __name__ == "__main__":
    unittest.main()
