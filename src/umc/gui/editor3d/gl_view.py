"""OpenGL view of a ViewModel: shaded / edges / wireframe, print-bed grid, orbit controls.

Uses only what PySide6-Essentials ships (QOpenGLWidget + QOpenGLShaderProgram/Buffer) with
GLSL 1.20 shaders and no vertex array objects, so it runs on a legacy 2.1 context, Mesa's
software renderer and Qt's opengl32sw. If OpenGL can't start, ``failed`` says why and the page
shows that instead of a blank area.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QMatrix4x4,
    QOpenGLContext,
    QVector3D,
    QVector4D,
)
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from umc.editor3d.camera import OrbitCamera
from umc.editor3d.mesh import ViewModel
from umc.gui import theme

log = logging.getLogger(__name__)

GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_ZERO = 0
GL_ONE = 1
GL_POLYGON_OFFSET_FILL = 0x8037
GL_TRIANGLES = 0x0004
GL_LINES = 0x0001
GL_FLOAT = 0x1406

STYLES = ("shaded", "edges", "wireframe")
ORBIT_DEG_PER_PX = 0.4

MESH_VS = """
#version 120
attribute vec3 pos;
attribute vec3 nrm;
uniform mat4 mvp;
varying vec3 v_n;
void main() { v_n = nrm; gl_Position = mvp * vec4(pos, 1.0); }
"""
MESH_FS = """
#version 120
varying vec3 v_n;
uniform vec3 color;
uniform vec3 light;
uniform vec3 fill;
void main() {
  vec3 n = normalize(v_n);
  if (!gl_FrontFacing) n = -n;
  float d = max(dot(n, light), 0.0);
  float f = max(dot(n, fill), 0.0);
  float hemi = 0.5 + 0.5 * n.z;
  vec3 c = color * (0.22 + 0.12 * hemi + 0.62 * d + 0.18 * f);
  gl_FragColor = vec4(c, 1.0);
}
"""
LINE_VS = """
#version 120
attribute vec3 pos;
uniform mat4 mvp;
void main() { gl_Position = mvp * vec4(pos, 1.0); }
"""
LINE_FS = """
#version 120
uniform vec4 color;
void main() { gl_FragColor = color; }
"""


def grid_lines(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Bed grid on Z = 0 covering the model's footprint with a margin.

    Returns (minor lines, major lines, spacing) as float32 (2·N, 3) arrays. The spacing is
    1 / 10 / 100 mm… so there are roughly 10–100 squares across.
    """
    extent = float(max(hi[0] - lo[0], hi[1] - lo[1], 1.0))
    step = 10.0 ** math.floor(math.log10(extent / 2))
    step = max(step, 1.0)
    half = math.ceil(extent * 0.75 / step) * step
    half = max(half, step * 5)
    n = int(round(half / step))
    minor, major = [], []
    for i in range(-n, n + 1):
        v = i * step
        target = major if i % 10 == 0 else minor
        target += [(v, -half, 0), (v, half, 0), (-half, v, 0), (half, v, 0)]
    return (
        np.array(minor, np.float32).reshape(-1, 3),
        np.array(major, np.float32).reshape(-1, 3),
        step,
    )


def gl_problem() -> str:
    """Why this computer can't show OpenGL in a widget, or "" when it can."""
    if QGuiApplication.platformName() in ("offscreen", "minimal", "vnc"):
        return f"OpenGL isn't available on the “{QGuiApplication.platformName()}” display"
    if not QOpenGLContext().create():
        return "OpenGL couldn't start"
    return ""


def _qmatrix(m: np.ndarray) -> QMatrix4x4:
    return QMatrix4x4(*m.astype(float).reshape(-1).tolist())  # row-major values


