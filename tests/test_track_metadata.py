import tempfile
import unittest
from pathlib import Path

from telemetry.track_metadata import (
    TrackMetadataError,
    TrackMetadataRepository,
    track_definition_from_dict,
    validate_boundaries,
)


class TrackMetadataTests(unittest.TestCase):
    def test_boundary_validation(self) -> None:
        validate_boundaries((0.33, 0.67))
        with self.assertRaises(TrackMetadataError):
            validate_boundaries((0.67, 0.33))
        with self.assertRaises(TrackMetadataError):
            validate_boundaries((0.0, 0.5))

    def test_user_override_loading_and_unknown_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tracks.json"
            path.write_text(
                """
                [
                  {
                    "game": "ACC",
                    "track_id": "test_track",
                    "display_name": "Test Track",
                    "layout_id": null,
                    "track_length_m": 5000,
                    "sector_boundaries_normalized": [0.31, 0.68],
                    "data_source": "unit-test",
                    "data_version": "1"
                  }
                ]
                """,
                encoding="utf-8",
            )
            repository = TrackMetadataRepository(path)
            self.assertIsNotNone(repository.find("ACC", "test_track"))
            self.assertIsNone(repository.find("ACC", "unknown_track"))

    def test_definition_from_dict_records_source(self) -> None:
        definition = track_definition_from_dict(
            {
                "game": "ACC",
                "track_id": "track",
                "display_name": "Track",
                "sector_boundaries_normalized": [0.3, 0.7],
                "data_source": "user",
                "data_version": "2026-06-30",
            }
        )
        self.assertEqual(definition.data_source, "user")
        self.assertEqual(definition.sector_boundaries_normalized, (0.3, 0.7))


if __name__ == "__main__":
    unittest.main()
