from __future__ import annotations

from dataclasses import dataclass
import json

from PySide6.QtCore import QSettings

from app.paths import ensure_user_directories, exports_dir, settings_dir, user_data_root


DEFAULT_F1_UDP_PORT = 20777


@dataclass(slots=True)
class SourceSettings:
    source_id: str
    udp_host: str = "0.0.0.0"
    udp_port: int | None = None


class AppSettings:
    def __init__(self) -> None:
        ensure_user_directories()
        self._settings = QSettings(str(settings_dir() / "settings.ini"), QSettings.Format.IniFormat)

    def f1_udp_port(self) -> int:
        return self._int_value("telemetry/f1_2018_udp_port", DEFAULT_F1_UDP_PORT, 1, 65535)

    def set_f1_udp_port(self, port: int) -> None:
        self._settings.setValue("telemetry/f1_2018_udp_port", max(1, min(65535, int(port))))

    def source_settings(self, source_id: str) -> SourceSettings:
        if source_id == "f1_2018":
            return SourceSettings(source_id=source_id, udp_port=self.f1_udp_port())
        return SourceSettings(source_id=source_id)

    def data_directory(self) -> str:
        return self._settings.value("paths/data_directory", str(user_data_root() / "data"), str)

    def import_directory(self) -> str:
        return self._settings.value("paths/import_directory", str(user_data_root() / "data"), str)

    def set_import_directory(self, path: str) -> None:
        self._settings.setValue("paths/import_directory", path)

    def export_directory(self) -> str:
        return self._settings.value("paths/export_directory", str(exports_dir()), str)

    def set_export_directory(self, path: str) -> None:
        self._settings.setValue("paths/export_directory", path)

    def graph_refresh_ms(self) -> int:
        return self._int_value("graphs/refresh_ms", 50, 33, 2000)

    def set_graph_refresh_ms(self, value: int) -> None:
        self._settings.setValue("graphs/refresh_ms", max(33, min(2000, int(value))))

    def graph_history_limit(self) -> int:
        return self._int_value("graphs/history_limit", 600, 100, 50000)

    def set_graph_history_limit(self, value: int) -> None:
        self._settings.setValue("graphs/history_limit", max(100, min(50000, int(value))))

    def restore_layout_at_startup(self) -> bool:
        return self._bool_value("layout/restore_at_startup", True)

    def set_restore_layout_at_startup(self, enabled: bool) -> None:
        self._settings.setValue("layout/restore_at_startup", enabled)

    def confirm_remove_sessions(self) -> bool:
        return self._bool_value("sessions/confirm_remove", True)

    def set_confirm_remove_sessions(self, enabled: bool) -> None:
        self._settings.setValue("sessions/confirm_remove", enabled)

    def fullscreen_at_startup(self) -> bool:
        return self._bool_value("window/fullscreen_at_startup", False)

    def set_fullscreen_at_startup(self, enabled: bool) -> None:
        self._settings.setValue("window/fullscreen_at_startup", enabled)

    def save_geometry(self, geometry) -> None:
        self._settings.setValue("window/geometry", geometry)

    def load_geometry(self):
        return self._settings.value("window/geometry")

    def save_state(self, state) -> None:
        self._settings.setValue("window/state", state)

    def load_state(self):
        return self._settings.value("window/state")

    def save_dashboard_layout(self, layout: dict) -> None:
        self._settings.setValue("layout/dashboard", json.dumps(layout))

    def load_dashboard_layout(self) -> dict | None:
        raw = self._settings.value("layout/dashboard", "", str)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def clear_legacy_window_layout(self) -> None:
        self._settings.remove("window/state")
        self._settings.remove("window/geometry")

    def graph_panels_state(self) -> list[dict]:
        raw = self._settings.value("graphs/panels", "[]", str)
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def set_graph_panels_state(self, panels: list[dict]) -> None:
        self._settings.setValue("graphs/panels", json.dumps(panels))

    def save_was_maximized(self, maximized: bool) -> None:
        self._settings.setValue("window/was_maximized", maximized)

    def was_maximized(self) -> bool:
        return self._bool_value("window/was_maximized", False)

    def reset(self) -> None:
        self._settings.clear()

    def sync(self) -> None:
        self._settings.sync()

    def _int_value(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self._settings.value(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _bool_value(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
