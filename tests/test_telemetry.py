import struct
import unittest

from models import format_gear
from telemetry import SOURCE_TYPES
from telemetry.assetto_corsa import normalize_ac_gear, to_percent as ac_to_percent
from telemetry.assetto_corsa_competizione import normalize_acc_gear
from telemetry.f1_2018 import (
    F1_2018_CAR_TELEMETRY_PACKET_ID,
    F1_2018_CAR_TELEMETRY_PACKET_SIZE,
    F1_2018_HEADER_FORMAT,
    F1_2018_HEADER_SIZE,
    F1_2018_PACKET_FORMAT,
    parse_car_telemetry_packet,
    parse_packet_header,
)


class TelemetryTests(unittest.TestCase):
    def test_source_registry_contains_required_sources(self) -> None:
        self.assertEqual(
            set(SOURCE_TYPES),
            {"demo", "f1_2018", "assetto_corsa", "assetto_corsa_competizione"},
        )

    def test_f1_header_parses_expected_fields(self) -> None:
        packet = struct.pack(
            F1_2018_HEADER_FORMAT,
            F1_2018_PACKET_FORMAT,
            1,
            F1_2018_CAR_TELEMETRY_PACKET_ID,
            123,
            42.5,
            99,
            3,
        )

        header = parse_packet_header(packet)

        self.assertIsNotNone(header)
        self.assertEqual(header.packet_format, F1_2018_PACKET_FORMAT)
        self.assertEqual(header.packet_id, F1_2018_CAR_TELEMETRY_PACKET_ID)
        self.assertEqual(header.player_car_index, 3)

    def test_f1_player_car_telemetry_parses(self) -> None:
        packet = bytearray(F1_2018_CAR_TELEMETRY_PACKET_SIZE)
        struct.pack_into(
            F1_2018_HEADER_FORMAT,
            packet,
            0,
            F1_2018_PACKET_FORMAT,
            1,
            F1_2018_CAR_TELEMETRY_PACKET_ID,
            123,
            10.0,
            7,
            0,
        )
        record_start = F1_2018_HEADER_SIZE
        struct.pack_into("<H", packet, record_start, 286)
        struct.pack_into("<B", packet, record_start + 2, 87)
        struct.pack_into("<B", packet, record_start + 4, 12)
        struct.pack_into("<b", packet, record_start + 6, 6)
        struct.pack_into("<H", packet, record_start + 7, 11250)

        telemetry = parse_car_telemetry_packet(bytes(packet), 0)

        self.assertIsNotNone(telemetry)
        self.assertEqual(telemetry.speed_kmh, 286)
        self.assertEqual(telemetry.throttle_percent, 87)
        self.assertEqual(telemetry.brake_percent, 12)
        self.assertEqual(telemetry.gear, 6)
        self.assertEqual(telemetry.rpm, 11250)

    def test_malformed_f1_packets_are_rejected(self) -> None:
        self.assertIsNone(parse_packet_header(b"\x00"))
        self.assertIsNone(parse_car_telemetry_packet(b"\x00", 0))

    def test_gear_normalization(self) -> None:
        self.assertEqual(normalize_ac_gear(0), -1)
        self.assertEqual(normalize_ac_gear(1), 0)
        self.assertEqual(normalize_ac_gear(2), 1)
        self.assertEqual(normalize_acc_gear(0), -1)
        self.assertEqual(normalize_acc_gear(1), 0)
        self.assertEqual(normalize_acc_gear(2), 1)
        self.assertEqual(format_gear(-1), "R")
        self.assertEqual(format_gear(0), "N")

    def test_percentage_clamps(self) -> None:
        self.assertEqual(ac_to_percent(-1.0), 0.0)
        self.assertEqual(ac_to_percent(0.5), 50.0)
        self.assertEqual(ac_to_percent(2.0), 100.0)


if __name__ == "__main__":
    unittest.main()
