import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.settings import AppSettings
from models import TelemetrySample, TelemetrySession
from telemetry.comparison import build_comparison_series, preferred_axis
from telemetry.importer import (
    TelemetryImportError,
    detect_delimiter,
    import_csv,
    import_json,
    map_columns,
)
from ui.main_window import MainWindow


def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


class DashboardFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        app()

    def test_udp_setting_persistence_and_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"LOCALAPPDATA": tmpdir}):
                settings = AppSettings()
                settings.set_f1_udp_port(22222)
                settings.sync()
                self.assertEqual(AppSettings().f1_udp_port(), 22222)
                settings.set_f1_udp_port(999999)
                self.assertEqual(settings.f1_udp_port(), 65535)

    def test_udp_field_enabled_only_for_f1(self) -> None:
        window = MainWindow()
        window.select_source("f1_2018")
        self.assertTrue(window.port_input.isEnabled())
        window.select_source("assetto_corsa")
        self.assertFalse(window.port_input.isEnabled())
        window.close()

    def test_fullscreen_toggle_and_escape(self) -> None:
        window = MainWindow()
        window.toggle_fullscreen(True)
        self.assertTrue(window.isFullScreen())
        event = type("Event", (), {"key": lambda self: Qt.Key.Key_Escape, "accept": lambda self: None})()
        window.keyPressEvent(event)
        self.assertFalse(window.isFullScreen())
        window.close()

    def test_dock_object_names_and_layout_restore(self) -> None:
        window = MainWindow()
        names = {dock.objectName() for dock in window.docks.values()}
        self.assertIn("live_telemetry", names)
        self.assertIn("source_status", names)
        self.assertIn("connection_diagnostics", names)
        state = window.saveState()
        window.reset_layout()
        self.assertTrue(window.restoreState(state))
        window.close()

    def test_multiple_graph_panels_share_samples(self) -> None:
        window = MainWindow()
        window.add_graph_panel()
        sample = TelemetrySample(timestamp=1.0, speed_kmh=100.0, rpm=5000, gear=3)
        window.handle_telemetry_sample(sample)
        self.assertTrue(all(len(panel.samples) == 1 for panel in window.graph_panels))
        dock = window.docks[f"graph_panel_{window.graph_counter}"]
        dock.close()
        window.handle_telemetry_sample(sample)
        self.assertIsNone(window.active_source)
        window.close()

    def test_csv_import_aliases_units_and_optional_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "simple.csv"
            path.write_text("time;speed_mph;rpm;accelerator;brake\n0;60;5000;0.5;25\n", encoding="utf-8")
            session = import_csv(path)
            self.assertAlmostEqual(session.samples[0].speed_kmh, 96.56064)
            self.assertEqual(session.samples[0].throttle_percent, 50.0)
            self.assertEqual(session.samples[0].brake_percent, 25.0)

    def test_csv_helpers_and_invalid_files(self) -> None:
        self.assertEqual(detect_delimiter("time,speed\n0,1\n"), ",")
        self.assertEqual(map_columns(["time", "speed", "steer"])["steer"], "steering")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.csv"
            path.write_text("name,value\nx,y\n", encoding="utf-8")
            with self.assertRaises(TelemetryImportError):
                import_csv(path)

    def test_json_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            path.write_text(json.dumps({"samples": [{"timestamp": 0, "speed_kmh": 1, "rpm": 2}]}), encoding="utf-8")
            session = import_json(path)
            self.assertEqual(session.sample_count, 1)

    def test_comparison_by_time_and_lap_distance(self) -> None:
        a = TelemetrySession(track="Track", samples=[
            TelemetrySample(timestamp=10.0, lap_distance=0.0, speed_kmh=10.0),
            TelemetrySample(timestamp=11.0, lap_distance=5.0, speed_kmh=20.0),
        ])
        b = TelemetrySession(track="Track", samples=[
            TelemetrySample(timestamp=20.0, lap_distance=0.0, speed_kmh=12.0),
            TelemetrySample(timestamp=21.0, lap_distance=5.0, speed_kmh=22.0),
        ])
        self.assertEqual(preferred_axis([a, b]), "lap_distance")
        series = build_comparison_series([a, b], "speed_kmh")
        self.assertEqual(len(series), 2)
        c = TelemetrySession(track="Other", samples=[TelemetrySample(timestamp=0.0, speed_kmh=1.0)])
        with self.assertRaises(ValueError):
            build_comparison_series([a, c], "speed_kmh")

    def test_recording_and_save_preview(self) -> None:
        window = MainWindow()
        window.start_recording()
        window.handle_telemetry_sample(TelemetrySample(timestamp=1.0, speed_kmh=50.0))
        self.assertEqual(len(window.recording_samples), 1)
        session = window._recorded_session_preview()
        self.assertEqual(session.sample_count, 1)
        window.stop_recording()
        window.close()


if __name__ == "__main__":
    unittest.main()
