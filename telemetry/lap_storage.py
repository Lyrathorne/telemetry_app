from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.paths import data_dir, ensure_user_directories
from models import LapResult, SectorResult, TelemetrySample


SCHEMA_VERSION = 2


class LapStorage:
    def __init__(self, path: str | Path | None = None) -> None:
        ensure_user_directories()
        self.path = Path(path) if path is not None else data_dir() / "racing_telemetry.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with closing(self.connect()) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry_sessions (
                    id TEXT PRIMARY KEY,
                    game TEXT NOT NULL,
                    track TEXT,
                    car TEXT,
                    driver_name TEXT,
                    source_type TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                );

                CREATE TABLE IF NOT EXISTS laps (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    lap_number INTEGER NOT NULL,
                    lap_time_ms INTEGER,
                    valid INTEGER NOT NULL,
                    complete INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    track TEXT,
                    car TEXT,
                    driver_name TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    notes TEXT,
                    FOREIGN KEY(session_id) REFERENCES telemetry_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lap_id TEXT NOT NULL,
                    sector_number INTEGER NOT NULL,
                    start_distance_m REAL,
                    end_distance_m REAL,
                    time_ms INTEGER,
                    valid INTEGER NOT NULL,
                    comparison_status TEXT,
                    timing_source TEXT,
                    FOREIGN KEY(lap_id) REFERENCES laps(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS lap_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lap_id TEXT NOT NULL,
                    sample_index INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    session_time REAL,
                    lap_time REAL,
                    lap_distance REAL,
                    normalized_track_position REAL,
                    speed_kmh REAL,
                    rpm REAL,
                    gear INTEGER,
                    throttle_percent REAL,
                    brake_percent REAL,
                    clutch_percent REAL,
                    steering REAL,
                    raw_json TEXT NOT NULL,
                    FOREIGN KEY(lap_id) REFERENCES laps(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_laps_session ON laps(session_id);
                CREATE INDEX IF NOT EXISTS idx_laps_track_car ON laps(track, car);
                CREATE INDEX IF NOT EXISTS idx_laps_lap_time ON laps(lap_time_ms);
                CREATE INDEX IF NOT EXISTS idx_samples_lap_time ON lap_samples(lap_id, lap_time);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(sectors)")}
            if "timing_source" not in columns:
                connection.execute("ALTER TABLE sectors ADD COLUMN timing_source TEXT")
            connection.execute(
                """
                DELETE FROM sectors
                WHERE id NOT IN (
                    SELECT MIN(id) FROM sectors GROUP BY lap_id, sector_number
                )
                """
            )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sectors_lap_number ON sectors(lap_id, sector_number)")
            if version < SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def ensure_session(
        self,
        session_id: str,
        game: str,
        track: str | None,
        car: str | None,
        driver_name: str | None = None,
        source_type: str = "live",
        started_at: str = "",
    ) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO telemetry_sessions
                (id, game, track, car, driver_name, source_type, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, game, track, car, driver_name, source_type, started_at),
            )
            connection.commit()

    def save_lap(self, lap: LapResult) -> None:
        with closing(self.connect()) as connection:
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT OR REPLACE INTO laps
                (id, session_id, lap_number, lap_time_ms, valid, complete, game, track,
                 car, driver_name, started_at, completed_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lap.id,
                    lap.session_id,
                    lap.lap_number,
                    lap.lap_time_ms,
                    int(lap.valid),
                    int(lap.complete),
                    lap.game,
                    lap.track,
                    lap.car,
                    lap.driver_name,
                    lap.started_at,
                    lap.completed_at,
                    lap.notes,
                ),
            )
            connection.execute("DELETE FROM sectors WHERE lap_id = ?", (lap.id,))
            connection.execute("DELETE FROM lap_samples WHERE lap_id = ?", (lap.id,))
            connection.executemany(
                """
                INSERT INTO sectors
                (lap_id, sector_number, start_distance_m, end_distance_m, time_ms, valid, comparison_status, timing_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        lap.id,
                        sector.sector_number,
                        sector.start_distance_m,
                        sector.end_distance_m,
                        sector.time_ms,
                        int(sector.valid),
                        sector.comparison_status,
                        sector.timing_source,
                    )
                    for sector in lap.sectors
                ],
            )
            connection.executemany(
                """
                INSERT INTO lap_samples
                (lap_id, sample_index, timestamp, session_time, lap_time, lap_distance,
                 normalized_track_position, speed_kmh, rpm, gear, throttle_percent, brake_percent,
                 clutch_percent, steering, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    sample_row(lap.id, index, sample)
                    for index, sample in enumerate(lap.samples)
                ],
            )
            connection.commit()

    def load_laps(self) -> list[LapResult]:
        with closing(self.connect()) as connection:
            lap_rows = connection.execute(
                """
                SELECT id, session_id, lap_number, lap_time_ms, valid, complete, game, track,
                       car, driver_name, started_at, completed_at, notes
                FROM laps
                ORDER BY started_at DESC, lap_number DESC
                """
            ).fetchall()
            laps: list[LapResult] = []
            for row in lap_rows:
                lap_id = row[0]
                sectors = [
                    SectorResult(
                        sector_number=sector_row[0],
                        start_distance_m=sector_row[1],
                        end_distance_m=sector_row[2],
                        time_ms=sector_row[3],
                        valid=bool(sector_row[4]),
                        comparison_status=sector_row[5],
                        timing_source=sector_row[6] or "unavailable",
                    )
                    for sector_row in connection.execute(
                        """
                        SELECT sector_number, start_distance_m, end_distance_m, time_ms, valid, comparison_status, timing_source
                        FROM sectors WHERE lap_id = ? ORDER BY sector_number
                        """,
                        (lap_id,),
                    )
                ]
                samples = [
                    sample_from_json(sample_row_data[0])
                    for sample_row_data in connection.execute(
                        "SELECT raw_json FROM lap_samples WHERE lap_id = ? ORDER BY sample_index",
                        (lap_id,),
                    )
                ]
                laps.append(
                    LapResult(
                        id=lap_id,
                        session_id=row[1],
                        lap_number=row[2],
                        lap_time_ms=row[3],
                        valid=bool(row[4]),
                        complete=bool(row[5]),
                        game=row[6],
                        track=row[7],
                        car=row[8],
                        driver_name=row[9],
                        started_at=row[10],
                        completed_at=row[11],
                        notes=row[12] or "",
                        sectors=sectors,
                        samples=samples,
                    )
                )
            return laps

    def delete_lap(self, lap_id: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute("DELETE FROM laps WHERE id = ?", (lap_id,))
            connection.commit()


def sample_row(lap_id: str, index: int, sample: TelemetrySample) -> tuple:
    return (
        lap_id,
        index,
        sample.timestamp,
        sample.session_time,
        sample.lap_time,
        sample.lap_distance,
        sample.normalized_track_position,
        sample.speed_kmh,
        sample.rpm,
        sample.gear,
        sample.throttle_percent,
        sample.brake_percent,
        sample.clutch_percent,
        sample.steering,
        json.dumps(sample_to_dict(sample)),
    )


def sample_to_dict(sample: TelemetrySample) -> dict:
    return {field: getattr(sample, field) for field in TelemetrySample.__dataclass_fields__}


def sample_from_json(raw_json: str) -> TelemetrySample:
    return TelemetrySample(**json.loads(raw_json))
