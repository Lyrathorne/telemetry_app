from __future__ import annotations


def parse_racing_time_to_ms(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return int(round(numeric if numeric > 1000 else numeric * 1000))
    text = str(value).strip().replace("+", "").replace("-", "")
    if not text or text in {"--", "-:--.---"}:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 1:
            return int(round(float(parts[0]) * 1000))
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            if seconds >= 60:
                return None
            return int(round((minutes * 60 + seconds) * 1000))
        if len(parts) == 3 and "." not in parts[2] and parts[2].isdigit() and len(parts[2]) <= 3:
            minutes = int(parts[0])
            seconds = int(parts[1])
            millis = int(parts[2].ljust(3, "0"))
            if seconds >= 60:
                return None
            return ((minutes * 60) + seconds) * 1000 + millis
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            if minutes >= 60 or seconds >= 60:
                return None
            return int(round(((hours * 60 + minutes) * 60 + seconds) * 1000))
    except (TypeError, ValueError, IndexError):
        return None
    return None
