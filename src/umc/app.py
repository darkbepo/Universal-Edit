"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from umc import __version__


def log_dir() -> Path:
    if sys.platform == "win32":
        from umc.core.paths import app_data_dir

        return app_data_dir()
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    from umc.core.paths import migrated_dir

    return migrated_dir(base)


def setup_logging() -> Path:
    path = log_dir() / "umc.log"
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if not getattr(sys, "frozen", False):
        root.addHandler(logging.StreamHandler())
    return path


def _log_migration() -> None:
    """Report what moving the data folders from the app's old name did (see core.paths)."""
    from umc.core.paths import MIGRATION_NOTES

    for note in MIGRATION_NOTES:
        logging.getLogger(__name__).info("rename to Universal Edit: %s", note)


def _start_cleanup() -> None:
    """Sweep leftovers of crashed runs in the background (never delays or breaks startup)."""
    import threading

    from umc.core.cleanup import sweep_leftovers
    from umc.core.paths import tools_dir

    def sweep() -> None:
        try:
            sweep_leftovers(tools=tools_dir())
        except Exception:
            logging.getLogger(__name__).exception("leftover cleanup failed")

    threading.Thread(target=sweep, name="umc-cleanup", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv[1:]
    if args[:1] == ["--self-test"]:
        from umc.selftest import run

        setup_logging()
        return run(Path(args[1] if len(args) > 1 else "umc-selftest.txt"))

    view_path = None
    if args[:1] == ["--view"]:  # "Open with → Universal Edit" runs this
        view_path = str(Path(args[1]).resolve()) if len(args) > 1 else None

    from PySide6.QtWidgets import QApplication

    from umc.gui import branding, theme

    log_path = setup_logging()
    logging.getLogger(__name__).info("starting v%s, log at %s", __version__, log_path)
    _log_migration()

    branding.set_windows_app_id()
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName(branding.APP_NAME)
    app.setOrganizationName(branding.ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(branding.app_icon())

    if view_path is not None:
        from umc.gui.single_instance import send_to_running

        if send_to_running({"view": view_path}):
            return 0  # the running window shows it
    _start_cleanup()
    branding.migrate_settings()
    app.setStyle("Fusion")
    app.setStyleSheet(theme.stylesheet())
    app.styleHints().colorSchemeChanged.connect(lambda _: app.setStyleSheet(theme.stylesheet()))

    from umc.gui.launcher import AppController

    controller = AppController()
    if view_path is not None:
        controller.view(view_path)
    else:
        files = [a for a in app.arguments()[1:] if Path(a).exists()]
        controller.show_main(files)  # "Open with…" / drag onto the .exe
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
