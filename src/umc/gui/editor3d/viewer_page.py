"""The 3D viewer page: top bar (files, views, display style, grid), the view, an info panel."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import DetectedFile
from umc.core.errors import ConversionError
from umc.editor3d.mesh import ViewModel, describe, load_view_model
from umc.gui.editor3d.gl_view import ModelGLView

log = logging.getLogger(__name__)

# Loads still running when their page went away: kept referenced until they end, because a
# QThread destroyed while running aborts the app.
_ORPHANS: list[tuple[QThread, QObject]] = []

HINT = "Drag to rotate · right-drag or Shift+drag to pan · wheel to zoom · double-click to fit"
VIEW_BUTTONS = (("iso", "3D"), ("front", "Front"), ("top", "Top"), ("right", "Side"))
STYLE_BUTTONS = (("shaded", "Shaded"), ("edges", "Edges"), ("wireframe", "Wireframe"))


class _Loader(QObject):
    done = Signal(object, object)  # (DetectedFile, ViewModel)
    failed = Signal(object, str)

    def __init__(self, f: DetectedFile) -> None:
        super().__init__()
        self.f = f

    @Slot()
    def run(self) -> None:
        try:
            model = load_view_model(self.f.path, self.f.ext)
            model.triangle_buffer()  # heavy for big models: build them here, not while painting
            model.edge_buffer()
        except ConversionError as exc:
            self.failed.emit(self.f, str(exc))
        except Exception:
            log.exception("3D viewer: couldn't open %s", self.f.path)
            self.failed.emit(self.f, f"Couldn't open “{self.f.name}” as a 3D model.")
        else:
            self.done.emit(self.f, model)


class ViewerPage(QWidget):
    back_requested = Signal()
    loaded = Signal()  # a model finished loading (or failed): for tests

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerPage")
        self.files: list[DetectedFile] = []
        self.models: dict[Path, ViewModel] = {}
        self.current: DetectedFile | None = None
        self.loading = False
        self._job: tuple[QThread, _Loader] | None = None
        self._pending: DetectedFile | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)
        self.view = ModelGLView()
        self.view.gl_failed.connect(self._on_gl_failed)
        self.placeholder = QLabel("", objectName="Muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.center = QStackedWidget()
        self.center.addWidget(self.view)
        self.center.addWidget(self.placeholder)
        body.addWidget(self.center, 1)
        body.addWidget(self._build_info())
        root.addLayout(body, 1)

        status = QFrame(objectName="EditorStatus")
        sl = QHBoxLayout(status)
        sl.setContentsMargins(14, 6, 14, 6)
        self.message = QLabel(objectName="Muted")
        self.message.setWordWrap(True)
        sl.addWidget(self.message, 1)
        sl.addWidget(QLabel(HINT, objectName="Muted"))
        root.addWidget(status)

    # ------------------------------------------------------------ layout
    def _build_top_bar(self) -> QWidget:
        bar = QFrame(objectName="EditorTopBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)

        def button(text: str, tip: str, name: str = "Small") -> QPushButton:
            b = QPushButton(text, objectName=name)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)
            return b

        self.back_button = button("← Back to Convert", "Leave the 3D viewer", "Link")
        self.back_button.clicked.connect(self.back_requested.emit)
        lay.addSpacing(12)
        self.file_box = QComboBox()
        self.file_box.setToolTip("Which model to show")
        self.file_box.currentIndexChanged.connect(self._on_file_chosen)
        lay.addWidget(self.file_box)
        lay.addStretch(1)

        self.fit_button = button("Fit", "Show the whole model (double-click the view)")
        self.fit_button.clicked.connect(lambda: self.view.fit())
        self.view_buttons: dict[str, QPushButton] = {}
        for key, label in VIEW_BUTTONS:
            b = button(label, f"{label} view")
            b.clicked.connect(lambda _c=False, k=key: self.view.set_view(k))
            self.view_buttons[key] = b
        lay.addSpacing(12)

        self.style_group = QButtonGroup(self)
        self.style_buttons: dict[str, QPushButton] = {}
        for key, label in STYLE_BUTTONS:
            b = button(label, f"Show the model {label.lower()}", "Segment")
            b.setCheckable(True)
            self.style_group.addButton(b)
            b.clicked.connect(lambda _c=False, k=key: self.view.set_style(k))
            self.style_buttons[key] = b
        self.style_buttons["shaded"].setChecked(True)
        lay.addSpacing(12)
        self.grid_box = QCheckBox("Bed grid")
        self.grid_box.setChecked(True)
        self.grid_box.setToolTip("Grid on the print bed (Z = 0); red = X, green = Y")
        self.grid_box.toggled.connect(lambda on: self.view.set_grid(on))
        lay.addWidget(self.grid_box)
        return bar

    def _build_info(self) -> QWidget:
        card = QFrame(objectName="Card")
        card.setFixedWidth(250)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        self.title = QLabel("", objectName="Title")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 8, 0, 0)
        self.form.setVerticalSpacing(6)
        lay.addLayout(self.form)
        note = QLabel(
            "Sizes are in millimetres, as a slicer will see them. Files without a unit "
            "(STL, OBJ, PLY) are taken as mm.",
            objectName="Muted",
        )
        note.setWordWrap(True)
        lay.addSpacing(8)
        lay.addWidget(note)
        lay.addStretch(1)
        return card

    # ------------------------------------------------------------ opening
    def open_files(self, files: list[DetectedFile]) -> bool:
        """Show these models (the first one straight away). False if there are none."""
        if not files:
            self.message.setText("Add a 3D model to view it.")
            return False
        self.files = list(files)
        self.models = {p: m for p, m in self.models.items() if any(f.path == p for f in files)}
        self.file_box.blockSignals(True)
        self.file_box.clear()
        self.file_box.addItems([f.name for f in files])
        self.file_box.setCurrentIndex(0)
        self.file_box.blockSignals(False)
        self.file_box.setVisible(len(files) > 1)
        self.show_file(files[0])
        return True

    def _on_file_chosen(self, index: int) -> None:
        if 0 <= index < len(self.files):
            self.show_file(self.files[index])

    def show_file(self, f: DetectedFile) -> None:
        self.current = f
        model = self.models.get(f.path)
        if model is not None:
            self._show_model(model)
            return
        self._show_placeholder(f"Opening {f.name}…")
        self._fill_info(None)
        if self._job is not None:  # one load at a time: this one starts when that ends
            self._pending = f
            return
        thread = QThread(self)
        worker = _Loader(f)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        self._job = (thread, worker)
        self.loading = True
        thread.start()

    @Slot(object, object)
    def _on_loaded(self, f: DetectedFile, model: ViewModel) -> None:
        self.models[f.path] = model
        if self.current is not None and self.current.path == f.path:
            self._show_model(model)
        self.loaded.emit()

    @Slot(object, str)
    def _on_failed(self, f: DetectedFile, msg: str) -> None:
        if self.current is not None and self.current.path == f.path:
            self._show_placeholder(msg)
            self.message.setText(msg)
        self.loaded.emit()

    def _on_thread_finished(self) -> None:
        if self._job is not None:
            self._job[0].deleteLater()
        self._job = None
        self.loading = False
        pending, self._pending = self._pending, None
        if pending is not None and self.current == pending:
            self.show_file(pending)

    def _show_placeholder(self, text: str) -> None:
        self.placeholder.setText(text)
        self.center.setCurrentWidget(self.placeholder)

    def _on_gl_failed(self) -> None:
        """OpenGL failed when the view first appeared (e.g. shaders): say so instead."""
        if self.center.currentWidget() is self.view:
            self._show_gl_problem()

    def _show_gl_problem(self) -> None:
        self._show_placeholder(
            "The 3D view needs OpenGL, which isn't working on this computer. "
            "The model's measurements are on the right."
        )
        self.message.setText(self.view.failed)

    def _show_model(self, model: ViewModel) -> None:
        self.message.setText("")
        self.view.set_model(model)
        if self.view.failed:
            self._show_gl_problem()
        else:
            self.center.setCurrentWidget(self.view)
        self._fill_info(model)

    def _fill_info(self, model: ViewModel | None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.title.setText(self.current.name if self.current else "")
        if model is None:
            return
        for label, value in describe(model):
            v = QLabel(value)
            v.setObjectName("InfoValue")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.form.addRow(QLabel(label, objectName="Muted"), v)

    def info(self) -> dict[str, str]:
        """The info panel as {label: value} (tests)."""
        out = {}
        for r in range(self.form.rowCount()):
            label = self.form.itemAt(r, QFormLayout.ItemRole.LabelRole).widget().text()
            out[label] = self.form.itemAt(r, QFormLayout.ItemRole.FieldRole).widget().text()
        return out

    # ------------------------------------------------------------ leaving
    def maybe_leave(self) -> bool:
        return True  # the viewer changes nothing

    def wait_idle(self, ms: int = 30000) -> None:
        """Block until a running load has finished (closing the window, tests)."""
        if self._job is None:
            return
        self._pending = None
        thread, worker = self._job
        thread.quit()  # ends the thread's event loop as soon as the load returns
        if not thread.wait(ms):
            # a long STEP load: let it finish on its own instead of destroying a running thread
            worker.done.disconnect()
            worker.failed.disconnect()
            thread.finished.disconnect()
            thread.setParent(None)
            _ORPHANS.append((thread, worker))
            thread.finished.connect(lambda t=thread, w=worker: _ORPHANS.remove((t, w)))
            self._job = None
            self.loading = False