class ModelGLView(QOpenGLWidget):
    camera_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ModelView")
        self.setMinimumSize(200, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.camera = OrbitCamera()
        self.model: ViewModel | None = None
        self.style = "shaded"
        self.show_grid = True
        self.failed = gl_problem()  # why OpenGL couldn't start, if it couldn't
        self._ready = False
        self._dirty = False  # the model changed: upload buffers before the next paint
        self._buffers: dict[str, tuple[QOpenGLBuffer, int]] = {}
        self._drag: tuple[QPointF, Qt.MouseButton, Qt.KeyboardModifier] | None = None
        self._mesh_prog: QOpenGLShaderProgram | None = None
        self._line_prog: QOpenGLShaderProgram | None = None

    # ------------------------------------------------------------ public
    def set_model(self, model: ViewModel | None) -> None:
        self.model = model
        self._dirty = True
        self.fit()

    def set_style(self, style: str) -> None:
        if style not in STYLES:
            raise ValueError(style)
        self.style = style
        self.update()

    def set_grid(self, on: bool) -> None:
        self.show_grid = on
        self.update()

    def set_view(self, name: str) -> None:
        self.camera.set_view(name)
        self.fit()

    def fit(self) -> None:
        if self.model is not None:
            lo, hi = self.model.bounds
            self.camera.fit(lo, hi, self.width() / max(self.height(), 1))
        self.camera_changed.emit()
        self.update()

    def mvp(self) -> np.ndarray:
        aspect = self.width() / max(self.height(), 1)
        return self.camera.projection_matrix(aspect) @ self.camera.view_matrix()

    # ------------------------------------------------------------ GL
    def initializeGL(self) -> None:  # noqa: N802
        try:
            self._mesh_prog = self._program(MESH_VS, MESH_FS, ("pos", "nrm"))
            self._line_prog = self._program(LINE_VS, LINE_FS, ("pos",))
            self._ready = True
            self._dirty = True
        except Exception as exc:  # no usable OpenGL on this computer
            log.exception("3D view: OpenGL setup failed")
            self.failed = str(exc) or "OpenGL couldn't start"

    def _program(self, vs: str, fs: str, attrs: tuple[str, ...]) -> QOpenGLShaderProgram:
        p = QOpenGLShaderProgram(self)
        ok = p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vs)
        ok = ok and p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fs)
        for i, name in enumerate(attrs):
            p.bindAttributeLocation(name, i)
        if not (ok and p.link()):
            raise RuntimeError(f"OpenGL shaders failed: {p.log().strip()}")
        return p

    def _upload(self, key: str, data: np.ndarray) -> None:
        old = self._buffers.pop(key, None)
        if old is not None:
            old[0].destroy()
        if data is None or not len(data):
            return
        data = np.ascontiguousarray(data, dtype=np.float32)
        buf = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        buf.create()
        buf.bind()
        buf.allocate(data.tobytes(), data.nbytes)
        buf.release()
        self._buffers[key] = (buf, len(data))

    def _upload_all(self) -> None:
        m = self.model
        self._upload("mesh", m.triangle_buffer() if m is not None else None)
        self._buffers.pop("edges", None)  # made on first use (can be slow for big models)
        lo, hi = m.bounds if m is not None else (np.zeros(3) - 50, np.zeros(3) + 50)
        minor, major, _step = grid_lines(lo, hi)
        self._upload("grid_minor", minor)
        self._upload("grid_major", major)
        ext = float(np.abs(major).max()) if len(major) else 50.0
        self._upload("axes", np.array([(0, 0, 0), (ext, 0, 0), (0, 0, 0), (0, ext, 0)], np.float32))
        self._dirty = False

    def _draw(self, prog: QOpenGLShaderProgram, key: str, mode: int, stride: int, normals: bool):
        item = self._buffers.get(key)
        if item is None:
            return
        buf, count = item
        f = self.context().functions()
        buf.bind()
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        if normals:
            prog.enableAttributeArray(1)
            prog.setAttributeBuffer(1, GL_FLOAT, 12, 3, stride)
        f.glDrawArrays(mode, 0, count)
        prog.disableAttributeArray(0)
        if normals:
            prog.disableAttributeArray(1)
        buf.release()

    def _lines(self, key: str, color: QColor, alpha: float = 1.0) -> None:
        prog = self._line_prog
        prog.setUniformValue("color", QVector4D(color.redF(), color.greenF(), color.blueF(), alpha))
        self._draw(prog, key, GL_LINES, 12, False)

    def paintGL(self) -> None:  # noqa: N802
        if not self._ready:
            return
        f = self.context().functions()
        p = theme.palette()
        bg = QColor(p["bg"])
        f.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self._dirty:
            self._upload_all()
        mvp = _qmatrix(self.mvp())
        f.glEnable(GL_DEPTH_TEST)
        f.glEnable(GL_BLEND)
        # keep the framebuffer opaque: Qt composites the widget with its alpha channel
        f.glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ZERO, GL_ONE)

        if self.model is not None and self.style in ("shaded", "edges"):
            prog = self._mesh_prog
            prog.bind()
            prog.setUniformValue("mvp", mvp)
            accent = QColor(p["accent"])
            base = QColor.fromHslF(accent.hslHueF(), 0.25, 0.62)
            prog.setUniformValue("color", QVector3D(base.redF(), base.greenF(), base.blueF()))
            cam = self.camera
            key = cam.direction() * 0.8 + cam.up() * 0.45 + cam.right() * -0.35
            key /= np.linalg.norm(key)
            prog.setUniformValue("light", QVector3D(*key.tolist()))
            fill = cam.direction() * 0.5 + cam.right() * 0.8
            fill /= np.linalg.norm(fill)
            prog.setUniformValue("fill", QVector3D(*fill.tolist()))
            f.glEnable(GL_POLYGON_OFFSET_FILL)
            f.glPolygonOffset(1.0, 1.0)
            self._draw(prog, "mesh", GL_TRIANGLES, 24, True)
            f.glDisable(GL_POLYGON_OFFSET_FILL)
            prog.release()

        prog = self._line_prog
        prog.bind()
        prog.setUniformValue("mvp", mvp)
        if self.model is not None and self.style in ("edges", "wireframe"):
            if "edges" not in self._buffers:
                self._upload("edges", self.model.edge_buffer())
            edge = QColor(p["text"]) if self.style == "wireframe" else QColor(p["muted"])
            self._lines("edges", edge, 0.85 if self.style == "wireframe" else 0.35)
        if self.show_grid:
            line = QColor(p["muted"])
            self._lines("grid_minor", line, 0.18)
            self._lines("grid_major", line, 0.4)
            self._lines_axes()
        prog.release()

    def _lines_axes(self) -> None:
        item = self._buffers.get("axes")
        if item is None:
            return
        buf, _count = item
        prog, f = self._line_prog, self.context().functions()
        buf.bind()
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, 12)
        prog.setUniformValue("color", QVector4D(0.86, 0.30, 0.30, 0.9))  # X red
        f.glDrawArrays(GL_LINES, 0, 2)
        prog.setUniformValue("color", QVector4D(0.35, 0.72, 0.38, 0.9))  # Y green
        f.glDrawArrays(GL_LINES, 2, 2)
        prog.disableAttributeArray(0)
        buf.release()

    def resizeGL(self, w: int, h: int) -> None:  # noqa: N802
        self.camera_changed.emit()

    # ------------------------------------------------------------ mouse
    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._drag = (event.position(), event.button(), event.modifiers())
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            return
        last, button, mods = self._drag
        pos = event.position()
        dx, dy = pos.x() - last.x(), pos.y() - last.y()
        self._drag = (pos, button, mods)
        panning = button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or bool(
            mods & Qt.KeyboardModifier.ShiftModifier
        )
        if panning:
            self.camera.pan(dx, dy, self.height())
        else:
            self.camera.orbit(-dx * ORBIT_DEG_PER_PX, dy * ORBIT_DEG_PER_PX)
        self.camera_changed.emit()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.fit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120
        if steps:
            self.camera.zoom(0.85**steps)
            self.camera_changed.emit()
            self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        k = event.key()
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.camera.zoom(0.85)
        elif k == Qt.Key.Key_Minus:
            self.camera.zoom(1 / 0.85)
        elif k == Qt.Key.Key_Left:
            self.camera.orbit(-15, 0)
        elif k == Qt.Key.Key_Right:
            self.camera.orbit(15, 0)
        elif k == Qt.Key.Key_Up:
            self.camera.orbit(0, 15)
        elif k == Qt.Key.Key_Down:
            self.camera.orbit(0, -15)
        else:
            super().keyPressEvent(event)
            return
        self.camera_changed.emit()
        self.update()
