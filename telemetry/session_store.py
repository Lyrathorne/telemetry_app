from __future__ import annotations

import json
from pathlib import Path

from app.paths import data_dir, ensure_user_directories
from models import TelemetrySession
from telemetry.importer import session_from_dict, session_to_dict


class SessionStore:
    def __init__(self) -> None:
        ensure_user_directories()
        self.path = data_dir() / "imported_sessions.json"

    def load(self) -> list[TelemetrySession]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [session_from_dict(item) for item in data if isinstance(item, dict)]

    def save(self, sessions: list[TelemetrySession]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [session_to_dict(session) for session in sessions]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save_session_file(self, session: TelemetrySession, path: str | Path) -> None:
        file_path = Path(path)
        file_path.write_text(json.dumps(session_to_dict(session), indent=2), encoding="utf-8")
