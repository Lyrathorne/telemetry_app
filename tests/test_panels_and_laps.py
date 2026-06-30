import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from models import LapResult, SectorResult, TelemetrySample
from telemetry.lap_comparison import (
    aligned_metric,
    assert_laps_comparable,
    common_position_grid,
    sector_marker_positions,
    time_delta,
)
from telemetry.lap_storage import LapStorage
from telemetry.lap_tracker import TimingState, LapTracker
from ui.main_window import MainWindow


def app() -> QApplication:
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def lap_sample(
    timestamp: float,
    lap_ms: int,
    position: float,
    completed_laps: int,
    sector: int = 0,
    invalid: bool = False,
    split_ms: int | None = None,
    last_sector_ms: int | None = None,
) -> TelemetrySample:
    return TelemetrySample(
        timestamp=timestamp,
        speed_kmh=100.0 + position * 100.0,
        rpm=5000,
        throttle_percent=70.0,
        brake_percent=0.0,
        source_name="ACC",
        car_name="Car",
        track_name="Track",
        lap_number=completed_laps + 1,
        lap_time=lap_ms / 1000.0,
        current_lap_time_ms=lap_ms,
        last_lap_time_ms=lap_ms if completed_laps else None,
        best_lap_time_ms=lap_ms if completed_laps else None,
        completed_laps=completed_laps,
        current_sector_index=sector,
        current_split_time_ms=split_ms,
        last_sector_time_ms=last_sector_ms,
        normalized_track_position=position,
        lap_distance=position * 5000.0,
        invalid_lap=invalid,
    )


def make_lap_with_sectors(times: list[int], complete: bool) -> LapResult:
    lap = LapResult(lap_number=1, lap_time_ms=sum(times) if complete else None, valid=True, complete=complete)
    lap.samples.append(TelemetrySample(current_lap_time_ms=sum(times), current_sector_index=len(times)))
    lap.sectors = [
        SectorResult(sector_number=index + 1, time_ms=time_ms, valid=True, timing_source="acc_split_derived")
        for index, time_ms in enumerate(times)
    ]
    return lap


