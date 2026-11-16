"""Holds the app's windows: converter windows and the one viewer window.

The viewer's Convert / Edit move its file into a converter window (a free one, else a new one);
"Open with" from a second start arrives through ``single_instance``."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QObject

from umc.gui.single_instance import InstanceServer

log = logging.getLogger(__name__)

GROUP_SECONDS = 3.0  # right-clicked files arriving this close together share one window


class AppController(QObject):
    def __init__(self, listen: bool = True, lock=None) -> None:
        super().__init__()
        self.windows: list = []  # MainWindow (imported on first use: the viewer starts faster)
        self.viewer = None
        self._convert_window = None  # where right-clicked files went last, and when
        self._convert_time = 0.0
        self.quick = None  # the Quick Convert window (one, reused)
        self.server = InstanceServer(self)
        self.server.message_received.connect(self._on_message)
        if listen:
            self.server.start(lock)

    def _on_message(self, msg: dict) -> None:
        if isinstance(msg.get("view"), str):
            self.view(msg["view"])
        elif isinstance(msg.get("convert"), list):
            self.convert([p for p in msg["convert"] if isinstance(p, str)])
        elif isinstance(msg.get("quick"), dict):
            q = msg["quick"]
            paths = q.get("paths")
            if isinstance(q.get("target"), str) and isinstance(paths, list):
                self.quick_convert(q["target"], [p for p in paths if isinstance(p, str)])
        elif isinstance(msg.get("choose"), list):
            self.quick_choose([p for p in msg["choose"] if isinstance(p, str)])

    # ------------------------------------------------------------ Quick Convert
    def _quick_window(self):
        if self.quick is None:
            from umc.gui.quick_convert import QuickConvertWindow

            self.quick = QuickConvertWindow()
            self.quick.open_in_app.connect(self.convert)
        return self.quick

    def quick_choose(self, paths: list[str]) -> None:
        """Thunar / Nemo "Quick Convert…": pick the format in a small window."""
        w = self._quick_window()
        if w.done:
            w.items.clear()  # a new round: last time's results aren't listed again
            w.index = 0
        w.choose(paths)  # (while files are converting, the picker comes once they're done)
        self._bring_forward(w)

    def quick_convert(self, target_id: str, paths: list[str]) -> None:
        """Right-click "Quick Convert → <format>": converted at once, next to the originals.
        Every file goes to the one Quick Convert window (Explorer starts us once per file)."""
        w = self._quick_window()
        if w.done:
            w.items.clear()  # a new round: last time's results aren't listed again
            w.index = 0
        w.add(target_id, paths)
        self._bring_forward(w)

    # ------------------------------------------------------------ viewer
    def view(self, path: str | Path) -> None:
        path = Path(path)
        if not path.is_file():
            log.warning("viewer: %s is not a file", path)
            self.show_main()
            return
        if self.viewer is None:
            from umc.gui.viewer.viewer_window import ViewerWindow

            self.viewer = ViewerWindow()
            self.viewer.convert_requested.connect(lambda p: self._move_to_main(p, "convert"))
            self.viewer.edit_requested.connect(lambda p: self._move_to_main(p, "edit"))
            self.viewer.properties_requested.connect(lambda p: self._move_to_main(p, "properties"))
        self.viewer.open_path(path)
        self._bring_forward(self.viewer)

    # ------------------------------------------------------------ converter windows
    def free_main(self):
        """A converter window with nothing going on, else a new one."""
        for w in self.windows:  # a closed (hidden) window is reused too
            if not w.files and not w.busy and w._current_editor() is None:
                return w
        from umc.gui.main_window import MainWindow

        w = MainWindow()
        self.windows.append(w)
        return w

    def show_main(self, files: list[str] | None = None):
        w = self.free_main()
        if files:
            w.add_files(files)
        self._bring_forward(w)
        return w

    def convert(self, paths: list[str]) -> None:
        """Right-click "Quick Convert → Open in Universal Edit…" (Linux: "Convert with Universal
        Edit" in Open with). Explorer starts us once per selected file,
        so files arriving close together join the same converter window."""
        now = time.monotonic()
        w = self._convert_window
        if w is not None and now - self._convert_time < GROUP_SECONDS and not w.busy:
            w.add_files(paths)
            self._bring_forward(w)
        else:
            w = self.show_main(paths)
        self._convert_window, self._convert_time = w, now

    def _move_to_main(self, path: Path, mode: str) -> None:
        w = self.show_main([str(path)])
        if mode == "edit":
            w.enter_edit()
        elif mode == "properties":
            w.enter_properties()
        if self.viewer is not None:
            self.viewer.close()

    @staticmethod
    def _bring_forward(w) -> None:
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
