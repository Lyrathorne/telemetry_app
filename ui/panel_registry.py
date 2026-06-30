from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtWidgets import QDockWidget, QWidget


@dataclass(slots=True)
class PanelRecord:
    panel_id: str
    panel_type: str
    title: str
    dock: QDockWidget | None
    content: QWidget
    singleton: bool = False
    location: str = "dock"


class PanelRegistry:
    def __init__(self) -> None:
        self._records: dict[str, PanelRecord] = {}
        self._logger = logging.getLogger(__name__)

    def register(
        self,
        panel_id: str,
        panel_type: str,
        title: str,
        dock: QDockWidget | None,
        content: QWidget,
        singleton: bool = False,
        location: str = "dock",
    ) -> PanelRecord:
        if panel_id in self._records:
            self._logger.warning("Ignoring duplicate panel id during registration: %s", panel_id)
            return self._records[panel_id]
        if dock is not None:
            dock.setObjectName(panel_id)
        content.setObjectName(f"{panel_id}_content")
        record = PanelRecord(panel_id, panel_type, title, dock, content, singleton, location)
        self._records[panel_id] = record
        return record

    def get(self, panel_id: str) -> PanelRecord | None:
        return self._records.get(panel_id)

    def contains(self, panel_id: str) -> bool:
        return panel_id in self._records

    def remove(self, panel_id: str) -> None:
        self._records.pop(panel_id, None)

    def records(self) -> list[PanelRecord]:
        return list(self._records.values())

    def ids(self) -> list[str]:
        return list(self._records)
