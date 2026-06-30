import struct
import unittest

from models import format_gear
from telemetry import SOURCE_TYPES
from telemetry.assetto_corsa import normalize_ac_gear, to_percent as ac_to_percent
from telemetry.assetto_corsa_competizione import normalize_acc_gear
from telemetry.assetto_corsa_competizione import (
    GRAPHICS_COMPLETED_LAPS_OFFSET,
    GRAPHICS_CURRENT_SECTOR_OFFSET,
    GRAPHICS_CURRENT_TIME_OFFSET,
    GRAPHICS_LAST_SECTOR_TIME_OFFSET,
    GRAPHICS_LAST_TIME_OFFSET,
    GRAPHICS_SPLIT_TEXT_OFFSET,
    GRAPHICS_HEADER_FORMAT,
    parse_acc_time_text,
    read_acc_graphics,
)
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

    def test_acc_graphics_reads_timing_fields(self) -> None:
        packet = bytearray(256)
        struct.pack_into(GRAPHICS_HEADER_FORMAT, packet, 0, 9, 2, 0)
        write_utf16(packet, GRAPHICS_SPLIT_TEXT_OFFSET, "01:14.135", 15)
        struct.pack_into("=i", packet, GRAPHICS_COMPLETED_LAPS_OFFSET, 4)
        struct.pack_into("=i", packet, GRAPHICS_CURRENT_TIME_OFFSET, 74135)
        struct.pack_into("=i", packet, GRAPHICS_LAST_TIME_OFFSET, 106942)
        struct.pack_into("=i", packet, GRAPHICS_CURRENT_SECTOR_OFFSET, 2)
        struct.pack_into("=i", packet, GRAPHICS_LAST_SECTOR_TIME_OFFSET, 42851)

        graphics = read_acc_graphics(BytesMapping(packet))

        self.assertEqual(graphics["completed_laps"], 4)
        self.assertEqual(graphics["current_lap_time_ms"], 74135)
        self.assertEqual(graphics["last_lap_time_ms"], 106942)
        self.assertEqual(graphics["current_sector_index"], 2)
        self.assertEqual(graphics["current_split_time_ms"], 74135)
        self.assertEqual(graphics["last_sector_time_ms"], 42851)

    def test_acc_time_text_parser(self) -> None:
        self.assertEqual(parse_acc_time_text("31.284"), 31284)
        self.assertEqual(parse_acc_time_text("01:31.532"), 91532)
        self.assertEqual(parse_acc_time_text("1:02:03.004"), 3723004)
        self.assertIsNone(parse_acc_time_text("--"))


class BytesMapping:
    def __init__(self, data: bytearray) -> None:
        self.data = bytes(data)

    def read_bytes(self, offset: int, size: int) -> bytes:
        return self.data[offset:offset + size]


def write_utf16(packet: bytearray, offset: int, value: str, wchar_count: int) -> None:
    raw = value.encode("utf-16-le")[: wchar_count * 2]
    packet[offset:offset + len(raw)] = raw


if __name__ == "__main__":
    unittest.main()
