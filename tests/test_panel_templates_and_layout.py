import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from app.settings import AppSettings
from models import LapResult, SectorResult, TelemetrySample
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
        panel = window.panel_widgets[panel_id]
        self.assertIsInstance(panel, GraphPanel)
        self.assertEqual(panel.selected_metrics(), ["throttle_percent", "brake_percent"])
        self.assertEqual(panel.y_mode_combo.currentData(), "metric_default")
        self.assertFalse(panel.settings_container.isVisible())
        window.close()

    def test_speed_rpm_template_uses_separate_graphs(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("speed_rpm_graph")
        graphs = window.panel_widgets[panel_id].findChildren(GraphPanel)
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
        self.assertIn(panel_id, window.dashboard_workspace.panel_to_tile)
        window.close()

    def test_singleton_template_is_not_duplicated(self) -> None:
        window = MainWindow(reset_layout=True)
        first = window.create_panel_from_template("lap_history")
        second = window.create_panel_from_template("lap_history")
        self.assertEqual(first, second)
        self.assertEqual(list(window.panel_templates.values()).count("lap_history"), 1)
        window.close()

    def test_session_history_template_is_available(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("session_history")
        self.assertIsNotNone(panel_id)
        self.assertEqual(list(window.panel_templates.values()).count("session_history"), 1)
        self.assertTrue(window.session_history_tables)
        window.close()

    def test_session_history_headers_are_not_repeated_as_rows(self) -> None:
        window = MainWindow(reset_layout=True)
        window.create_panel_from_template("session_history")
        summary_table, detail_table = window.session_history_tables[-1]
        summary_headers = [summary_table.horizontalHeaderItem(column).text() for column in range(summary_table.columnCount())]
        detail_headers = [detail_table.horizontalHeaderItem(column).text() for column in range(detail_table.columnCount())]

        self.assertEqual(summary_headers, ["Track", "Car", "Game", "Date/time", "Best lap", "Laps"])
        self.assertEqual(detail_headers, ["Lap", "Lap time", "S1", "S2", "S3", "Delta", "Valid", "Notes"])
        for table, headers in ((summary_table, summary_headers), (detail_table, detail_headers)):
            for row in range(table.rowCount()):
                values = [table.item(row, column).text() for column in range(table.columnCount()) if table.item(row, column)]
                self.assertNotEqual(values, headers)
        window.close()

    def test_telemetry_source_is_dock_not_central_header(self) -> None:
        window = MainWindow(reset_layout=True)
        self.assertIn("telemetry_source", window.docks)
        self.assertIs(window.docks["telemetry_source"].widget(), window.source_combo.parentWidget().parentWidget())
        self.assertNotEqual(window.centralWidget(), window.docks["telemetry_source"].widget())
        window.close()

    def test_completed_lap_pedal_overlay_panel_opens(self) -> None:
        window = MainWindow(reset_layout=True)
        lap = LapResult(
            lap_number=7,
            lap_time_ms=100000,
            valid=True,
            complete=True,
            sectors=[
                SectorResult(1, time_ms=30000),
                SectorResult(2, time_ms=30000),
                SectorResult(3, time_ms=40000),
            ],
        )
        for index, progress in enumerate((0.0, 0.5, 1.0)):
            lap.samples.append(
                TelemetrySample(
                    timestamp=float(index),
                    current_lap_time_ms=index * 50000,
                    normalized_track_position=progress,
                    throttle_percent=80.0,
                    brake_percent=10.0 * index,
                )
            )

        widget = window.open_lap_pedal_overlay(lap)

        self.assertIsNotNone(widget)
        self.assertIn(widget, window.panel_widgets.values())
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
        buttons = [item for item in window.findChildren(QPushButton) if item.isEnabled()]
        self.assertTrue(buttons)
        self.assertFalse(any(button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) for button in buttons))
        window.close()

    def test_workspace_tile_can_split_horizontally_and_vertically(self) -> None:
        window = MainWindow(reset_layout=True)
        first_tile = window.dashboard_workspace.first_tile().tile_id
        second = window.dashboard_workspace.split_tile(first_tile, "right")
        third = window.dashboard_workspace.split_tile(second, "below")
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        self.assertEqual(window.dashboard_workspace.tile_count(), 3)
        window.close()

    def test_workspace_split_is_local_and_preserves_existing_panel(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("pedals_graph")
        original_tile = window.dashboard_workspace.panel_to_tile[panel_id]
        window.dashboard_workspace.split_tile(original_tile, "below")
        self.assertEqual(window.dashboard_workspace.panel_to_tile[panel_id], original_tile)
        self.assertEqual(window.dashboard_workspace.tile_count(), 2)
        window.close()

    def test_panel_moves_between_tiles_without_duplication(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("live_values")
        target = window.dashboard_workspace.split_tile(window.dashboard_workspace.panel_to_tile[panel_id], "right")
        self.assertTrue(window.dashboard_workspace.move_panel(panel_id, target))
        occurrences = sum(panel_id in tile.panel_ids() for tile in window.dashboard_workspace.tiles.values())
        self.assertEqual(occurrences, 1)
        window.close()

    def test_center_tab_group_adds_second_panel_to_same_tile(self) -> None:
        window = MainWindow(reset_layout=True)
        first = window.create_panel_from_template("live_values")
        tile_id = window.dashboard_workspace.panel_to_tile[first]
        second = window.create_panel_from_template("sector_timing", tile_id=tile_id)
        self.assertEqual(window.dashboard_workspace.panel_to_tile[first], window.dashboard_workspace.panel_to_tile[second])
        self.assertEqual(len(window.dashboard_workspace.tiles[tile_id].panel_ids()), 2)
        window.close()

    def test_quick_grid_and_ten_panels(self) -> None:
        window = MainWindow(reset_layout=True)
        window.apply_quick_grid(3, 3)
        created = [window.create_panel_from_template("pedals_graph") for _ in range(4)]
        created += [window.create_panel_from_template("live_values") for _ in range(2)]
        created += [window.create_panel_from_template("sector_timing") for _ in range(3)]
        created.append(window.create_panel_from_template("speed_rpm_graph"))
        self.assertEqual(window.dashboard_workspace.tile_count(), 9)
        self.assertEqual(len([item for item in created if item]), 10)
        self.assertEqual(len(set(window.panel_registry.ids())), len(window.panel_registry.ids()))
        window.close()

    def test_workspace_layout_snapshot_restores_splitters_and_ratios(self) -> None:
        window = MainWindow(reset_layout=True)
        window.apply_quick_grid(2, 2)
        panel_id = window.create_panel_from_template("pedals_graph")
        snapshot = window._layout_snapshot()
        restored = MainWindow(reset_layout=True)
        self.assertTrue(restored._restore_layout_data(snapshot, notify=False))
        self.assertEqual(restored.dashboard_workspace.tile_count(), 4)
        self.assertIn(panel_id, restored.dashboard_workspace.panel_to_tile)
        self.assertIn("workspace", restored._layout_snapshot())
        window.close()
        restored.close()

    def test_detached_workspace_panel_returns_to_small_tile(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("pedals_graph")
        tile_id = window.dashboard_workspace.panel_to_tile[panel_id]
        target = window.dashboard_workspace.split_tile(tile_id, "right")
        window.detach_panel(panel_id)
        self.assertIn(panel_id, window.detached_windows)
        window.dock_panel_back(panel_id, tile_id=target)
        self.assertNotIn(panel_id, window.detached_windows)
        self.assertEqual(window.dashboard_workspace.panel_to_tile[panel_id], target)
        window.close()

    def test_compact_mode_reduces_graph_minimum_size(self) -> None:
        window = MainWindow(reset_layout=True)
        panel_id = window.create_panel_from_template("pedals_graph")
        panel = window.panel_widgets[panel_id]
        old_size = panel.plot_widget.minimumSize()
        window.set_panel_compact_mode(panel_id, True)
        self.assertLessEqual(panel.plot_widget.minimumSize().height(), old_size.height())
        self.assertTrue(panel.property("compact_mode"))
        window.close()


if __name__ == "__main__":
    unittest.main()
