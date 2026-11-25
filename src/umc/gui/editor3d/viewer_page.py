"""The 3D viewer page: top bar (files, views, display style, grid), the view, an info panel."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import MODEL_EXTS, DetectedFile
from umc.core.errors import ConversionError
from umc.core.filemode import match_mode
from umc.editor3d import logo as L
from umc.editor3d import pick
from umc.editor3d import scene as S
from umc.editor3d.mesh import ViewModel, describe, load_view_model
from umc.gui import theme
from umc.gui.editor3d.gl_view import ModelGLView
from umc.gui.editor3d.logo_panel import FACE_HINT, LogoPanel
from umc.gui.viewer.tasks import TaskRunner

log = logging.getLogger(__name__)

# Loads still running when their page went away: kept referenced until they end, because a
# QThread destroyed while running aborts the app.
_ORPHANS: list[tuple[QThread, QObject]] = []

HINT = "Drag to rotate · right-drag or Shift+drag to pan · wheel to zoom · double-click to fit"
EMPTY_TEXT = "Open a 3D model with “Open…”, or drop one here (STL, OBJ, 3MF, PLY, GLB, STEP, IGES)."
VIEW_BUTTONS = (("iso", "3D"), ("front", "Front"), ("top", "Top"), ("right", "Side"))
STYLE_BUTTONS = (("shaded", "Shaded"), ("edges", "Edges"), ("wireframe", "Wireframe"))
PICTURE_FILTER = (
    "Pictures (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.heic *.avif *.svg);;"
    "All files (*)"
)
SAVE_FILTERS = {
    "3mf": "3MF (*.3mf)",
    "stl": "STL (*.stl)",
    "obj": "OBJ (*.obj)",
    "ply": "PLY (*.ply)",
    "glb": "GLB (*.glb)",
}
REBUILD_MS = 250  # after the last setting change
_CURRENT = object()  # save_as: the file shown


@dataclass
class _Edit:
    """The logo being made or changed (not in the scene until Apply)."""

    index: int | None = None  # None = a new logo, else the one being changed
    picture: S.Picture | None = None
    point: tuple | None = None
    normal: tuple | None = None
    face_tris: list[int] | None = None
    shape: L.Shape | None = None
    shape_key: tuple | None = None
    logo: S.Logo | None = None
    preview: ViewModel | None = None
    problem: str = ""
    apply_when_traced: bool = False  # Apply was pressed while tracing


def _shape_key(pic: S.Picture, s: S.LogoSettings) -> tuple:
    return (id(pic), s.mode, s.threshold, s.invert, s.detail, s.width)


def _trace_job(pic: S.Picture, s: S.LogoSettings, colour: tuple[int, int, int]):
    """(shape or LogoError, silhouette RGBA for the preview) — runs off the UI thread."""
    m = L.mask(pic.rgba, pic.w, pic.h, s.mode, s.threshold, s.invert)
    rgba = np.zeros((pic.h, pic.w, 4), np.uint8)
    rgba[m == 1] = (*colour, 255)
    try:
        # cap triangles made here too, so placing / dragging on the UI thread stays quick
        shp: L.Shape | L.LogoError = L.prepare(L.shape_of_mask(m, s.detail, s.width))
    except L.LogoError as exc:
        shp = exc
    return shp, rgba.tobytes()


def _view_model(meshes, name: str) -> ViewModel | None:
    meshes = [m for m in meshes if len(m.faces)]
    if not meshes:
        return None
    offsets = np.cumsum([0] + [len(m.vertices) for m in meshes[:-1]])
    return ViewModel(
        name=name,
        vertices=np.concatenate([m.vertices for m in meshes]),
        faces=np.concatenate([m.faces + o for m, o in zip(meshes, offsets, strict=True)]),
        parts=len(meshes),
        watertight=True,
    )


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
    files_chosen = Signal(list)  # list[str] picked with "Open…" (the window adds + shows them)
    loaded = Signal()  # a model finished loading (or failed): for tests
    logo_built = Signal()  # the logo preview was (re)built or failed: for tests
    saved = Signal(str)  # a Save as finished (the path, or "" if it failed): for tests

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ViewerPage")
        self.files: list[DetectedFile] = []
        self.models: dict[Path, ViewModel] = {}
        self.current: DetectedFile | None = None
        self.loading = False
        self._job: tuple[QThread, _Loader] | None = None
        self._pending: DetectedFile | None = None
        self.tasks = TaskRunner(self)
        self.histories: dict[Path | None, S.History] = {None: S.History()}
        self.editing: _Edit | None = None
        self.saving = False
        self._grab_offset = np.zeros(3)  # logo point − where the drag started on its plane
        self._rebuild_timer = QTimer(self, singleShot=True, interval=REBUILD_MS)
        self._rebuild_timer.timeout.connect(self._rebuild)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(12)
        self.view = ModelGLView()
        self.view.gl_failed.connect(self._on_gl_failed)
        self.view.clicked.connect(self._on_view_clicked)
        self.view.grab_test = self._grab_test
        self.view.grab_moved.connect(self._on_grab_moved)
        self.placeholder = QLabel("", objectName="Muted")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.center = QStackedWidget()
        self.center.addWidget(self.view)
        self.center.addWidget(self.placeholder)
        body.addWidget(self.center, 1)
        self.side = QStackedWidget()
        self.side.addWidget(self._build_info())
        self.logo_panel = LogoPanel()
        self.logo_panel.changed.connect(self._on_settings_changed)
        self.logo_panel.picture_requested.connect(self.choose_picture)
        self.logo_panel.apply_requested.connect(self.apply_logo)
        self.logo_panel.cancel_requested.connect(self.cancel_logo)
        self.side.addWidget(self.logo_panel)
        body.addWidget(self.side)
        root.addLayout(body, 1)
        for keys, slot in ((QKeySequence.StandardKey.Undo, self.undo),
                           (QKeySequence.StandardKey.Redo, self.redo),
                           ("Ctrl+Y", self.redo)):  # fmt: skip
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.cancel_logo)

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
        self.open_button = button("Open…", "Open 3D models (STL, OBJ, 3MF, STEP, …)")
        self.open_button.clicked.connect(self.choose_files)
        self.file_box = QComboBox()
        self.file_box.setToolTip("Which model to show")
        self.file_box.currentIndexChanged.connect(self._on_file_chosen)
        lay.addWidget(self.file_box)
        lay.addSpacing(12)
        self.logo_button = button("Add logo…", "Turn a picture into a raised 3D logo")
        self.logo_button.clicked.connect(lambda: self.start_logo())
        self.undo_button = button("Undo", "Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo)
        self.redo_button = button("Redo", "Redo (Ctrl+Y)")
        self.redo_button.clicked.connect(self.redo)
        self.save_button = button("Save as…", "Save the model with its logos (3MF, STL, …)")
        self.save_button.clicked.connect(self.save_as)
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
        self.logos_title = QLabel("Logos", objectName="Muted")
        lay.addSpacing(8)
        lay.addWidget(self.logos_title)
        self.logo_list = QListWidget()
        self.logo_list.setMaximumHeight(120)
        self.logo_list.itemDoubleClicked.connect(lambda _i: self.edit_selected_logo())
        lay.addWidget(self.logo_list)
        row = QHBoxLayout()
        self.edit_logo_button = QPushButton("Change…", objectName="Small")
        self.edit_logo_button.clicked.connect(self.edit_selected_logo)
        self.remove_logo_button = QPushButton("Remove", objectName="Small")
        self.remove_logo_button.clicked.connect(self.remove_selected_logo)
        row.addWidget(self.edit_logo_button)
        row.addWidget(self.remove_logo_button)
        row.addStretch(1)
        lay.addLayout(row)
        self.logo_list.currentRowChanged.connect(lambda _r: self._sync_buttons())
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

    def open_empty(self) -> None:
        """Started from the editor tiles: nothing to show until a model is opened or dropped."""
        self._end_edit()
        self.files = []
        self.current = None
        self.file_box.blockSignals(True)
        self.file_box.clear()
        self.file_box.blockSignals(False)
        self.file_box.setVisible(False)
        self._fill_info(None)
        self.title.setText("")
        self._show_placeholder(EMPTY_TEXT)
        self.message.setText("")

    def choose_files(self) -> None:
        exts = " ".join(f"*.{e}" for e in sorted(MODEL_EXTS | {"stp", "igs"}))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open 3D models", "", f"3D models ({exts});;All files (*)"
        )
        if paths:
            self.files_chosen.emit(paths)

    def _select_current(self) -> None:
        """Show the current file in the file box again (without reloading it)."""
        if self.current in self.files:
            self.file_box.blockSignals(True)
            self.file_box.setCurrentIndex(self.files.index(self.current))
            self.file_box.blockSignals(False)

    def _on_file_chosen(self, index: int) -> None:
        if 0 <= index < len(self.files):
            self.show_file(self.files[index])

    def show_file(self, f: DetectedFile) -> None:
        if self.editing is not None:
            # a model dropped while a logo is being made: it waits in the list
            self._select_current()
            if f != self.current:
                self.message.setText(f"Apply or cancel the logo first, then choose {f.name} above.")
            return
        self._end_edit()
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
        if f.path not in self.histories:
            self.histories[f.path] = S.History(S.Scene(base=model))
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
        self._refresh(fit=True)

    def _fill_info(self, model: ViewModel | None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.title.setText(self.current.name if self.current else "")
        self._fill_logos()
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

    # ------------------------------------------------------------ scene
    @property
    def key(self) -> Path | None:
        return self.current.path if self.current is not None else None

    @property
    def history(self) -> S.History | None:
        """The current file's logos and their undo (None while its model is still loading)."""
        return self.histories.get(self.key)

    @property
    def base(self) -> ViewModel | None:
        h = self.history
        return h.scene.base if h is not None else None

    def _shown_scene(self) -> S.Scene | None:
        h = self.history
        if h is None:
            return None
        sc = h.scene
        if self.editing is not None and self.editing.index is not None:
            sc = sc.without(self.editing.index)  # the one being changed is the preview
        return sc

    def _refresh(self, fit: bool = False) -> None:
        """Show the current scene (+ the logo preview), fill the info panel, set buttons."""
        sc = self._shown_scene()
        combined = sc.combined(self.title_name()) if sc is not None else None
        preview = self.editing.preview if self.editing is not None else None
        first = self.view.model is None and self.view.overlay is None
        self.view.set_overlay(preview)
        self.view.set_model(combined, fit=fit or (first and (combined or preview) is not None))
        if combined is None and preview is None:
            if self.current is None:
                self._show_placeholder(EMPTY_TEXT)
        elif self.view.failed:
            self._show_gl_problem()
        else:
            self.center.setCurrentWidget(self.view)
        self._fill_info(combined)
        self._sync_buttons()

    def title_name(self) -> str:
        return self.current.name if self.current is not None else "Logo"

    def _fill_logos(self) -> None:
        h = self.history
        logos = h.scene.logos if h is not None else ()
        row = self.logo_list.currentRow()
        self.logo_list.blockSignals(True)
        self.logo_list.clear()
        for i, lg in enumerate(logos):
            where = "badge" if lg.settings.where == "badge" else "on the model"
            self.logo_list.addItem(f"{i + 1}. {lg.picture.name} ({lg.width:.0f} mm, {where})")
        if logos:
            self.logo_list.setCurrentRow(min(max(row, 0), len(logos) - 1))
        self.logo_list.blockSignals(False)
        for w in (self.logos_title, self.logo_list, self.edit_logo_button, self.remove_logo_button):
            w.setVisible(bool(logos))

    def _sync_buttons(self) -> None:
        h = self.history
        editing = self.editing is not None
        idle = not editing and not self.saving
        self.logo_button.setEnabled(idle and h is not None)
        self.undo_button.setEnabled(idle and h is not None and bool(h.undo_stack))
        self.redo_button.setEnabled(idle and h is not None and bool(h.redo_stack))
        self.save_button.setEnabled(idle and h is not None and bool(h.scene.logos))
        has = h is not None and self.logo_list.currentRow() >= 0 and bool(h.scene.logos)
        self.edit_logo_button.setEnabled(idle and has)
        self.remove_logo_button.setEnabled(idle and has)
        self.file_box.setEnabled(not editing)
        self.open_button.setEnabled(not editing)

    def _push(self, scene: S.Scene) -> None:
        self.history.push(scene)
        self._refresh()

    def undo(self) -> None:
        if self.editing is None and not self.saving and self.history and self.history.undo():
            self._refresh()

    def redo(self) -> None:
        if self.editing is None and not self.saving and self.history and self.history.redo():
            self._refresh()

    def remove_selected_logo(self) -> None:
        row = self.logo_list.currentRow()
        h = self.history
        if self.editing is None and h is not None and 0 <= row < len(h.scene.logos):
            self._push(h.scene.without(row))

    # ------------------------------------------------------------ making a logo
    def start_logo(self, index: int | None = None) -> None:
        """Open the logo panel for a new logo (or to change logo ``index``)."""
        h = self.history
        if h is None or self.editing is not None or self.saving:
            return
        e = _Edit(index=index)
        has_model = h.scene.base is not None
        self.logo_panel.set_has_model(has_model)
        if index is None:
            settings = self.logo_panel.settings()
            if not has_model:
                settings = replace(settings, where="badge")
            self.logo_panel.set_settings(settings)
            self.logo_panel.set_picture("")
            self.logo_panel.set_preview(None)
            self.logo_panel.title.setText("Add a logo")
        else:
            lg = h.scene.logos[index]
            e.picture, e.point, e.normal = lg.picture, lg.point, lg.normal
            if lg.point is not None and h.scene.base is not None:
                e.face_tris = self._face_at_point(lg.point, lg.normal)
            self.logo_panel.set_settings(lg.settings)
            self.logo_panel.set_picture(lg.picture.name)
            self.logo_panel.title.setText("Change the logo")
        self.editing = e
        self.side.setCurrentWidget(self.logo_panel)
        self.logo_panel.set_note(
            "Choose a picture: a PNG with a transparent background works best."
        )
        self._refresh()
        if e.picture is not None:
            self._rebuild()

    def edit_selected_logo(self) -> None:
        row = self.logo_list.currentRow()
        h = self.history
        if h is not None and 0 <= row < len(h.scene.logos):
            self.start_logo(row)

    def _face_at_point(self, point, normal) -> list[int] | None:
        """The flat face under a logo placed before (to check the edge again)."""
        base = self.base
        n = np.asarray(normal, float)
        found = pick.hit(base.vertices, base.faces, np.asarray(point) + n * 0.5, -n)
        return pick.flat_face(base.vertices, base.faces, found[0]).triangles if found else None

    def ask_picture_path(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a logo picture", "", PICTURE_FILTER)
        return path

    def choose_picture(self) -> None:
        if self.editing is None:
            return
        path = self.ask_picture_path()
        if path:
            self.load_picture(Path(path))

    def load_picture(self, path: Path) -> None:
        """Read the picture (SVG drawn here, others on a worker thread), then build."""
        if path.suffix.lower() in (".svg", ".svgz"):
            pic = self._svg_picture(path)
            if pic is None:
                self.logo_panel.set_note(f"Couldn't read “{path.name}” as a picture.")
                self.logo_built.emit()
            else:
                self._picture_loaded(pic)
            return
        self.logo_panel.set_note(f"Reading {path.name}…")

        def failed(_msg: str) -> None:
            self.logo_panel.set_note(f"Couldn't read “{path.name}” as a picture.")
            self.logo_built.emit()

        self.tasks.submit("picture", lambda: S.load_picture(path), self._picture_loaded, failed)

    def _svg_picture(self, path: Path) -> S.Picture | None:
        from PySide6.QtSvg import QSvgRenderer

        r = QSvgRenderer(str(path))
        if not r.isValid():
            return None
        size = r.defaultSize()
        w, h = max(size.width(), 1), max(size.height(), 1)
        k = L.MAX_SIDE / max(w, h)
        w, h = max(1, round(w * k)), max(1, round(h * k))
        img = QImage(w, h, QImage.Format.Format_RGBA8888)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        r.render(painter)
        painter.end()
        return S.Picture(path.name, bytes(img.constBits())[: w * h * 4], w, h)

    def _picture_loaded(self, pic: S.Picture) -> None:
        e = self.editing
        if e is None:
            return
        e.picture, e.shape, e.shape_key = pic, None, None
        self.logo_panel.set_picture(pic.name)
        s = self.logo_panel.settings()
        self.logo_panel.set_settings(replace(s, mode=L.default_mode(pic.rgba, pic.w, pic.h)))
        self._rebuild()

    def _on_settings_changed(self) -> None:
        if self.editing is not None:
            self._rebuild_timer.start()

    def _rebuild(self) -> None:
        """Trace again if the picture / shape settings changed (off the UI thread), else just
        place the logo again."""
        self._rebuild_timer.stop()
        e = self.editing
        if e is None or e.picture is None:
            return
        s = self.logo_panel.settings()
        key = _shape_key(e.picture, s)
        if e.shape is not None and e.shape_key == key:
            self._place()
            return
        self.logo_panel.set_note("Tracing the picture…")
        c = QColor(theme.palette()["text"])
        colour = (c.red(), c.green(), c.blue())
        pic = e.picture

        def done(result) -> None:
            if self.editing is not e:
                return
            shp, preview = result
            self.logo_panel.set_preview(preview, pic.w, pic.h)
            if isinstance(shp, L.LogoError):
                e.apply_when_traced = False
                e.shape, e.shape_key, e.logo, e.preview = None, None, None, None
                e.problem = str(shp)
                self.logo_panel.set_note(e.problem)
                self.logo_panel.set_size(0, 0)
                self._refresh()
                self.logo_built.emit()
                return
            e.shape, e.shape_key = shp, key
            self._place()
            if e.apply_when_traced:
                e.apply_when_traced = False
                self.apply_logo()

        def failed(_msg: str) -> None:
            if self.editing is e:
                e.apply_when_traced = False
                self.logo_panel.set_note("Couldn't make a logo from this picture.")
                self.logo_built.emit()

        self.tasks.submit("trace", lambda: _trace_job(pic, s, colour), done, failed)

    def _place(self) -> None:
        """Extrude + place the traced shape where it goes (quick: no tracing)."""
        e = self.editing
        s = self.logo_panel.settings()
        e.logo, e.preview, e.problem = None, None, ""
        self.logo_panel.set_size(e.shape.width, e.shape.height)
        if s.where == "face" and e.point is None:
            self.logo_panel.set_note(FACE_HINT)
        else:
            try:
                e.logo = S.build_logo(
                    e.picture, s, e.point, e.normal, self.base, e.face_tris, shp=e.shape
                )
            except L.LogoError as exc:
                e.problem = str(exc)
            else:
                e.preview = _view_model(e.logo.meshes, "logo")
            self.logo_panel.set_note(e.problem or self._logo_note(e.logo))
        self._refresh()
        self.logo_built.emit()

    @staticmethod
    def _logo_note(lg: S.Logo) -> str:
        notes = [f"{lg.width:.1f} × {lg.height:.1f} mm."]
        if lg.overhang:
            notes.append("The logo hangs over the edge of this face — make it smaller or move it.")
        if lg.dropped:
            bits = "bit" if lg.dropped == 1 else "bits"
            notes.append(f"{lg.dropped} tiny {bits} left out (too small to print).")
        if lg.settings.where == "face":
            notes.append("Drag the logo to move it, or click another face.")
        return " ".join(notes)

    def _on_view_clicked(self, x: float, y: float) -> None:
        e, base = self.editing, self.base
        if e is None or base is None or self.logo_panel.settings().where != "face":
            return
        origin, direction = pick.ray(self.view.camera, x, y, self.view.width(), self.view.height())
        found = pick.hit(base.vertices, base.faces, origin, direction)
        if found is not None:
            self.place_on(found[0], found[1], direction)

    def place_on(self, tri: int, point, view_dir=None) -> None:
        """Put the logo on the flat face around triangle ``tri`` of the model, at ``point``."""
        e, base = self.editing, self.base
        if e is None or base is None:
            return
        face = pick.flat_face(base.vertices, base.faces, tri)
        normal = face.normal
        if view_dir is not None and float(np.dot(normal, view_dir)) > 0:
            normal = -normal  # clicked the inside of a model wound the wrong way round
        e.point = tuple(float(v) for v in point)
        e.normal = tuple(float(v) for v in normal)
        e.face_tris = face.triangles
        if e.shape is not None:
            self._place()
        else:
            self._rebuild()

    def _grab_test(self, x: float, y: float) -> bool:
        e = self.editing
        if e is None or e.preview is None or e.normal is None or e.point is None:
            return False
        origin, direction = pick.ray(self.view.camera, x, y, self.view.width(), self.view.height())
        if pick.hit(e.preview.vertices, e.preview.faces, origin, direction) is None:
            return False
        at = self._on_plane(origin, direction)
        if at is None:
            return False
        self._grab_offset = np.asarray(e.point, float) - at
        return True

    def _on_grab_moved(self, x: float, y: float) -> None:
        e = self.editing
        if e is None or e.normal is None or e.shape is None:
            return
        origin, d = pick.ray(self.view.camera, x, y, self.view.width(), self.view.height())
        self.move_logo_along_ray(origin, d, self._grab_offset)

    def _on_plane(self, origin, direction) -> np.ndarray | None:
        """Where the ray meets the plane of the logo's face (None: parallel or behind)."""
        e = self.editing
        n, p0 = np.asarray(e.normal, float), np.asarray(e.point, float)
        denom = float(np.dot(direction, n))
        if abs(denom) < 1e-6:
            return None
        t = float(np.dot(p0 - np.asarray(origin, float), n)) / denom
        return np.asarray(origin, float) + np.asarray(direction, float) * t if t > 0 else None

    def move_logo_along_ray(self, origin, direction, offset=(0.0, 0.0, 0.0)) -> None:
        """Slide the logo over its face's plane to where the ray meets it (+ ``offset``: the
        logo keeps the spot it was grabbed by under the pointer)."""
        e = self.editing
        if e is None or e.normal is None or e.point is None or e.shape is None:
            return
        at = self._on_plane(origin, direction)
        if at is None:
            return
        e.point = tuple(float(v) for v in at + np.asarray(offset, float))
        self._place()

    def apply_logo(self) -> None:
        e = self.editing
        if e is None:
            return
        self._rebuild_timer.stop()
        if self._rebuild_timer_pending():
            e.apply_when_traced = True  # applied as soon as the trace is back
            return
        if e.logo is None:
            self.logo_panel.set_note(e.problem or (
                "Choose a picture first." if e.picture is None else FACE_HINT
            ))  # fmt: skip
            return
        h = self.history
        scene = h.scene.with_logo(e.logo, e.index)
        self._end_edit()
        self._push(scene)

    def _rebuild_timer_pending(self) -> bool:
        """Settings changed since the last build: build now. True when that has to trace (it
        answers on a worker thread)."""
        e = self.editing
        if e is None or e.picture is None:
            return False
        s = self.logo_panel.settings()
        if e.shape is not None and e.shape_key == _shape_key(e.picture, s):
            if e.logo is None or e.logo.settings != s:
                self._place()
            return False
        self._rebuild()
        return True

    def cancel_logo(self) -> None:
        if self.editing is not None:
            self._end_edit()
            self._refresh()

    def _end_edit(self) -> None:
        self._rebuild_timer.stop()
        self.tasks.cancel_group("trace")
        self.tasks.cancel_group("picture")
        if self.editing is not None:
            self.editing = None
            self.view.set_overlay(None)
            self.side.setCurrentIndex(0)

    # ------------------------------------------------------------ saving
    def ask_save_path(self, suggested: Path) -> str:
        filters = ";;".join(SAVE_FILTERS.values())
        path, chosen = QFileDialog.getSaveFileName(self, "Save as", str(suggested), filters)
        if path and not Path(path).suffix:
            ext = next((k for k, v in SAVE_FILTERS.items() if v == chosen), "3mf")
            path += f".{ext}"
        return path

    def ask(self, title: str, text: str, buttons: tuple[str, ...]) -> str:
        names = {
            "save": ("Save", QMessageBox.ButtonRole.AcceptRole),
            "discard": ("Don't save", QMessageBox.ButtonRole.DestructiveRole),
            "cancel": ("Cancel", QMessageBox.ButtonRole.RejectRole),
        }
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(text)
        made = {box.addButton(names[b][0], names[b][1]): b for b in buttons}
        box.setDefaultButton(next(iter(made)))
        box.exec()
        return made.get(box.clickedButton(), "cancel")

    def save_as(self, key: Path | None | object = _CURRENT) -> bool:
        """Ask where, then write a file's model with its logos on a worker thread (``key``:
        whose — by default the one shown; None = the empty viewer's badge)."""
        if key is _CURRENT:
            key = self.key
        h = self.histories.get(key)
        if h is None or not h.scene.logos or self.editing is not None or self.saving:
            return False
        src = key
        folder = src.parent if src is not None else Path.home()
        path = self.ask_save_path(folder / S.default_save_name(src, "3mf"))
        if not path:
            return False
        dest = Path(path)
        ext = dest.suffix.lower().lstrip(".")
        if ext not in S.SAVE_EXTS:
            self.message.setText("Save as 3MF, STL, OBJ, PLY or GLB.")
            return False
        scene = h.scene
        self.saving = True
        self._sync_buttons()
        self.message.setText(f"Saving {dest.name}…")

        def work() -> str:
            data = S.export(scene, ext)
            fd, tmp = tempfile.mkstemp(prefix=".umc-tmp-", dir=dest.parent)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                match_mode(tmp, dest)
                os.replace(tmp, dest)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            return str(dest)

        def done(saved_path: str) -> None:
            self.saving = False
            h.saved = scene
            self.message.setText(
                f"Saved {Path(saved_path).name} — the model and its logos as one object, "
                "centred on the print bed as shown here."
            )
            self._sync_buttons()
            self.saved.emit(saved_path)

        def failed(msg: str) -> None:
            self.saving = False
            self.message.setText(msg or f"Couldn't save {dest.name}.")
            self._sync_buttons()
            self.saved.emit("")

        def guarded() -> str:
            try:
                return work()
            except OSError as exc:
                raise ConversionError(f"Couldn't save {dest.name}: {exc.strerror or exc}.") from exc

        self.tasks.submit("save", guarded, done, failed, exclusive=False)
        return True

    def wait_saved(self, ms: int = 60000) -> None:
        """Block until a running save has reported back (leaving, tests)."""
        from PySide6.QtCore import QCoreApplication, QElapsedTimer

        clock = QElapsedTimer()
        clock.start()
        while self.saving and clock.elapsed() < ms:
            self.tasks.wait(50)
            QCoreApplication.processEvents()

    @property
    def dirty(self) -> bool:
        return any(h.dirty for h in self.histories.values())

    # ------------------------------------------------------------ leaving
    def maybe_leave(self) -> bool:
        self.cancel_logo()
        self.tasks.wait(30000)
        for key, h in list(self.histories.items()):
            if not h.dirty:
                continue
            name = key.name if key is not None else "the logo"
            answer = self.ask(
                "Unsaved logos",
                f"Save {name} with its logos first?",
                ("save", "discard", "cancel"),
            )
            if answer == "cancel":
                return False
            if answer == "save":
                if not self.save_as(key):
                    return False
                self.wait_saved()
                if h.dirty:
                    return False
        for h in self.histories.values():
            h.revert()  # "Don't save": the logos go
        self._refresh()
        return True

    def wait_idle(self, ms: int = 30000) -> None:
        """Block until a running load has finished (closing the window, tests)."""
        self.tasks.wait(ms)
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