class PanelAndLapTests(unittest.TestCase):
    def setUp(self) -> None:
        app()

    def test_detach_and_dock_preserves_graph_data_and_settings(self) -> None:
        window = MainWindow()
        panel = window.graph_panels[0]
        panel.add_sample(TelemetrySample(timestamp=1.0, speed_kmh=50.0))
        panel.set_settings_hidden(True)
        panel.settings_toggle_button.setChecked(True)
        dock_id = "graph_panel_1"

        window.detach_panel(dock_id)
        self.assertIn(dock_id, window.detached_windows)
        detached = window.detached_windows[dock_id]
        self.assertTrue(detached.windowFlags() & Qt.WindowType.Window)
        detached.showMaximized()
        self.assertTrue(detached.isMaximized())
        detached.showNormal()

        window.dock_panel_back(dock_id)
        self.assertNotIn(dock_id, window.detached_windows)
        self.assertEqual(panel.raw_sample_count(), 1)
        self.assertFalse(panel.settings_container.isVisible())
        window.close()

    def test_repeated_detach_dock_and_close_detached_window(self) -> None:
        window = MainWindow()
        dock_id = "source_status"
        for _ in range(3):
            window.detach_panel(dock_id)
            self.assertIn(dock_id, window.detached_windows)
            window.dock_panel_back(dock_id)
            self.assertNotIn(dock_id, window.detached_windows)
        window.detach_panel(dock_id)
        detached = window.detached_windows[dock_id]
        detached.close()
        self.assertIn(dock_id, window.detached_windows)
        window.recover_all_panels()
        self.assertNotIn(dock_id, window.detached_windows)
        self.assertIsNotNone(window.docks[dock_id].widget())
        window.close()

    def test_invalid_layout_recovery(self) -> None:
        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad-layout.json"
            path.write_text("{bad", encoding="utf-8")
            self.assertFalse(window._read_layout(path))
        window.close()

    def test_lap_starts_completes_once_and_saves(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            tracker = LapTracker(storage)
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.00, 0, 0))
            tracker.process_sample(lap_sample(20.0, 20000, 0.33, 0, 1))
            tracker.process_sample(lap_sample(40.0, 40000, 0.66, 0, 2))
            completed = tracker.process_sample(lap_sample(60.0, 60000, 0.01, 1, 0))
            duplicate = tracker.process_sample(lap_sample(60.1, 60100, 0.02, 1, 0))

            self.assertIsNotNone(completed)
            self.assertIsNone(duplicate)
            self.assertEqual(len(tracker.completed_laps), 1)
            self.assertEqual(completed.lap_time_ms, 60000)
            self.assertEqual([sector.time_ms for sector in completed.sectors[:3]], [20000, 20000, 20000])
            loaded = storage.load_laps()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].lap_time_ms, 60000)

    def test_acc_like_sector_splits_with_last_lap_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            tracker = LapTracker(storage)
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(31.284, 31284, 0.30, 0, 1, split_ms=31284, last_sector_ms=31284))
            tracker.process_sample(lap_sample(74.135, 74135, 0.68, 0, 2, split_ms=74135, last_sector_ms=42851))
            finish = lap_sample(106.942, 100, 0.01, 1, 0, last_sector_ms=32807)
            finish.last_lap_time_ms = 106942
            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed)
            self.assertEqual([sector.time_ms for sector in completed.sectors], [31284, 42851, 32807])
            self.assertEqual([sector.timing_source for sector in completed.sectors], ["acc_direct_sector"] * 3)
            loaded = storage.load_laps()
            self.assertEqual([sector.time_ms for sector in loaded[0].sectors], [31284, 42851, 32807])
            self.assertEqual(loaded[0].sectors[2].timing_source, "acc_direct_sector")

    def test_observed_cumulative_splits_are_converted_to_individual_sector_durations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "nurburgring_24h", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(59.617, 59617, 0.32, 0, 1, split_ms=59617, last_sector_ms=59617))
            tracker.process_sample(lap_sample(105.730, 105730, 0.67, 0, 2, split_ms=105730, last_sector_ms=105730))
            finish = lap_sample(130.405, 100, 0.01, 1, 0, last_sector_ms=130405)
            finish.last_lap_time_ms = 130405

            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed)
            sector_times = [sector.time_ms for sector in completed.sectors[:3]]
            self.assertEqual(sector_times, [59617, 46113, 24675])
            self.assertNotEqual(sector_times[1], 105730)
            self.assertEqual(sum(sector_times), completed.lap_time_ms)
            self.assertEqual(
                [sector.timing_source for sector in completed.sectors[:3]],
                ["acc_direct_sector", "acc_cumulative_split", "sector_transition_derived"],
            )

    def test_second_observed_cumulative_split_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "nurburgring_24h", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(67.420, 67420, 0.32, 0, 1, split_ms=67420))
            tracker.process_sample(lap_sample(115.162, 115162, 0.67, 0, 2, split_ms=115162))
            finish = lap_sample(146.020, 100, 0.01, 1, 0)
            finish.last_lap_time_ms = 146020

            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed)
            sector_times = [sector.time_ms for sector in completed.sectors[:3]]
            self.assertEqual(sector_times, [67420, 47742, 30858])
            self.assertEqual(sum(sector_times), 146020)

    def test_sector_boundaries_do_not_use_packet_delta_or_next_lap_reset_timer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.000, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(40.200, 40200, 0.31, 0, 0))
            tracker.process_sample(lap_sample(40.220, 40220, 0.32, 0, 1, split_ms=20))
            tracker.process_sample(lap_sample(83.520, 83520, 0.65, 0, 1))
            tracker.process_sample(lap_sample(83.540, 83540, 0.66, 0, 2, split_ms=22))
            finish = lap_sample(125.012, 2, 0.01, 1, 0)
            finish.last_lap_time_ms = 125012

            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed)
            sector_times = [sector.time_ms for sector in completed.sectors[:3]]
            self.assertEqual(sector_times, [40220, 43320, 41472])
            self.assertNotIn(20, sector_times)
            self.assertNotIn(2, sector_times)
            self.assertEqual(sum(sector_times), 125012)
            self.assertEqual(completed.telemetry_series.sample_count, 6)
            self.assertEqual(completed.telemetry_series.sector_boundary_elapsed_s, [40.22, 83.54])

    def test_current_lap_graph_resets_after_confirmed_completion_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(40.0, 40000, 0.3, 0, 1))
            self.assertEqual(tracker.current_lap_graph.sample_count, 2)
            finish = lap_sample(100.0, 2, 0.01, 1, 0)
            finish.last_lap_time_ms = 100000
            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed.telemetry_series)
            self.assertEqual(completed.telemetry_series.sample_count, 3)
            self.assertEqual(tracker.current_lap_graph.sample_count, 0)
            tracker.process_sample(lap_sample(100.1, 100, 0.01, 1, 0))
            self.assertEqual(tracker.current_lap_graph.sample_count, 1)

    def test_sector_statuses_recalculate_when_new_purple_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            for sample in [
                lap_sample(0.0, 0, 0.0, 0, 0),
                lap_sample(40.0, 40000, 0.3, 0, 1),
                lap_sample(80.0, 80000, 0.6, 0, 2),
            ]:
                tracker.process_sample(sample)
            finish_1 = lap_sample(120.0, 2, 0.01, 1, 0)
            finish_1.last_lap_time_ms = 120000
            lap_1 = tracker.process_sample(finish_1)
            tracker.process_sample(lap_sample(120.1, 100, 0.01, 1, 0))
            tracker.process_sample(lap_sample(158.0, 38000, 0.3, 1, 1))
            tracker.process_sample(lap_sample(196.0, 76000, 0.6, 1, 2))
            finish_2 = lap_sample(234.0, 2, 0.01, 2, 0)
            finish_2.last_lap_time_ms = 114000
            lap_2 = tracker.process_sample(finish_2)

            self.assertEqual(lap_2.sectors[0].comparison_status, "PURPLE")
            self.assertEqual(lap_1.sectors[0].comparison_status, "YELLOW")
            self.assertEqual(lap_2.sectors[1].comparison_status, "PURPLE")
            self.assertEqual(lap_2.sectors[2].comparison_status, "PURPLE")

    def test_invalid_cumulative_sector_order_keeps_lap_but_rejects_sectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(59.0, 59000, 0.3, 0, 1, split_ms=59000))
            tracker.process_sample(lap_sample(58.0, 58000, 0.6, 0, 2, split_ms=58000))
            finish = lap_sample(120.0, 100, 0.01, 1, 0)
            finish.last_lap_time_ms = 120000

            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed)
            self.assertEqual(completed.lap_time_ms, 120000)
            self.assertEqual(len(tracker.completed_laps), 1)
            self.assertTrue(any(sector.time_ms is None for sector in completed.sectors))
            self.assertIn("Sector timing inconsistent", completed.notes)

    def test_duplicate_sector_transition_does_not_duplicate_sector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(31.284, 31284, 0.30, 0, 1, split_ms=31284))
            tracker.process_sample(lap_sample(31.300, 31300, 0.31, 0, 1, split_ms=31284))
            self.assertEqual(len(tracker.active_lap.sectors), 1)

    def test_partial_first_lap_does_not_fabricate_previous_sector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(50.0, 50000, 0.50, 0, 1))
            tracker.process_sample(lap_sample(74.135, 74135, 0.68, 0, 2))
            self.assertEqual([sector.sector_number for sector in tracker.active_lap.sectors], [2])
            self.assertIsNone(tracker.active_lap.sectors[0].time_ms)
            self.assertEqual(tracker.active_lap.notes, "Started mid-lap")
            self.assertEqual(tracker.timing_state, TimingState.PARTIAL_LAP)

    def test_first_finish_after_mid_lap_connection_starts_full_lap_without_saving_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            tracker = LapTracker(storage)
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(50.0, 50000, 0.50, 0, 1))
            finish = lap_sample(90.0, 100, 0.01, 1, 0, last_sector_ms=30000)
            finish.last_lap_time_ms = 90000
            result = tracker.process_sample(finish)

            self.assertIsNone(result)
            self.assertEqual(len(tracker.completed_laps), 0)
            self.assertEqual(len(storage.load_laps()), 0)
            self.assertIsNotNone(tracker.active_lap)
            self.assertEqual(tracker.timing_state, TimingState.TRACKING_LAP)

    def test_completed_lap_enters_memory_before_persistence_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            tracker = LapTracker(storage)
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(31.0, 31000, 0.3, 0, 1))
            tracker.process_sample(lap_sample(70.0, 70000, 0.7, 0, 2))
            finish = lap_sample(100.0, 100, 0.01, 1, 0)
            finish.last_lap_time_ms = 100000

            completed = tracker.process_sample(finish)

            self.assertIsNotNone(completed)
            self.assertIs(tracker.repository.get_lap(completed.id), completed)
            self.assertEqual(tracker.repository.get_session_laps(tracker.session_id), [completed])
            self.assertEqual(tracker.storage_status.completed_laps_in_memory, 1)
            self.assertEqual(tracker.storage_status.last_save_result, "Lap saved successfully")

    def test_automatic_lap_summary_persists_with_raw_recording_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            tracker = LapTracker(storage)
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            tracker.process_sample(lap_sample(30.0, 30000, 0.3, 0, 1, split_ms=30000))
            tracker.process_sample(lap_sample(70.0, 70000, 0.7, 0, 2, split_ms=70000))
            finish = lap_sample(100.0, 100, 0.01, 1, 0)
            finish.last_lap_time_ms = 100000

            completed = tracker.process_sample(finish)
            loaded = storage.load_laps()

            self.assertIsNotNone(completed)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].lap_time_ms, 100000)
            self.assertFalse(loaded[0].raw_samples_recorded)
            self.assertEqual(loaded[0].samples, [])

    def test_pit_sample_does_not_create_completed_lap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, 0))
            pit_sample = lap_sample(20.0, 20000, 0.25, 0, 0)
            pit_sample.in_pit = True
            result = tracker.process_sample(pit_sample)

            self.assertIsNone(result)
            self.assertEqual(tracker.timing_state, TimingState.IN_PITS)
            self.assertEqual(len(tracker.completed_laps), 0)

    def test_session_counter_reset_is_not_lap_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 2, 0))
            result = tracker.process_sample(lap_sample(1.0, 1000, 0.01, 0, 0))

            self.assertIsNone(result)
            self.assertEqual(len(tracker.completed_laps), 0)
            self.assertEqual(tracker.timing_state, TimingState.WAITING_FOR_SESSION)

    def test_timing_tracker_is_alive_without_sector_panel(self) -> None:
        window = MainWindow(reset_layout=True)
        initial_tables = len(window.sector_timing_tables)
        sample = lap_sample(0.0, 0, 0.0, 0, 0)
        window.lap_tracker.start_session("ACC", "Track", "Car")
        window.lap_tracker.process_sample(sample)

        self.assertEqual(len(window.sector_timing_tables), initial_tables)
        self.assertIsNotNone(window.lap_tracker.active_lap)
        self.assertEqual(window.lap_tracker.timing_state, TimingState.TRACKING_LAP)
        window.close()

    def test_current_lap_graph_panel_is_separate_from_continuous_graphs(self) -> None:
        window = MainWindow(reset_layout=True)
        continuous_count = len(window.graph_panels)
        panel_id = window.create_panel_from_template("current_lap_graph")
        self.assertIsNotNone(panel_id)
        self.assertEqual(len(window.graph_panels), continuous_count)
        self.assertEqual(len(window.current_lap_graph_panels), 1)
        lap = make_lap_with_sectors([10000], complete=False)
        lap.samples = [lap_sample(0.0, 0, 0.0, 0, 0), lap_sample(1.0, 1000, 0.01, 0, 0)]
        window.handle_lap_updated(lap)
        self.assertEqual(window.current_lap_graph_panels[0].raw_sample_count(), 2)
        window.close()

    def test_saved_lap_graph_opens_from_in_memory_completed_lap(self) -> None:
        window = MainWindow(reset_layout=True)
        lap = make_lap_with_sectors([40220, 43320, 41472], complete=True)
        lap.samples = [
            lap_sample(0.0, 0, 0.0, 0, 0),
            lap_sample(40.22, 40220, 0.32, 0, 1),
            lap_sample(83.54, 83540, 0.66, 0, 2),
        ]
        panel = window.open_lap_graph(lap)
        self.assertIsNotNone(panel)
        self.assertEqual(panel.raw_sample_count(), 3)
        window.handle_telemetry_sample(lap_sample(200.0, 2000, 0.02, 1, 0))
        self.assertEqual(panel.raw_sample_count(), 3)
        window.close()

    def test_live_lap_table_updates_sector_cells(self) -> None:
        window = MainWindow(reset_layout=True)
        window.create_panel_from_template("live_lap_timing")
        window.handle_lap_updated(make_lap_with_sectors([31284, 42851], complete=False))
        table = window.live_lap_tables[-1]
        self.assertEqual(table.item(0, 2).text(), "00:31.284")
        self.assertEqual(table.item(0, 3).text(), "00:42.851")
        self.assertEqual(table.item(0, 4).text(), "—")
        window.close()

    def test_invalid_lap_and_incomplete_stop_are_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            tracker = LapTracker(storage)
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.0, 0, invalid=True))
            tracker.stop_session()
            laps = storage.load_laps()
            self.assertEqual(len(laps), 1)
            self.assertFalse(laps[0].valid)
            self.assertFalse(laps[0].complete)

    def test_reconnect_short_wrap_does_not_create_false_lap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            tracker.process_sample(lap_sample(0.0, 0, 0.98, 0))
            result = tracker.process_sample(lap_sample(1.0, 1000, 0.01, 0))
            self.assertIsNone(result)
            self.assertEqual(len(tracker.completed_laps), 0)

    def test_lap_comparison_alignment_delta_and_track_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = LapTracker(LapStorage(Path(tmpdir) / "laps.sqlite3"))
            tracker.start_session("ACC", "Track", "Car")
            for item in [
                lap_sample(0.0, 0, 0.0, 0),
                lap_sample(30.0, 30000, 0.5, 0),
                lap_sample(60.0, 60000, 1.0, 0),
                lap_sample(60.1, 60000, 0.01, 1),
            ]:
                tracker.process_sample(item)
            lap = tracker.completed_laps[0]
            other = tracker.completed_laps[0]
            grid = common_position_grid([lap, other], points=10)
            self.assertEqual(grid.size, 10)
            self.assertEqual(aligned_metric(lap, "speed_kmh", grid).size, 10)
            delta_x, delta_y = time_delta(lap, other, points=10)
            self.assertEqual(delta_x.size, 10)
            self.assertEqual(delta_y.size, 10)
            self.assertTrue(sector_marker_positions(lap))
            other_track_lap = deepcopy(lap)
            other_track_lap.track = "Other"
            with self.assertRaises(ValueError):
                assert_laps_comparable([tracker.completed_laps[0], other_track_lap])


if __name__ == "__main__":
    unittest.main()
