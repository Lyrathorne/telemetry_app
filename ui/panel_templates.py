from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PanelTemplate:
    template_id: str
    title: str
    panel_type: str
    description: str
    singleton: bool = False
    default_config: dict = field(default_factory=dict)


PANEL_TEMPLATES: dict[str, PanelTemplate] = {
    "graph": PanelTemplate(
        "graph",
        "Graph Panel",
        "graph",
        "Configurable live graph panel.",
        default_config={
            "metrics": ["speed_kmh"],
            "x_mode": "follow_live",
            "recent_window": 30,
            "y_mode": "metric_default",
        },
    ),
    "live_graph": PanelTemplate(
        "live_graph",
        "Live Graphs",
        "graph",
        "General-purpose live graph panel.",
        default_config={
            "metrics": ["speed_kmh"],
            "x_mode": "follow_live",
            "recent_window": 30,
            "y_mode": "metric_default",
        },
    ),
    "pedals_graph": PanelTemplate(
        "pedals_graph",
        "Pedals graph",
        "graph",
        "Live throttle and brake traces using a fixed 0-100% scale.",
        default_config={
            "metrics": ["throttle_percent", "brake_percent"],
            "x_mode": "follow_live",
            "recent_window": 30,
            "y_mode": "metric_default",
            "manual_y": [0.0, 100.0],
            "settings_hidden": True,
        },
    ),
    "current_lap_graph": PanelTemplate(
        "current_lap_graph",
        "Current lap telemetry",
        "current_lap_graph",
        "Throttle and brake graph for the active lap; resets only after confirmed lap completion.",
        default_config={
            "metrics": ["throttle_percent", "brake_percent"],
            "x_mode": "full_session",
            "y_mode": "metric_default",
            "manual_y": [0.0, 100.0],
            "settings_hidden": True,
        },
    ),
    "speed_rpm_graph": PanelTemplate(
        "speed_rpm_graph",
        "Speed and RPM graph",
        "stacked_graph",
        "Linked speed and RPM graphs with separate Y scales.",
        default_config={
            "graphs": [
                {"title": "Speed", "metrics": ["speed_kmh"], "manual_y": [0.0, 300.0]},
                {"title": "RPM", "metrics": ["rpm"], "manual_y": [0.0, 12000.0]},
            ],
            "x_mode": "follow_live",
            "recent_window": 30,
            "settings_hidden": True,
        },
    ),
    "live_values": PanelTemplate(
        "live_values",
        "Live telemetry values",
        "live_values",
        "Compact live values for the current telemetry sample.",
    ),
    "live_lap_timing": PanelTemplate(
        "live_lap_timing",
        "Live lap timing",
        "live_lap_timing",
        "Real-time lap and sector table with status text.",
    ),
    "sector_timing": PanelTemplate(
        "sector_timing",
        "Sector timing",
        "sector_timing",
        "Compact current-sector timing and deltas.",
    ),
    "lap_history": PanelTemplate(
        "lap_history",
        "Lap history",
        "lap_history",
        "Saved lap history from the local database.",
        singleton=True,
    ),
    "session_history": PanelTemplate(
        "session_history",
        "Session history",
        "session_history",
        "Saved driving sessions and lap timing summaries.",
        singleton=True,
    ),
    "best_laps": PanelTemplate(
        "best_laps",
        "Best laps",
        "best_laps",
        "Best saved laps filtered by track and car.",
    ),
    "lap_comparison": PanelTemplate(
        "lap_comparison",
        "Lap comparison",
        "lap_comparison",
        "Compare saved laps using aligned telemetry metrics.",
    ),
    "time_delta_graph": PanelTemplate(
        "time_delta_graph",
        "Time delta graph",
        "time_delta_graph",
        "Reference-relative time delta graph aligned by lap position.",
    ),
    "source_status": PanelTemplate(
        "source_status",
        "Source status",
        "builtin",
        "Telemetry source state and latest connection status.",
        singleton=True,
    ),
    "connection_diagnostics": PanelTemplate(
        "connection_diagnostics",
        "Connection diagnostics",
        "builtin",
        "UDP and shared-memory connection diagnostics.",
        singleton=True,
    ),
}


TEMPLATE_GROUPS: dict[str, list[str]] = {
    "Live driving": ["pedals_graph", "speed_rpm_graph", "current_lap_graph", "live_values"],
    "Timing": ["live_lap_timing", "sector_timing", "lap_history", "session_history", "best_laps"],
    "Analysis": ["lap_comparison", "time_delta_graph"],
    "Diagnostics": ["source_status", "connection_diagnostics"],
}


BUILTIN_LAYOUTS: dict[str, list[str]] = {
    "Live driving": ["pedals_graph", "speed_rpm_graph", "current_lap_graph", "live_values", "sector_timing"],
    "Timing": ["live_lap_timing", "sector_timing", "lap_history", "session_history", "live_values"],
    "Analysis": ["lap_history", "lap_comparison", "time_delta_graph"],
    "Diagnostics": ["source_status", "connection_diagnostics"],
}
