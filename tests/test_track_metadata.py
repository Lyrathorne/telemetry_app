import tempfile
import unittest
from pathlib import Path

from telemetry.track_metadata import (
    TrackMetadataError,
    TrackMetadataRepository,
    normalize_track_key,
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

    def test_builtin_acc_sqlite_resolves_track_id_aliases_length_and_map(self) -> None:
        repository = TrackMetadataRepository()
        definition = repository.find_by_acc_id("monza")
        self.assertIsNotNone(definition)
        assert definition is not None
        self.assertEqual(definition.track_id, "monza")
        self.assertGreater(repository.get_length_m("monza") or 0, 0)
        self.assertEqual(repository.find_by_alias("Autodromo Nazionale Monza"), definition)
        self.assertGreater(len(repository.get_map_points("monza")), 2)
        self.assertIn("<svg", repository.get_svg("monza") or "")

    def test_builtin_acc_database_contains_all_tracks_with_lengths_and_maps(self) -> None:
        repository = TrackMetadataRepository()
        for track_id in (
            "barcelona",
            "brands-hatch",
            "cota",
            "donington",
            "hungaroring",
            "imola",
            "indianapolis",
            "kyalami",
            "laguna-seca",
            "misano",
            "monza",
            "mount-panorama",
            "nurburgring-24h",
            "nurburgring",
            "oulton-park",
            "paul-ricard",
            "red-bull-ring",
            "silverstone",
            "snetterton",
            "spa",
            "suzuka",
            "valencia",
            "watkins-glen",
            "zandvoort",
            "zolder",
        ):
            self.assertGreater(repository.get_length_m(track_id) or 0, 0, track_id)
            self.assertGreater(len(repository.get_map_points(track_id)), 2, track_id)

    def test_track_key_normalization_handles_case_spaces_dashes_and_diacritics(self) -> None:
        self.assertEqual(normalize_track_key("  Nürburgring-24H "), normalize_track_key("nurburgring 24h"))


if __name__ == "__main__":
    unittest.main()
