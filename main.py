import sys
import traceback
import logging

from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_NAME
from app.logging_config import configure_logging


def main() -> int:
    log_path = configure_logging()

    try:
        if "--diagnostics" in sys.argv:
            from app.diagnostics import run_diagnostics

            return run_diagnostics()

        from ui.main_window import MainWindow

        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        window = MainWindow()

        if "--demo" in sys.argv:
            window.select_source("demo")
            window.start_selected_source()

        window.show()
        exit_code = app.exec()
        logging.info("Application shutdown complete with exit code %s", exit_code)
        return exit_code
    except Exception as error:
        traceback_text = "".join(traceback.format_exception(error))
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("\nUnhandled startup error\n")
            log_file.write(traceback_text)

        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        QMessageBox.critical(
            None,
            "Racing Telemetry startup error",
            f"Racing Telemetry could not start.\n\n{error}\n\nLog file:\n{log_path}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
