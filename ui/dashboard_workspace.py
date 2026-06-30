from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


def new_tile_id() -> str:
    return f"tile_{uuid4().hex[:10]}"


@dataclass(slots=True)
class TilePanel:
    panel_id: str
    title: str
    widget: QWidget
    compact: bool = False


class DashboardTile(QFrame):
    add_requested = Signal(str)
    split_requested = Signal(str, str)
    detach_requested = Signal(str)
    close_requested = Signal(str)
    compact_requested = Signal(str, bool)
    panel_dropped = Signal(str, str, str)

    def __init__(self, tile_id: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.tile_id = tile_id or new_tile_id()
        self.setObjectName(self.tile_id)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)
        self._drop_zone = ""
        self.setMinimumSize(96, 72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._edit_mode = False

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(2, 2, 2, 2)
        self.layout.setSpacing(2)
        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.layout.addWidget(self.tabs)

        self.placeholder = QWidget(self)
        placeholder_layout = QVBoxLayout(self.placeholder)
        placeholder_layout.setContentsMargins(4, 4, 4, 4)
        label = QLabel("Drop a telemetry panel here")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_button = QPushButton("+ Add panel")
        add_button.clicked.connect(lambda: self.add_requested.emit(self.tile_id))
        placeholder_layout.addStretch(1)
        placeholder_layout.addWidget(label)
        placeholder_layout.addWidget(add_button)
        placeholder_layout.addStretch(1)
        self.layout.addWidget(self.placeholder)
        self._refresh_placeholder()

    def panel_ids(self) -> list[str]:
        return [str(self.tabs.widget(index).property("panel_id")) for index in range(self.tabs.count())]

    def add_panel(self, panel_id: str, title: str, widget: QWidget, compact: bool = False) -> None:
        widget.setProperty("panel_id", panel_id)
        widget.setProperty("compact_mode", compact)
        index = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(index)
        self._refresh_placeholder()

    def remove_panel(self, panel_id: str) -> QWidget | None:
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if widget.property("panel_id") == panel_id:
                self.tabs.removeTab(index)
                self._refresh_placeholder()
                return widget
        return None

    def current_panel_id(self) -> str | None:
        widget = self.tabs.currentWidget()
        if widget is None:
            return None
        value = widget.property("panel_id")
        return str(value) if value else None

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        self.setStyleSheet("DashboardTile { border: 1px solid #3d7eff; }" if enabled else "")
        self.placeholder.setVisible(enabled or self.tabs.count() == 0)

    def snapshot(self) -> dict:
        return {
            "type": "tile",
            "tile_id": self.tile_id,
            "active_tab": self.tabs.currentIndex(),
            "panels": [
                {
                    "panel_id": str(self.tabs.widget(index).property("panel_id")),
                    "compact": bool(self.tabs.widget(index).property("compact_mode")),
                }
                for index in range(self.tabs.count())
            ],
        }

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Add panel here", lambda: self.add_requested.emit(self.tile_id))
        menu.addSeparator()
        menu.addAction("Split left", lambda: self.split_requested.emit(self.tile_id, "left"))
        menu.addAction("Split right", lambda: self.split_requested.emit(self.tile_id, "right"))
        menu.addAction("Split above", lambda: self.split_requested.emit(self.tile_id, "above"))
        menu.addAction("Split below", lambda: self.split_requested.emit(self.tile_id, "below"))
        panel_id = self.current_panel_id()
        if panel_id:
            compact = not bool(self.tabs.currentWidget().property("compact_mode"))
            menu.addSeparator()
            menu.addAction("Toggle compact mode", lambda: self.compact_requested.emit(panel_id, compact))
            menu.addAction("Detach", lambda: self.detach_requested.emit(panel_id))
            menu.addAction("Close panel", lambda: self.close_requested.emit(panel_id))
        elif self.tabs.count() == 0:
            menu.addAction("Remove empty tile", self.deleteLater)
        menu.exec(event.globalPos())

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        panel_id = widget.property("panel_id")
        if panel_id:
            self.close_requested.emit(str(panel_id))

    def _refresh_placeholder(self) -> None:
        empty = self.tabs.count() == 0
        self.tabs.setVisible(not empty)
        self.placeholder.setVisible(empty or self._edit_mode)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat("application/x-racing-panel-id") or event.mimeData().hasText():
            event.acceptProposedAction()
            self._set_drop_zone("center")
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self._set_drop_zone(self._zone_for_position(event.position().toPoint()))
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_zone("")
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        panel_id = bytes(event.mimeData().data("application/x-racing-panel-id")).decode("utf-8") if event.mimeData().hasFormat("application/x-racing-panel-id") else event.mimeData().text()
        zone = self._zone_for_position(event.position().toPoint())
        self._set_drop_zone("")
        if panel_id:
            self.panel_dropped.emit(self.tile_id, panel_id, zone)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _zone_for_position(self, position) -> str:
        width = max(1, self.width())
        height = max(1, self.height())
        x = position.x() / width
        y = position.y() / height
        margin = 0.25
        if x < margin:
            return "left"
        if x > 1.0 - margin:
            return "right"
        if y < margin:
            return "above"
        if y > 1.0 - margin:
            return "below"
        return "center"

    def _set_drop_zone(self, zone: str) -> None:
        self._drop_zone = zone
        if zone:
            self.setStyleSheet("DashboardTile { border: 2px solid #00a3ff; background: rgba(0, 120, 215, 24); }")
        else:
            self.set_edit_mode(self._edit_mode)


class DashboardWorkspace(QWidget):
    add_panel_requested = Signal(str)
    detach_panel_requested = Signal(str)
    close_panel_requested = Signal(str)
    compact_panel_requested = Signal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tiles: dict[str, DashboardTile] = {}
        self.panel_to_tile: dict[str, str] = {}
        self._edit_mode = False
        self.root: QWidget = self._new_tile()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.root)

    def set_edit_mode(self, enabled: bool) -> None:
        self._edit_mode = enabled
        for tile in self.tiles.values():
            tile.set_edit_mode(enabled)

    def add_panel(self, panel_id: str, title: str, widget: QWidget, tile_id: str | None = None, compact: bool = False) -> str:
        if panel_id in self.panel_to_tile:
            self.move_panel(panel_id, tile_id or self.panel_to_tile[panel_id])
            return self.panel_to_tile[panel_id]
        tile = self.tiles.get(tile_id or "") or self.first_empty_tile() or self.first_tile()
        tile.add_panel(panel_id, title, widget, compact)
        self.panel_to_tile[panel_id] = tile.tile_id
        return tile.tile_id

    def move_panel(self, panel_id: str, tile_id: str) -> bool:
        source = self.tiles.get(self.panel_to_tile.get(panel_id, ""))
        target = self.tiles.get(tile_id)
        if source is None or target is None:
            return False
        widget = source.remove_panel(panel_id)
        if widget is None:
            return False
        title = widget.windowTitle() or panel_id
        target.add_panel(panel_id, title, widget, bool(widget.property("compact_mode")))
        self.panel_to_tile[panel_id] = target.tile_id
        return True

    def remove_panel(self, panel_id: str) -> QWidget | None:
        tile = self.tiles.get(self.panel_to_tile.pop(panel_id, ""))
        if tile is None:
            return None
        return tile.remove_panel(panel_id)

    def split_tile(self, tile_id: str, direction: str) -> str | None:
        tile = self.tiles.get(tile_id)
        if tile is None:
            return None
        orientation = Qt.Orientation.Horizontal if direction in {"left", "right"} else Qt.Orientation.Vertical
        new_tile = self._new_tile()
        splitter = QSplitter(orientation)
        splitter.setChildrenCollapsible(False)
        parent_splitter = tile.parentWidget() if isinstance(tile.parentWidget(), QSplitter) else None
        old_index = parent_splitter.indexOf(tile) if parent_splitter is not None else -1
        if parent_splitter is None:
            self.layout.replaceWidget(self.root, splitter)
            self.root.setParent(None)
            self.root = splitter
        else:
            tile.setParent(None)
            parent_splitter.insertWidget(old_index, splitter)
        if direction in {"left", "above"}:
            splitter.addWidget(new_tile)
            splitter.addWidget(tile)
        else:
            splitter.addWidget(tile)
            splitter.addWidget(new_tile)
        splitter.setSizes([1, 1])
        return new_tile.tile_id

    def create_tab_group(self, source_panel_id: str, target_tile_id: str) -> bool:
        return self.move_panel(source_panel_id, target_tile_id)

    def quick_grid(self, columns: int, rows: int) -> None:
        panels = self.extract_all_panels()
        self._clear_root()
        if rows <= 1:
            root = QSplitter(Qt.Orientation.Horizontal)
            for _ in range(columns):
                root.addWidget(self._new_tile())
        else:
            root = QSplitter(Qt.Orientation.Horizontal)
            for _column in range(columns):
                column_splitter = QSplitter(Qt.Orientation.Vertical)
                column_splitter.setChildrenCollapsible(False)
                for _row in range(rows):
                    column_splitter.addWidget(self._new_tile())
                column_splitter.setSizes([1] * rows)
                root.addWidget(column_splitter)
        root.setChildrenCollapsible(False)
        root.setSizes([1] * max(1, columns))
        self.root = root
        self.layout.addWidget(self.root)
        for panel in panels:
            self.add_panel(panel.panel_id, panel.title, panel.widget, compact=panel.compact)

    def extract_all_panels(self) -> list[TilePanel]:
        panels: list[TilePanel] = []
        for tile in list(self.tiles.values()):
            for index in reversed(range(tile.tabs.count())):
                widget = tile.tabs.widget(index)
                panel_id = str(widget.property("panel_id"))
                panels.append(TilePanel(panel_id, tile.tabs.tabText(index), widget, bool(widget.property("compact_mode"))))
                tile.tabs.removeTab(index)
        self.panel_to_tile.clear()
        return list(reversed(panels))

    def snapshot(self) -> dict:
        return self._snapshot_widget(self.root)

    def restore(self, data: dict, widgets: dict[str, tuple[str, QWidget]]) -> bool:
        if not isinstance(data, dict):
            return False
        panels = self.extract_all_panels()
        self._clear_root()
        try:
            self.root = self._restore_node(data, widgets, depth=0)
        except (KeyError, TypeError, ValueError):
            self.root = self._new_tile()
            self.layout.addWidget(self.root)
            for panel in panels:
                self.add_panel(panel.panel_id, panel.title, panel.widget, compact=panel.compact)
            return False
        self.layout.addWidget(self.root)
        self.set_edit_mode(self._edit_mode)
        return True

    def first_tile(self) -> DashboardTile:
        return next(iter(self.tiles.values()))

    def first_empty_tile(self) -> DashboardTile | None:
        return next((tile for tile in self.tiles.values() if not tile.panel_ids()), None)

    def tile_count(self) -> int:
        return len(self.tiles)

    def _new_tile(self, tile_id: str | None = None) -> DashboardTile:
        tile = DashboardTile(tile_id)
        self.tiles[tile.tile_id] = tile
        tile.add_requested.connect(self.add_panel_requested)
        tile.split_requested.connect(self.split_tile)
        tile.detach_requested.connect(self.detach_panel_requested)
        tile.close_requested.connect(self.close_panel_requested)
        tile.compact_requested.connect(self.compact_panel_requested)
        tile.panel_dropped.connect(self._handle_panel_drop)
        tile.set_edit_mode(self._edit_mode)
        return tile

    def _handle_panel_drop(self, tile_id: str, panel_id: str, zone: str) -> None:
        target_tile = tile_id
        if zone in {"left", "right", "above", "below"}:
            new_tile = self.split_tile(tile_id, zone)
            if new_tile is not None:
                target_tile = new_tile
        self.move_panel(panel_id, target_tile)

    def _clear_root(self) -> None:
        self.tiles.clear()
        self.panel_to_tile.clear()
        if self.root is not None:
            self.layout.removeWidget(self.root)
            self.root.setParent(None)

    def _snapshot_widget(self, widget: QWidget) -> dict:
        if isinstance(widget, DashboardTile):
            return widget.snapshot()
        if isinstance(widget, QSplitter):
            return {
                "type": "splitter",
                "orientation": "horizontal" if widget.orientation() == Qt.Orientation.Horizontal else "vertical",
                "sizes": widget.sizes(),
                "children": [self._snapshot_widget(widget.widget(index)) for index in range(widget.count())],
            }
        raise ValueError("Unsupported dashboard node")

    def _restore_node(self, data: dict, widgets: dict[str, tuple[str, QWidget]], depth: int) -> QWidget:
        if depth > 20:
            raise ValueError("Dashboard layout is too deep")
        node_type = data["type"]
        if node_type == "tile":
            tile = self._new_tile(str(data.get("tile_id") or new_tile_id()))
            for panel in data.get("panels", []):
                panel_id = str(panel.get("panel_id", ""))
                if panel_id in widgets and panel_id not in self.panel_to_tile:
                    title, widget = widgets[panel_id]
                    tile.add_panel(panel_id, title, widget, bool(panel.get("compact", False)))
                    self.panel_to_tile[panel_id] = tile.tile_id
            active = int(data.get("active_tab", 0))
            if tile.tabs.count():
                tile.tabs.setCurrentIndex(max(0, min(active, tile.tabs.count() - 1)))
            return tile
        if node_type == "splitter":
            orientation = Qt.Orientation.Horizontal if data.get("orientation") == "horizontal" else Qt.Orientation.Vertical
            splitter = QSplitter(orientation)
            splitter.setChildrenCollapsible(False)
            children = data.get("children")
            if not isinstance(children, list) or not children:
                raise ValueError("Splitter has no children")
            for child in children:
                splitter.addWidget(self._restore_node(child, widgets, depth + 1))
            sizes = data.get("sizes")
            if isinstance(sizes, list) and len(sizes) == splitter.count():
                splitter.setSizes([max(1, int(size)) for size in sizes])
            return splitter
        raise ValueError("Unknown dashboard node")
