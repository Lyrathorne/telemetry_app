import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from models import TelemetrySample
from ui.graph_panel import (
    GraphPanel,
    auto_y_range,
    combined_metric_default_range,
    downsample_xy,
)
from ui.track_map_panel import trajectory_points


def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def sample(index: int) -> TelemetrySample:
    return TelemetrySample(
        timestamp=float(index) / 100.0,
        speed_kmh=float(index % 300),
        rpm=900 + index,
        gear=(index // 50) % 6,
        throttle_percent=float(index % 101),
        brake_percent=float((100 - index) % 101),
        steering=-50.0 + float(index % 101),
    )


class GraphPanelTests(unittest.TestCase):
    def setUp(self) -> None:
        app()

    def test_add_duplicate_remove_clear_and_reset_metrics(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        self.assertEqual(panel.selected_metrics(), ["speed_kmh"])
        self.assertFalse(panel.add_metric("speed_kmh"))
        self.assertTrue(panel.remove_metric("speed_kmh"))
        self.assertEqual(panel.selected_metrics(), [])
        self.assertTrue(panel.add_metric("throttle_percent"))
        self.assertTrue(panel.add_metric("brake_percent"))
        panel.clear_metrics()
        self.assertEqual(panel.selected_metrics(), [])
        panel.reset_default_metrics()
        self.assertEqual(panel.selected_metrics(), ["speed_kmh"])

    def test_metric_groups_prevent_unreadable_shared_axis(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        self.assertFalse(panel.add_metric("rpm"))
        panel.clear_metrics()
        self.assertTrue(panel.add_metric("throttle_percent"))
        self.assertTrue(panel.add_metric("brake_percent"))

    def test_default_y_ranges_are_physical(self) -> None:
        self.assertEqual(combined_metric_default_range(["throttle_percent"]), (0.0, 100.0))
        self.assertEqual(combined_metric_default_range(["brake_percent"]), (0.0, 100.0))
        self.assertEqual(combined_metric_default_range(["clutch_percent"]), (0.0, 100.0))
        self.assertEqual(combined_metric_default_range(["steering"]), (-1.0, 1.0))
        self.assertGreaterEqual(auto_y_range(["speed_kmh"], [], True)[0], 0.0)
        self.assertGreaterEqual(auto_y_range(["rpm"], [], True)[0], 0.0)

    def test_x_axis_session_time_starts_at_zero(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.add_sample(TelemetrySample(timestamp=1000.0, speed_kmh=10.0))
        panel.add_sample(TelemetrySample(timestamp=1001.0, speed_kmh=20.0))
        self.assertEqual(panel._sample_x(panel.samples[0]), 0.0)
        self.assertEqual(panel._sample_x(panel.samples[1]), 1.0)

    def test_x_axis_prefers_lap_distance_when_available(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.add_sample(TelemetrySample(timestamp=1000.0, lap_distance=12.5, speed_kmh=10.0))
        panel.add_sample(TelemetrySample(timestamp=1001.0, lap_distance=27.0, speed_kmh=20.0))
        self.assertEqual(panel._sample_x(panel.samples[0]), 12.5)
        self.assertEqual(panel._sample_x(panel.samples[1]), 27.0)
        self.assertTrue(panel._uses_distance_axis())

    def test_distance_axis_is_limited_to_track_length_and_latest_lap(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.add_sample(TelemetrySample(timestamp=1.0, lap_number=1, lap_distance=100.0, track_length_m=5000.0, speed_kmh=10.0))
        panel.add_sample(TelemetrySample(timestamp=2.0, lap_number=2, lap_distance=50.0, track_length_m=5000.0, speed_kmh=20.0))
        panel.add_sample(TelemetrySample(timestamp=3.0, lap_number=2, lap_distance=6000.0, track_length_m=5000.0, speed_kmh=30.0))
        visible_x, visible_indices = panel._visible_x_values()
        self.assertEqual(visible_x.tolist(), [50.0])
        self.assertEqual(visible_indices.tolist(), [1])

    def test_full_session_history_does_not_discard_old_samples(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        for index in range(1000):
            panel.add_sample(sample(index))
        self.assertEqual(panel.raw_sample_count(), 1000)
        panel.x_mode_combo.setCurrentIndex(panel.x_mode_combo.findData("full_session"))
        panel.refresh_plot()
        self.assertEqual(panel.raw_sample_count(), 1000)

    def test_recent_window_limits_visible_only(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        for index in range(1000):
            panel.add_sample(sample(index))
        panel.x_mode_combo.setCurrentIndex(panel.x_mode_combo.findData("recent_window"))
        panel.recent_window_seconds.setValue(1)
        panel.refresh_plot()
        self.assertEqual(panel.raw_sample_count(), 1000)
        self.assertLess(panel.visible_sample_count(), 1000)

    def test_follow_live_renders_visible_absolute_window_only(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        for index in range(1000):
            panel.add_sample(sample(index))
        original_x = panel._sample_x(panel.samples[500])
        panel.x_mode_combo.setCurrentIndex(panel.x_mode_combo.findData("follow_live"))
        panel.recent_window_seconds.setValue(1)
        visible_x, visible_indices = panel._visible_x_values()
        self.assertLess(len(visible_indices), panel.raw_sample_count())
        self.assertAlmostEqual(panel._sample_x(panel.samples[500]), original_x)
        self.assertGreaterEqual(float(visible_x[0]), panel.latest_sample_x - 1.0)

    def test_constant_pedal_y_range_is_stable(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.clear_metrics()
        panel.add_metric("throttle_percent")
        panel.add_metric("brake_percent")
        for index in range(500):
            panel.add_sample(TelemetrySample(timestamp=index / 60.0, throttle_percent=50.0, brake_percent=20.0))
        self.assertEqual(panel.y_mode_combo.currentData(), "metric_default")
        self.assertEqual(combined_metric_default_range(panel.selected_metrics()), (0.0, 100.0))

    def test_manual_range_validation(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.x_min.setValue(10.0)
        panel.x_max.setValue(5.0)
        self.assertEqual(panel.manual_x_range(), (None, None))
        panel.y_min.setValue(10.0)
        panel.y_max.setValue(5.0)
        self.assertEqual(panel.manual_y_range(), (None, None))

    def test_pause_resume_does_not_lose_samples(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.pause_button.setChecked(True)
        for index in range(200):
            panel.add_sample(sample(index))
        panel.refresh_plot()
        self.assertEqual(panel.raw_sample_count(), 200)
        self.assertIsNone(panel.latest_displayed_x)
        panel.pause_button.setChecked(False)
        self.assertIsNotNone(panel.latest_displayed_x)

    def test_downsampling_only_affects_rendering(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        for index in range(8000):
            panel.add_sample(sample(index))
        panel.refresh_plot()
        self.assertEqual(panel.raw_sample_count(), 8000)
        self.assertLessEqual(panel.diagnostics.rendered_points, 5000)

    def test_graph_uses_newest_available_sample_without_per_packet_redraw(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        for index in range(500):
            panel.add_sample(sample(index))
        self.assertEqual(panel.diagnostics.rendered_frames, 0)
        panel.refresh_plot()
        self.assertEqual(panel.diagnostics.rendered_frames, 1)
        self.assertAlmostEqual(panel.latest_displayed_x, panel.latest_sample_x)

    def test_no_new_plot_item_is_created_for_every_update(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        curve_ids = {metric: id(curve) for metric, curve in panel.curves.items()}
        for index in range(100):
            panel.add_sample(sample(index))
            panel.refresh_plot()
        self.assertEqual(curve_ids, {metric: id(curve) for metric, curve in panel.curves.items()})

    def test_settings_round_trip(self) -> None:
        panel = GraphPanel("Test", 50, 100)
        panel.clear_metrics()
        panel.add_metric("throttle_percent")
        panel.add_metric("brake_percent")
        panel.x_mode_combo.setCurrentIndex(panel.x_mode_combo.findData("recent_window"))
        panel.recent_window_seconds.setValue(60)
        state = panel.settings_state()

        restored = GraphPanel("Restored", 50, 100)
        restored.restore_settings_state(state)
        self.assertEqual(restored.selected_metrics(), ["throttle_percent", "brake_percent"])
        self.assertEqual(restored.x_mode_combo.currentData(), "recent_window")
        self.assertEqual(restored.recent_window_seconds.value(), 60)

    def test_trajectory_points_use_real_world_coordinates_only(self) -> None:
        points = trajectory_points(
            [
                TelemetrySample(world_position_x=1.0, world_position_z=2.0),
                TelemetrySample(speed_kmh=100.0),
                TelemetrySample(world_position_x=3.0, world_position_z=4.0),
            ]
        )
        self.assertEqual(points.tolist(), [[1.0, 2.0], [3.0, 4.0]])

    def test_trajectory_points_filter_non_finite_and_break_teleports(self) -> None:
        points = trajectory_points(
            [
                TelemetrySample(world_position_x=1.0, world_position_z=2.0),
                TelemetrySample(world_position_x=float("nan"), world_position_z=3.0),
                TelemetrySample(world_position_x=1000.0, world_position_z=1000.0),
            ]
        )
        self.assertEqual(points.shape, (3, 2))
        self.assertTrue(points[1, 0] != points[1, 0])
        self.assertEqual(points[-1].tolist(), [1000.0, 1000.0])


if __name__ == "__main__":
    unittest.main()
