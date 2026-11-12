"""Holds the app's windows: converter windows and the one viewer window.

The viewer's Convert / Edit move its file into a converter window (a free one, else a new one);
"Open with" from a second start arrives through ``single_instance``."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject

from umc.gui.single_instance import InstanceServer

log = logging.getLogger(__name__)


class AppController(QObject):
    def __init__(self, listen: bool = True) -> None:
        super().__init__()
        self.windows: list = []  # MainWindow (imported on first use: the viewer starts faster)
        self.viewer = None
        self.server = InstanceServer(self)
        self.server.message_received.connect(self._on_message)
        if listen:
            self.server.start()

    def _on_message(self, msg: dict) -> None:
        if isinstance(msg.get("view"), str):
            self.view(msg["view"])

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
            self.viewer.convert_requested.connect(lambda p: self._move_to_main(p, edit=False))
            self.viewer.edit_requested.connect(lambda p: self._move_to_main(p, edit=True))
        self.viewer.open_path(path)
        self._bring_forward(self.viewer)

    # ------------------------------------------------------------ converter windows
    def free_main(self):
        """A converter window with nothing going on, else a new one."""
        for w in self.windows:
            if w.isVisible() and not w.files and not w.busy and w._current_editor() is None:
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

    def _move_to_main(self, path: Path, edit: bool) -> None:
        w = self.show_main([str(path)])
        if edit:
            w.enter_edit()
        if self.viewer is not None:
            self.viewer.close()

    @staticmethod
    def _bring_forward(w) -> None:
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
