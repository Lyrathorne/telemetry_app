import sys
import logging

from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_NAME
from app.crash_report import write_crash_report
from app.logging_config import configure_logging


def main() -> int:
    log_path = configure_logging()

    try:
        if "--diagnostics" in sys.argv:
            from app.diagnostics import run_diagnostics

            return run_diagnostics()

        if "--smoke-test" in sys.argv:
            from app.smoke import run_smoke_test

            return run_smoke_test()

        from ui.main_window import MainWindow

        if "--diagnostics-acc-timing" in sys.argv:
            import os

            os.environ["RACING_TELEMETRY_ACC_TIMING_DIAGNOSTICS"] = "1"

        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        window = MainWindow(reset_layout="--reset-layout" in sys.argv)

        if "--demo" in sys.argv:
            window.select_source("demo")
            window.start_selected_source()

        window.show()
        exit_code = app.exec()
        logging.info("Application shutdown complete with exit code %s", exit_code)
        return exit_code
    except Exception as error:
        crash_path = write_crash_report(error)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("\nUnhandled startup error\n")
            log_file.write(f"Crash report: {crash_path}\n")

        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)

        QMessageBox.critical(
            None,
            "Racing Telemetry startup error",
            f"Racing Telemetry could not start.\n\n{error}\n\nLog file:\n{log_path}\nCrash report:\n{crash_path}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
