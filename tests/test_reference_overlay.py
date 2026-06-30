import json
import tempfile
import unittest
from pathlib import Path

from models import LapResult, ReferenceLap, SectorResult, TelemetryPoint, TelemetrySample
from telemetry.lap_delta import completed_lap_delta_ms, format_delta_ms
from telemetry.lap_storage import LapStorage
from telemetry.reference_importer import import_reference_lap
from telemetry.sector_feedback import sector_feedback
from telemetry.telemetry_overlay import build_lap_overlay


def lap_with_samples() -> LapResult:
    lap = LapResult(
        lap_number=1,
        lap_time_ms=100000,
        valid=True,
        complete=True,
        game="ACC",
        track="monza",
        car="porsche_911_gt3",
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
                speed_kmh=100.0 + index * 50,
                throttle_percent=80.0,
                brake_percent=0.0 if index < 2 else 20.0,
                steering=0.1 * index,
                gear=3 + index,
                rpm=7000 + index * 500,
            )
        )
    return lap


class ReferenceOverlayTests(unittest.TestCase):
    def test_reference_json_import_save_and_best_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "reference.json"
            path.write_text(
                json.dumps(
                    {
                        "game": "ACC",
                        "track": "monza",
                        "car": "porsche_911_gt3",
                        "lap_time": 99.5,
                        "source": "web",
                        "player_name": "Reference Driver",
                        "telemetry_points": [
                            {"lap_progress": 0.0, "speed": 100, "throttle": 1.0, "brake": 0.0},
                            {"lap_progress": 0.5, "speed": 150, "throttle": 0.7, "brake": 0.1},
                            {"lap_progress": 1.0, "speed": 120, "throttle": 0.9, "brake": 0.0},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reference = import_reference_lap(path)
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            storage.save_reference_lap(reference)

            loaded = storage.best_reference_lap("ACC", "monza", "porsche_911_gt3")

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.player_name, "Reference Driver")
            self.assertEqual(loaded.lap_time_ms, 99500)
            self.assertEqual(len(loaded.telemetry_points), 3)

    def test_racing_time_string_import_is_minutes_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "reference.json"
            path.write_text(
                json.dumps(
                    {
                        "game": "ACC",
                        "track": "monza",
                        "car": "porsche_911_gt3",
                        "lap_time": "2:08.000",
                        "telemetry_points": [
                            {"lap_progress": 0.0, "speed": 100},
                            {"lap_progress": 1.0, "speed": 120},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reference = import_reference_lap(path)
            own = LapResult(lap_time_ms=129000, valid=True, complete=True, track="monza", car="porsche_911_gt3")
            best = LapResult(lap_time_ms=128000, valid=True, complete=True, track="monza", car="porsche_911_gt3")

            self.assertEqual(reference.lap_time_ms, 128000)
            self.assertEqual(format_delta_ms(completed_lap_delta_ms(own, [own, best])), "+1.000")

    def test_reference_matching_requires_same_track_and_car(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            storage.save_reference_lap(
                ReferenceLap(
                    game="ACC",
                    track_id="monza",
                    car_id="porsche_911_gt3",
                    lap_time_ms=100000,
                    telemetry_points=[TelemetryPoint(lap_progress=0.0), TelemetryPoint(lap_progress=1.0)],
                )
            )

            self.assertIsNotNone(storage.best_reference_lap("ACC", "monza", "porsche_911_gt3"))
            self.assertIsNone(storage.best_reference_lap("ACC", "monza", "bmw_m4_gt3"))
            self.assertIsNone(storage.best_reference_lap("ACC", "spa", "porsche_911_gt3"))

    def test_reference_with_lap_time_only_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            storage.save_reference_lap(
                ReferenceLap(game="ACC", track_id="monza", car_id="porsche_911_gt3", lap_time_ms=100000)
            )

            reference = storage.best_reference_lap("ACC", "monza", "porsche_911_gt3")

            self.assertIsNotNone(reference)
            self.assertEqual(reference.telemetry_points, [])

    def test_overlay_aligns_two_laps_by_progress(self) -> None:
        main = lap_with_samples()
        comparison = lap_with_samples()
        comparison.samples[1].speed_kmh = 140.0

        overlay = build_lap_overlay(main, comparison, metrics=["speed_kmh", "steering"], points=5)

        self.assertEqual({series.metric for series in overlay}, {"speed_kmh", "steering"})
        self.assertEqual(len(overlay[0].axis), 5)
        self.assertEqual(len(overlay[0].main), 5)
        self.assertEqual(len(overlay[0].comparison), 5)

    def test_overlay_skips_missing_metrics_safely(self) -> None:
        main = lap_with_samples()
        comparison = lap_with_samples()
        for sample in comparison.samples:
            sample.steering = None

        overlay = build_lap_overlay(main, comparison, metrics=["steering"], points=5)

        self.assertEqual(overlay, [])

    def test_sector_feedback_uses_reference_and_stays_quiet_without_one(self) -> None:
        lap = lap_with_samples()
        reference = lap_with_samples()
        reference.sectors[2].time_ms = 39500

        messages = sector_feedback(lap, reference)

        self.assertTrue(messages)
        self.assertIn("Sector 3", messages[0] + " " + messages[-1])
        self.assertEqual(sector_feedback(lap, None), [])


if __name__ == "__main__":
    unittest.main()
