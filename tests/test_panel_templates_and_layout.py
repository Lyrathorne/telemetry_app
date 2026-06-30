import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from app.settings import AppSettings
from models import TelemetrySample
from ui.graph_panel import GraphPanel
from ui.main_window import MainWindow


def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


class PanelTemplateAndLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        app()

    def test_panel_ids_and_object_names_are_unique(self) -> None:
        window = MainWindow(reset_layout=True)
        window.create_panel_from_template("pedals_graph")
        window.create_panel_from_template("speed_rpm_graph")
        ids = list(window.docks)
        object_names = [dock.objectName() for dock in window.docks.values()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(object_names), len(set(object_names)))
        window.close()

    def test_pedals_graph_template_configuration(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("pedals_graph")
        panel = window.docks[panel_id].widget()
        self.assertIsInstance(panel, GraphPanel)
        self.assertEqual(panel.selected_metrics(), ["throttle_percent", "brake_percent"])
        self.assertEqual(panel.y_mode_combo.currentData(), "metric_default")
        self.assertFalse(panel.settings_container.isVisible())
        window.close()

    def test_speed_rpm_template_uses_separate_graphs(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("speed_rpm_graph")
        graphs = window.docks[panel_id].widget().findChildren(GraphPanel)
        self.assertEqual([graph.selected_metrics() for graph in graphs], [["speed_kmh"], ["rpm"]])
        window.close()

    def test_live_values_handle_unavailable_metrics(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("live_values")
        window.handle_telemetry_sample(TelemetrySample(timestamp=1.0, speed_kmh=42.0, rpm=5000, gear=3))
        labels = window.live_value_panels[-1]
        self.assertEqual(labels["speed"].text(), "42 km/h")
        self.assertEqual(labels["clutch"].text(), "--")
        self.assertEqual(labels["delta"].text(), "Unavailable")
        self.assertIsNotNone(window.docks[panel_id])
        window.close()

    def test_singleton_template_is_not_duplicated(self) -> None:
        window = MainWindow(reset_layout=True)
        first = window.create_panel_from_template("lap_history")
        second = window.create_panel_from_template("lap_history")
        self.assertEqual(first, second)
        self.assertEqual(list(window.panel_templates.values()).count("lap_history"), 1)
        window.close()

    def test_dynamic_templates_get_unique_ids(self) -> None:
        window = MainWindow(reset_layout=True)
        first = window.create_panel_from_template("pedals_graph")
        second = window.create_panel_from_template("pedals_graph")
        self.assertNotEqual(first, second)
        window.close()

    def test_detached_and_docked_are_mutually_exclusive_and_opaque(self) -> None:
        window = MainWindow(reset_layout=True)
        dock_id = "source_status"
        window.detach_panel(dock_id)
        detached = window.detached_windows[dock_id]
        self.assertFalse(window.docks[dock_id].isVisible())
        self.assertEqual(window.windowOpacity(), 1.0)
        self.assertEqual(detached.windowOpacity(), 1.0)
        self.assertFalse(bool(detached.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)))
        window.dock_panel_back(dock_id)
        self.assertNotIn(dock_id, window.detached_windows)
        window.close()

    def test_layout_restore_docks_native_floating_widgets_once(self) -> None:
        window = MainWindow(reset_layout=True)
        window.comparison_dock.setFloating(True)
        data = window._layout_snapshot()
        restored = MainWindow(reset_layout=True)
        self.assertTrue(restored._restore_layout_data(data, notify=False))
        self.assertEqual(restored._layout_restore_count, 1)
        self.assertFalse(any(dock.isFloating() for dock in restored.docks.values()))
        window.close()
        restored.close()

    def test_invalid_layout_and_duplicate_ids_fall_back(self) -> None:
        window = MainWindow(reset_layout=True)
        data = window._layout_snapshot()
        data["panels"].append(dict(data["panels"][0]))
        self.assertFalse(window._restore_layout_data(data, notify=False))
        window.close()

    def test_reset_layout_skips_saved_dashboard_state(self) -> None:
        settings = AppSettings()
        settings.save_dashboard_layout({"schema_version": 999, "panels": []})
        settings.sync()
        window = MainWindow(reset_layout=True)
        self.assertIn("live_telemetry", window.docks)
        self.assertEqual(window._layout_restore_count, 0)
        window.close()

    def test_visible_button_can_receive_click(self) -> None:
        window = MainWindow(reset_layout=True)
        button = window.findChild(QPushButton, "")
        buttons = [item for item in window.findChildren(QPushButton) if item.isEnabled()]
        self.assertTrue(buttons)
        self.assertFalse(any(button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) for button in buttons))
        window.close()


if __name__ == "__main__":
    unittest.main()
