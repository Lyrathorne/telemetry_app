import struct
import unittest

from models import format_gear, format_time_ms
from telemetry import SOURCE_TYPES
from telemetry.assetto_corsa import normalize_ac_gear, to_percent as ac_to_percent
from telemetry.assetto_corsa_competizione import normalize_acc_gear
from telemetry.assetto_corsa_competizione import (
    GRAPHICS_BEST_TIME_TEXT_OFFSET,
    GRAPHICS_COMPLETED_LAPS_OFFSET,
    GRAPHICS_CURRENT_TIME_TEXT_OFFSET,
    GRAPHICS_CURRENT_SECTOR_OFFSET,
    GRAPHICS_CURRENT_TIME_OFFSET,
    GRAPHICS_LAST_SECTOR_TIME_OFFSET,
    GRAPHICS_LAST_TIME_TEXT_OFFSET,
    GRAPHICS_LAST_TIME_OFFSET,
    GRAPHICS_CAR_COORDINATES_OFFSET,
    GRAPHICS_DISTANCE_TRAVELED_OFFSET,
    GRAPHICS_NORMALIZED_POSITION_OFFSET,
    GRAPHICS_SPLIT_TEXT_OFFSET,
    GRAPHICS_HEADER_FORMAT,
    GRAPHICS_MAP_SIZE,
    MAP_NAMES,
    PHYSICS_MAP_SIZE,
    STATIC_MAP_SIZE,
    normalize_steering,
    parse_acc_time_text,
    read_acc_graphics,
)
from telemetry.lap_distance import distance_from_normalized_progress, validate_lap_distance
from telemetry.f1_2018 import (
    F1_2018_CAR_TELEMETRY_PACKET_ID,
    F1_2018_CAR_TELEMETRY_PACKET_SIZE,
    F1_2018_LAP_DATA_PACKET_ID,
    F1_2018_LAP_DATA_PACKET_SIZE,
    F1_2018_MOTION_PACKET_ID,
    F1_2018_MOTION_PACKET_SIZE,
    F1_2018_HEADER_FORMAT,
    F1_2018_HEADER_SIZE,
    F1_2018_PACKET_FORMAT,
    F1_2018_MOTION_RECORD_SIZE,
    parse_car_telemetry_packet,
    parse_lap_data_packet,
    parse_motion_packet,
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
        struct.pack_into("<b", packet, record_start + 3, -14)
        struct.pack_into("<B", packet, record_start + 4, 12)
        struct.pack_into("<B", packet, record_start + 5, 7)
        struct.pack_into("<b", packet, record_start + 6, 6)
        struct.pack_into("<H", packet, record_start + 7, 11250)

        telemetry = parse_car_telemetry_packet(bytes(packet), 0)

        self.assertIsNotNone(telemetry)
        self.assertEqual(telemetry.speed_kmh, 286)
        self.assertEqual(telemetry.throttle_percent, 87)
        self.assertEqual(telemetry.brake_percent, 12)
        self.assertEqual(telemetry.clutch_percent, 7)
        self.assertEqual(telemetry.steering, -14)
        self.assertEqual(telemetry.gear, 6)
        self.assertEqual(telemetry.rpm, 11250)

    def test_f1_lap_data_parses_distance_and_lap_state(self) -> None:
        packet = bytearray(F1_2018_LAP_DATA_PACKET_SIZE)
        struct.pack_into(
            F1_2018_HEADER_FORMAT,
            packet,
            0,
            F1_2018_PACKET_FORMAT,
            1,
            F1_2018_LAP_DATA_PACKET_ID,
            123,
            10.0,
            7,
            0,
        )
        record_start = F1_2018_HEADER_SIZE
        struct.pack_into("<f", packet, record_start + 0, 80.5)
        struct.pack_into("<f", packet, record_start + 4, 12.25)
        struct.pack_into("<f", packet, record_start + 8, 79.0)
        struct.pack_into("<f", packet, record_start + 20, 456.75)
        struct.pack_into("<B", packet, record_start + 33, 3)
        struct.pack_into("<B", packet, record_start + 35, 1)
        struct.pack_into("<B", packet, record_start + 36, 0)

        lap_data = parse_lap_data_packet(bytes(packet), 0)

        self.assertIsNotNone(lap_data)
        self.assertEqual(lap_data.last_lap_time_ms, 80500)
        self.assertEqual(lap_data.current_lap_time_ms, 12250)
        self.assertEqual(lap_data.best_lap_time_ms, 79000)
        self.assertAlmostEqual(lap_data.lap_distance, 456.75)
        self.assertEqual(lap_data.lap_number, 3)
        self.assertEqual(lap_data.current_sector_index, 1)
        self.assertFalse(lap_data.invalid_lap)

    def test_f1_motion_parses_world_position(self) -> None:
        packet = bytearray(F1_2018_MOTION_PACKET_SIZE)
        struct.pack_into(
            F1_2018_HEADER_FORMAT,
            packet,
            0,
            F1_2018_PACKET_FORMAT,
            1,
            F1_2018_MOTION_PACKET_ID,
            123,
            10.0,
            7,
            1,
        )
        record_start = F1_2018_HEADER_SIZE + F1_2018_MOTION_RECORD_SIZE
        struct.pack_into("<fff", packet, record_start, 10.0, 2.0, -5.0)

        motion = parse_motion_packet(bytes(packet), 1)

        self.assertIsNotNone(motion)
        self.assertEqual((motion.world_position_x, motion.world_position_y, motion.world_position_z), (10.0, 2.0, -5.0))

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
        packet = bytearray(GRAPHICS_MAP_SIZE)
        struct.pack_into(GRAPHICS_HEADER_FORMAT, packet, 0, 9, 2, 0)
        write_utf16(packet, GRAPHICS_SPLIT_TEXT_OFFSET, "01:14.135", 15)
        write_utf16(packet, GRAPHICS_CURRENT_TIME_TEXT_OFFSET, "01:14.135", 15)
        write_utf16(packet, GRAPHICS_LAST_TIME_TEXT_OFFSET, "01:46.942", 15)
        write_utf16(packet, GRAPHICS_BEST_TIME_TEXT_OFFSET, "01:45.000", 15)
        struct.pack_into("=i", packet, GRAPHICS_COMPLETED_LAPS_OFFSET, 4)
        struct.pack_into("=i", packet, GRAPHICS_CURRENT_TIME_OFFSET, -1)
        struct.pack_into("=i", packet, GRAPHICS_LAST_TIME_OFFSET, -1)
        struct.pack_into("=i", packet, GRAPHICS_CURRENT_SECTOR_OFFSET, 2)
        struct.pack_into("=i", packet, GRAPHICS_LAST_SECTOR_TIME_OFFSET, 42851)
        struct.pack_into("=f", packet, GRAPHICS_DISTANCE_TRAVELED_OFFSET, 12345.0)
        struct.pack_into("=f", packet, GRAPHICS_NORMALIZED_POSITION_OFFSET, 0.75)
        struct.pack_into("=fff", packet, GRAPHICS_CAR_COORDINATES_OFFSET, 10.0, 2.0, -5.0)

        graphics = read_acc_graphics(BytesMapping(packet))

        self.assertEqual(graphics["completed_laps"], 4)
        self.assertEqual(graphics["current_lap_time_ms"], 74135)
        self.assertEqual(graphics["last_lap_time_ms"], 106942)
        self.assertEqual(graphics["best_lap_time_ms"], 105000)
        self.assertEqual(graphics["current_sector_index"], 2)
        self.assertEqual(graphics["current_split_time_ms"], 74135)
        self.assertEqual(graphics["last_sector_time_ms"], 42851)
        self.assertAlmostEqual(graphics["distance_traveled_m"], 12345.0)
        self.assertIsNone(graphics["lap_distance_m"])
        self.assertEqual(graphics["normalized_track_position"], 0.75)
        self.assertEqual(graphics["car_coordinates"], (10.0, 2.0, -5.0))

    def test_acc_shared_memory_pages_are_defined_independently(self) -> None:
        self.assertEqual(MAP_NAMES["physics"], "Local\\acpmf_physics")
        self.assertEqual(MAP_NAMES["graphics"], "Local\\acpmf_graphics")
        self.assertEqual(MAP_NAMES["static"], "Local\\acpmf_static")
        self.assertGreaterEqual(PHYSICS_MAP_SIZE, 800)
        self.assertGreaterEqual(GRAPHICS_MAP_SIZE, 1588)
        self.assertGreaterEqual(STATIC_MAP_SIZE, 784)

    def test_acc_negative_timing_sentinels_become_none(self) -> None:
        packet = bytearray(GRAPHICS_MAP_SIZE)
        struct.pack_into(GRAPHICS_HEADER_FORMAT, packet, 0, 10, 2, 0)
        struct.pack_into("=i", packet, GRAPHICS_CURRENT_TIME_OFFSET, -1)
        struct.pack_into("=i", packet, GRAPHICS_LAST_TIME_OFFSET, -1)
        struct.pack_into("=i", packet, GRAPHICS_LAST_SECTOR_TIME_OFFSET, -1)
        struct.pack_into("=i", packet, GRAPHICS_CURRENT_SECTOR_OFFSET, 7)
        struct.pack_into("=i", packet, GRAPHICS_COMPLETED_LAPS_OFFSET, 0)

        graphics = read_acc_graphics(BytesMapping(packet))

        self.assertIsNone(graphics["current_lap_time_ms"])
        self.assertIsNone(graphics["last_lap_time_ms"])
        self.assertIsNone(graphics["last_sector_time_ms"])
        self.assertIsNone(graphics["current_sector_index"])

    def test_acc_time_text_parser(self) -> None:
        self.assertEqual(parse_acc_time_text("31.284"), 31284)
        self.assertEqual(parse_acc_time_text("01:31.532"), 91532)
        self.assertEqual(parse_acc_time_text("1:32.456"), 92456)
        self.assertEqual(parse_acc_time_text("2:08.000"), 128000)
        self.assertEqual(parse_acc_time_text("0:59.999"), 59999)
        self.assertEqual(parse_acc_time_text("12:34.567"), 754567)
        self.assertEqual(parse_acc_time_text("1:02:03.004"), 3723004)
        self.assertEqual(parse_acc_time_text("2:10.405"), 130405)
        self.assertEqual(parse_acc_time_text("2:10:405"), 130405)
        self.assertIsNone(parse_acc_time_text("--"))

    def test_time_formatter_keeps_milliseconds_as_milliseconds(self) -> None:
        self.assertEqual(format_time_ms(59617), "00:59.617")
        self.assertEqual(format_time_ms(105730), "01:45.730")
        self.assertEqual(format_time_ms(130405), "02:10.405")
        self.assertEqual(format_time_ms(146020), "02:26.020")
        self.assertEqual(format_time_ms(3723004), "1:02:03.004")

    def test_acc_lap_distance_uses_normalized_progress_and_rejects_session_distance(self) -> None:
        derived = distance_from_normalized_progress(0.5, 5793, lap_number=3)
        self.assertEqual(derived.source, "normalized_progress")
        self.assertAlmostEqual(derived.lap_distance_m or 0.0, 2896.5)
        rejected = validate_lap_distance(12_345.0, 5793.0, previous_distance_m=100.0, lap_number=3)
        self.assertIsNone(rejected.lap_distance_m)
        self.assertEqual(rejected.rejected_reason, "out_of_range")

    def test_acc_steering_keeps_sign_and_normalized_range(self) -> None:
        self.assertEqual(normalize_steering(0.0), 0.0)
        self.assertEqual(normalize_steering(-0.4), -0.4)
        self.assertEqual(normalize_steering(0.7), 0.7)
        self.assertEqual(normalize_steering(4.0), 1.0)
        self.assertIsNone(normalize_steering(float("nan")))


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
