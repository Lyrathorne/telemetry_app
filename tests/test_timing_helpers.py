import tempfile
import unittest
from pathlib import Path

from models import LapResult, LapTelemetrySeries, SectorResult, TelemetrySample
from telemetry.display_names import display_car_name, display_track_name
from telemetry.lap_delta import completed_lap_delta_ms, format_delta_ms, live_lap_delta_ms
from telemetry.lap_storage import LapStorage
from telemetry.timing_status import recalculate_sector_statuses


def lap(lap_number: int, lap_time_ms: int, sector_times: list[int], started_at: str | None = None) -> LapResult:
    return LapResult(
        lap_number=lap_number,
        lap_time_ms=lap_time_ms,
        valid=True,
        complete=True,
        game="ACC",
        track="track",
        car="car",
        session_id="session",
        started_at=started_at or f"2026-01-01T00:00:{lap_number:02d}.000",
        sectors=[
            SectorResult(sector_number=index + 1, time_ms=time_ms, valid=True)
            for index, time_ms in enumerate(sector_times)
        ],
    )


class TimingHelperTests(unittest.TestCase):
    def test_only_one_purple_per_sector_and_old_purple_becomes_green(self) -> None:
        lap_1 = lap(1, 120000, [40000, 40000, 40000])
        lap_2 = lap(2, 119000, [41000, 39500, 39000])

        recalculate_sector_statuses([lap_1, lap_2], lap_2)

        for sector_number in (1, 2, 3):
            purple_count = sum(
                1
                for candidate in (lap_1, lap_2)
                for sector in candidate.sectors
                if sector.sector_number == sector_number and sector.comparison_status == "PURPLE"
            )
            self.assertEqual(purple_count, 1)
        self.assertEqual(lap_1.sectors[2].comparison_status, "GREEN")
        self.assertEqual(lap_2.sectors[2].comparison_status, "PURPLE")

    def test_delta_calculation_against_best_lap(self) -> None:
        best = lap(1, 100000, [30000, 30000, 40000])
        slower = lap(2, 102345, [31000, 31000, 40345])

        self.assertEqual(completed_lap_delta_ms(best, [best, slower]), 0)
        self.assertEqual(format_delta_ms(completed_lap_delta_ms(best, [best, slower]), best_label=True), "Best")
        self.assertEqual(format_delta_ms(completed_lap_delta_ms(slower, [best, slower])), "+2.345")

    def test_live_delta_uses_reference_progress_when_available(self) -> None:
        reference = lap(1, 100000, [30000, 30000, 40000])
        reference.telemetry_series = LapTelemetrySeries(
            lap_id=reference.id,
            lap_number=1,
            lap_time_ms=100000,
            elapsed_time_s=[0.0, 50.0, 100.0],
            normalized_position=[0.0, 0.5, 1.0],
        )
        current = LapResult(lap_number=2, track="track", car="car", complete=False)
        current.samples.append(TelemetrySample(current_lap_time_ms=51000, normalized_track_position=0.5))

        self.assertEqual(live_lap_delta_ms(current, [reference]), 1000)

    def test_display_name_catalog_and_fallback(self) -> None:
        self.assertEqual(display_car_name("Porsche_911_gt3"), "Porsche 911 GT3")
        self.assertEqual(display_track_name("ks_silverstone"), "Silverstone")
        self.assertEqual(display_car_name("f1_2021_mclaren"), "McLaren")
        self.assertEqual(display_track_name("f1_2021_britain"), "Silverstone")
        self.assertEqual(display_track_name("unknown_fast_track"), "Unknown Fast Track")

    def test_session_summary_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = LapStorage(Path(tmpdir) / "laps.sqlite3")
            storage.ensure_session("s1", game="ACC", track="ks_silverstone", car="Porsche_911_gt3", started_at="2026-01-01T10:00:00.000")
            saved_lap = lap(1, 100000, [30000, 30000, 40000])
            saved_lap.session_id = "s1"
            saved_lap.track = "ks_silverstone"
            saved_lap.car = "Porsche_911_gt3"
            storage.save_lap(saved_lap)
            storage.end_session("s1", "2026-01-01T10:10:00.000")

            summaries = storage.load_session_summaries()

            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].session_id, "s1")
            self.assertEqual(summaries[0].lap_count, 1)
            self.assertEqual(summaries[0].best_lap_time_ms, 100000)
            self.assertEqual(summaries[0].ended_at, "2026-01-01T10:10:00.000")

    def test_corrupted_storage_file_is_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "laps.sqlite3"
            path.write_text("not sqlite", encoding="utf-8")

            storage = LapStorage(path)

            self.assertEqual(storage.load_laps(), [])
            self.assertTrue(any(item.name.startswith("laps.sqlite3.corrupt-") for item in Path(tmpdir).iterdir()))


if __name__ == "__main__":
    unittest.main()
