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


def _sync_thunar() -> None:
    """Installed on Linux: Thunar's right-click "Quick Convert" (per-user, so the .deb can't
    add it) — added once, rewritten after an update, left alone if switched off in Settings."""
    if not (sys.platform.startswith("linux") and getattr(sys, "frozen", False)):
        return
    import threading

    from PySide6.QtCore import QSettings

    from umc.tools import linux_filemanagers as lf

    settings = QSettings()
    wanted = settings.value(lf.THUNAR_SETTING, True, bool)
    written_by = settings.value(lf.THUNAR_VERSION, "", str)

    def sync() -> None:
        try:
            done = lf.sync_thunar(bool(wanted), written_by, __version__)
            if done:
                QSettings().setValue(lf.THUNAR_VERSION, done)
        except Exception:
            logging.getLogger(__name__).exception("Thunar Quick Convert setup failed")

    threading.Thread(target=sync, name="umc-thunar", daemon=True).start()


def restore_child_library_path(env: dict[str, str] | None = None) -> None:
    """The built Linux app starts with LD_LIBRARY_PATH pointing at its own libraries (the
    PyInstaller launcher sets it, and keeps the original in LD_LIBRARY_PATH_ORIG). Programs the
    app starts — the system's FFmpeg, Pandoc, LibreOffice … — would inherit it and load the
    app's older libstdc++ ("GLIBCXX_3.4.32 not found"), so it is put back for them. The app's
    own process isn't affected: the loader read the variable when it started."""
    env = os.environ if env is None else env
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux")):
        return
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)


# Set by PyInstaller's PySide6 hook to the app's own Qt folders: Qt needs them while the
# QApplication starts, programs the app starts (xdg-open → kde-open …) must not get them.
BUNDLED_QT_VARS = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH")


def forget_bundled_qt_paths(app, env: dict[str, str] | None = None) -> None:
    """After the QApplication exists (built Linux app only): Qt has its plugin folders (asking
    for them caches them), so the variables pointing at the bundled Qt are removed for child
    programs — a system Qt program could otherwise load the app's plugins and crash."""
    env = os.environ if env is None else env
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux")):
        return
    app.libraryPaths()
    for name in BUNDLED_QT_VARS:
        env.pop(name, None)


def main(argv: list[str] | None = None) -> int:
    restore_child_library_path()
    args = sys.argv[1:] if argv is None else argv[1:]
    if args[:1] == ["--self-test"]:
        from umc.core.addons import activate_all
        from umc.selftest import run

        setup_logging()
        activate_all()
        return run(Path(args[1] if len(args) > 1 else "umc-selftest.txt"))

    if args[:1] == ["--install-addon"]:  # the installer / CI: an editor from a local zip
        from umc.core.addons import run_cli as install_addon

        setup_logging()
        return install_addon(args)

    if args[:1] in (["--register"], ["--unregister"]):  # the installer / uninstaller
        from umc.tools.shell_integration import run_cli

        setup_logging()
        return run_cli(args)

    view_path = None
    if args[:1] == ["--view"]:  # "Open with → Universal Edit" runs this
        view_path = str(Path(args[1]).resolve()) if len(args) > 1 else None
    # Right-click "Quick Convert → Open in Universal Edit…" (one start per selected file)
    convert_paths = [str(Path(a).resolve()) for a in args[1:]] if args[:1] == ["--convert"] else []
    # Right-click "Quick Convert → <format>": --quick-convert <target id> FILE…
    quick = None
    if args[:1] == ["--quick-convert"] and len(args) >= 3:
        quick = {"target": args[1], "paths": [str(Path(a).resolve()) for a in args[2:]]}
    # Thunar / Nemo "Quick Convert…" (no submenus there): pick the format in a small window
    choose = [str(Path(a).resolve()) for a in args[1:]] if args[:1] == ["--quick-choose"] else []

    # The viewer plays through Qt's FFmpeg backend everywhere (Windows' own one reads far fewer
    # formats); --self-test checks that it loads.
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    from PySide6.QtWidgets import QApplication

    from umc.gui import branding, theme

    log_path = setup_logging()
    logging.getLogger(__name__).info("starting v%s, log at %s", __version__, log_path)
    _log_migration()
    from umc.core.addons import activate_all

    activate_all()  # installed editor add-ons become importable

    branding.set_windows_app_id()
    app = QApplication(sys.argv if argv is None else argv)
    forget_bundled_qt_paths(app)
    from umc.gui.gc_guard import install_gui_gc

    install_gui_gc(app)  # cyclic GC only on the GUI thread (worker-thread GC crashes PySide)
    app.setApplicationName(branding.APP_NAME)
    app.setOrganizationName(branding.ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(branding.app_icon())
    if sys.platform.startswith("linux"):
        from umc.tools.linux_desktop import APP_ID

        app.setDesktopFileName(APP_ID)  # windows belong to universal-edit.desktop (its icon)
    from umc.gui.clipboard_owner import release_clipboard

    app.aboutToQuit.connect(release_clipboard)  # else a copied picture crashes the exit

    lock = None
    if view_path is not None or convert_paths or quick is not None or choose:
        from umc.gui.single_instance import claim

        if quick is not None:
            message = {"quick": quick}
        elif choose:
            message = {"choose": choose}
        elif view_path is not None:
            message = {"view": view_path}
        else:
            message = {"convert": convert_paths}
        handed_over, lock = claim(message)
        if handed_over:
            return 0  # the running copy shows it
    _start_cleanup()
    branding.migrate_settings()
    _sync_thunar()
    app.setStyle("Fusion")
    theme.load_choice()  # the remembered System / Light / Dark, before the first window
    theme.apply(app)  # our palette too: tables / text boxes / dialogs otherwise follow the OS
    theme.notifier().changed.connect(lambda: theme.apply(app))
    from umc.gui import touch

    touch.install(app)  # finger scrolling in lists / text boxes (the mouse is unchanged)
    touch.notifier().changed.connect(lambda: theme.apply(app))

    from umc.gui.launcher import AppController

    controller = AppController(lock=lock)
    controller.start_update_check()
    if quick is not None:
        controller.quick_convert(quick["target"], quick["paths"])
    elif choose:
        controller.quick_choose(choose)
    elif view_path is not None:
        controller.view(view_path)
    elif convert_paths:
        controller.convert(convert_paths)
    else:
        files = [a for a in app.arguments()[1:] if Path(a).exists()]
        controller.show_main(files)  # "Open with…" / drag onto the .exe
    if quick is None and not choose:
        from umc.gui import default_viewer_prompt

        default_viewer_prompt.schedule()  # installed Linux app, once: open files here?
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
