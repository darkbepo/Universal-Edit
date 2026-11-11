"""Floating windows inside the editor: movable by their title bar, minimised back into their
rail icon, closed with ×. Several can be open at once; each remembers where it was put.

A window holds any widget — the old dialogs (``QDialog`` subclasses) are embedded as plain
child widgets, so their OK / Cancel buttons still work: accepting or rejecting closes the window.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from umc.gui import icons, theme

_KEY = "editor/panel/{}"
MARGIN = 8


class _TitleBar(QFrame):
    def __init__(self, panel: FloatingPanel, title: str, closable: bool = True) -> None:
        super().__init__(objectName="PanelTitle")
        self.panel = panel
        self._grab: QPoint | None = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 4, 4)
        lay.setSpacing(2)
        self.label = QLabel(title, objectName="PanelTitleText")
        lay.addWidget(self.label, 1)
        color = theme.palette()["muted"]
        buttons = [("minus", "Minimise (back into its icon)", panel.minimise)]
        if closable:
            buttons.append(("close", "Close", panel.close_panel))
        for name, tip, fn in buttons:
            b = QToolButton(objectName="PanelButton")
            b.setIcon(icons.icon(name, color, 14))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(fn)
            lay.addWidget(b)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._grab = e.globalPosition().toPoint() - self.panel.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.panel.raise_()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._grab is not None:
            self.panel.move_to(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._grab is not None:
            self._grab = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.panel.host.pinned.discard(self.panel.key)  # placed by hand: stays there
            self.panel.remember()


class FloatingPanel(QFrame):
    """One window: a title bar and the content widget."""

    def __init__(
        self, key: str, title: str, content: QWidget, host: PanelHost, closable: bool = True
    ) -> None:
        super().__init__(host.parent_widget)
        self.setObjectName("FloatingPanel")
        self.key, self.host, self.content, self.closable = key, host, content, closable
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title_bar = _TitleBar(self, title, closable)
        lay.addWidget(self.title_bar)
        body = QWidget(objectName="PanelBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(12, 8, 12, 12)
        if isinstance(content, QDialog):
            content.setWindowFlags(Qt.WindowType.Widget)
            content.finished.connect(lambda _r: self.close_panel())
        bl.addWidget(content)
        lay.addWidget(body)
        content.show()
        self.adjustSize()

    def set_title(self, title: str) -> None:
        self.title_bar.label.setText(title)

    def move_to(self, pos: QPoint) -> None:
        """Move, keeping the window inside the editor area."""
        area = self.parentWidget().rect()
        x = max(MARGIN, min(pos.x(), area.width() - self.width() - MARGIN))
        y = max(MARGIN, min(pos.y(), area.height() - 40))
        self.move(x, y)

    def remember(self) -> None:
        QSettings().setValue(_KEY.format(self.key), self.pos())

    def minimise(self) -> None:
        self.host.minimise(self.key)

    def close_panel(self) -> None:
        self.host.close(self.key)


class PanelHost(QObject):
    """The windows floating over ``parent_widget`` (the canvas)."""

    state_changed = Signal(str, str)  # key, "open" | "minimised" | "closed"

    def __init__(self, parent_widget: QWidget) -> None:
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.panels: dict[str, FloatingPanel] = {}
        self._on_close: dict[str, object] = {}
        self.pinned: set[str] = set()  # kept in the top-right corner until moved by hand
        self._pending: dict[str, QPoint] = {}  # saved positions waiting for the real canvas size
        parent_widget.installEventFilter(self)

    def claim_keys(self, panel: FloatingPanel) -> None:
        """Keys typed into a window belong to it: watch every widget in it (again after its
        content was rebuilt, e.g. the tool options for another tool)."""
        for w in [panel, *panel.findChildren(QWidget)]:
            w.removeEventFilter(self)
            w.installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        t = e.type()
        if t == QEvent.Type.ShortcutOverride and isinstance(obj, QWidget):
            # the editor's page-wide shortcuts (Enter = apply, letters = tools, Delete = clear…)
            # mustn't take keys from a field in a window: Enter there means OK, Esc Cancel
            if obj is not self.parent_widget:
                e.accept()
                return True
        elif obj is self.parent_widget and t == QEvent.Type.Resize:
            if self.parent_widget.width() > 100:
                for key, pos in list(self._pending.items()):
                    p = self.panels.get(key)
                    if p is not None:
                        p.move_to(pos)
                self._pending.clear()
            for key in self.pinned:
                self._to_corner(self.panels.get(key))
        return False

    def _to_corner(self, p: FloatingPanel | None) -> None:
        if p is not None:
            p.move_to(QPoint(self.parent_widget.width() - p.width() - MARGIN, MARGIN))

    def fit(self, key: str) -> None:
        """Resize a window to its content (after the content changed), once Qt has laid it out."""
        p = self.panels.get(key)
        if p is None:
            return

        def again() -> None:
            p.adjustSize()
            self.claim_keys(p)
            if key in self.pinned:
                self._to_corner(p)

        again()
        QTimer.singleShot(0, again)

    def state(self, key: str) -> str:
        p = self.panels.get(key)
        if p is None:
            return "closed"
        return "open" if p.isVisible() else "minimised"

    def open(
        self,
        key: str,
        title: str,
        content: QWidget,
        on_close=None,
        closable: bool = True,
        right: bool = False,
    ) -> FloatingPanel:
        """Show a new window (or bring back the one already open under ``key``). A window that
        isn't closable (the tool options) only minimises."""
        if key in self.panels:
            content.deleteLater()  # the one already open wins
            self.restore(key)
            return self.panels[key]
        panel = FloatingPanel(key, title, content, self, closable)
        self.panels[key] = panel
        self.claim_keys(panel)
        if on_close is not None:
            self._on_close[key] = on_close
        saved = QSettings().value(_KEY.format(key))
        if isinstance(saved, QPoint):
            panel.move_to(saved)
            if self.parent_widget.width() <= 100:  # not laid out yet: place it again then
                self._pending[key] = saved
        elif right:  # the top-right corner of the canvas, following it until moved by hand
            self.pinned.add(key)
            self._to_corner(panel)
        else:  # cascade from the top-left corner of the canvas
            n = len(self.panels) - 1
            panel.move_to(QPoint(MARGIN + 24 * (n % 8), MARGIN + 24 * (n % 8)))
        panel.show()
        panel.raise_()
        self.state_changed.emit(key, "open")
        return panel

    def minimise(self, key: str) -> None:
        p = self.panels.get(key)
        if p is not None and p.isVisible():
            p.hide()
            self.state_changed.emit(key, "minimised")

    def restore(self, key: str) -> None:
        p = self.panels.get(key)
        if p is not None:
            p.show()
            p.raise_()
            p.move_to(p.pos())  # the editor may have shrunk meanwhile
            self.state_changed.emit(key, "open")

    def toggle(self, key: str) -> bool:
        """An icon clicked again: minimise an open window, bring back a minimised one.
        False when there is no window under ``key`` (the caller opens one)."""
        state = self.state(key)
        if state == "open":
            self.minimise(key)
        elif state == "minimised":
            self.restore(key)
        return state != "closed"

    def close(self, key: str) -> None:
        p = self.panels.get(key)
        if p is None:
            return
        if not p.closable:  # the tool options only minimise (their widget lives on)
            self.minimise(key)
            return
        self.panels.pop(key)
        p.remember()
        on_close = self._on_close.pop(key, None)
        content = p.content
        if isinstance(content, QDialog) and content.result() == 0 and content.isVisible():
            content.reject()  # closed with ×: like Cancel (clears a live preview)
        p.hide()
        p.deleteLater()
        self.state_changed.emit(key, "closed")
        if callable(on_close):
            on_close()

    def close_all(self, keep: tuple[str, ...] = ()) -> None:
        for key in list(self.panels):
            if key not in keep:
                self.close(key)
