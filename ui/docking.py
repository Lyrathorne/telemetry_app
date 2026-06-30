from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDockWidget, QMainWindow, QMenu, QWidget


class DetachedPanelWindow(QMainWindow):
    def __init__(self, main_window, dock: QDockWidget, panel_widget: QWidget) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.main_window = main_window
        self.dock = dock
        self.panel_widget = panel_widget
        self.setObjectName(f"detached_{dock.objectName()}")
        self.setWindowTitle(dock.windowTitle())
        self.setWindowOpacity(1.0)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        panel_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCentralWidget(panel_widget)
        self.resize(max(640, panel_widget.width()), max(420, panel_widget.height()))

        dock_back_action = QAction("Dock back", self)
        dock_back_action.triggered.connect(self.dock_back)
        self.menuBar().addAction(dock_back_action)

    def dock_back(self) -> None:
        self.main_window.dock_panel_back(self.dock.objectName())

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()


def install_dock_context_menu(main_window, dock: QDockWidget) -> None:
    dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show_menu(position) -> None:
        menu = QMenu(dock)
        dock_id = dock.objectName()
        if dock_id in main_window.detached_windows:
            menu.addAction("Dock back", lambda: main_window.dock_panel_back(dock_id))
            window = main_window.detached_windows[dock_id]
            menu.addAction("Maximize panel window", window.showMaximized)
            menu.addAction("Restore panel window", window.showNormal)
        else:
            menu.addAction("Detach", lambda: main_window.detach_panel(dock_id))
            menu.addAction("Hide panel", dock.hide)
        menu.addAction("Reset panel size", lambda: dock.resize(640, 420))
        menu.exec(dock.mapToGlobal(position))

    dock.customContextMenuRequested.connect(show_menu)
