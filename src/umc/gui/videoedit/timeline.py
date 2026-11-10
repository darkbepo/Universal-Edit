"""The timeline: ruler, text rows, the main track and music rows, painted by hand.

The widget never changes a project. It computes the new one with ``umc.videoedit.edits`` and
emits ``edit(project, label)``; the page pushes it to the history and calls ``set_project``.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from umc.gui import theme
from umc.videoedit import edits
from umc.videoedit.project import (
    AudioClip,
    PictureClip,
    Project,
    Span,
    TextItem,
    VideoClip,
    layout,
)

SOURCE_MIME = "application/x-umc-source"
HEADER_WIDTH = 80  # left column: row labels and the "+ row" buttons
RULER_H = 24
TEXT_H = 28
MAIN_H = 64
MUSIC_H = 40
WAVE_H = 16
EDGE = 6
HANDLE = 12
HANDLE_H = 20  # the transition handle is a tab at the top of the main row
SNAP_PX = 8
DRAG_PX = 3
MIN_PPS, MAX_PPS, DEFAULT_PPS = 5.0, 800.0, 80.0
ZOOM_STEP = 1.25

Part = Literal["body", "left", "right", "transition"]


@dataclasses.dataclass(frozen=True)
class HitResult:
    item_id: str
    part: Part


class TimelineView(QWidget):
    edit = Signal(object, str)
    seek = Signal(float)
    selection_changed = Signal(object)
    source_dropped = Signal(str, float, str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project: Project | None = None
        self.playhead = 0.0
        self.pixels_per_second = DEFAULT_PPS
        self.scroll_seconds = 0.0
        self.selected_id: str | None = None
        self._spans: dict[str, Span] = {}
        self._thumbs: dict[str, list[QImage]] = {}
        self._waves: dict[str, list[float]] = {}
        self._drag: dict | None = None
        self._seeking = False
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._update_size()

    # ---- state ----------------------------------------------------------------------------
    def set_project(self, project: Project) -> None:
        self.project = project
        self._spans = layout(project)
        sel = self.selected_id
        if sel is not None:
            key = sel[3:] if sel.startswith("tr:") else sel
            if key not in self._spans:
                self.selected_id = None
        self._drag = None
        self._update_size()
        self.update()

    def set_playhead(self, t: float) -> None:
        self.playhead = max(0.0, t)
        self.update()

    def set_selected(self, item_id: str | None) -> None:
        if item_id != self.selected_id:
            self.selected_id = item_id
            self.selection_changed.emit(item_id)
            self.update()

    def set_thumbnails(self, source_id: str, images: list[QImage]) -> None:
        self._thumbs[source_id] = images
        self.update()

    def set_waveform(self, source_id: str, peaks: list[float]) -> None:
        self._waves[source_id] = peaks
        self.update()

    def zoom_in(self) -> None:
        self._zoom(ZOOM_STEP)

    def zoom_out(self) -> None:
        self._zoom(1 / ZOOM_STEP)

    def _zoom(self, factor: float, anchor_x: float | None = None) -> None:
        if anchor_x is None:
            anchor_x = HEADER_WIDTH + (self.width() - HEADER_WIDTH) / 2
        anchor_t = self.t_for(anchor_x)
        self.pixels_per_second = max(MIN_PPS, min(MAX_PPS, self.pixels_per_second * factor))
        self.scroll_seconds = max(
            0.0, anchor_t - (anchor_x - HEADER_WIDTH) / self.pixels_per_second
        )
        self.update()

    # ---- geometry -------------------------------------------------------------------------
    def x_for(self, t: float) -> int:
        return round((t - self.scroll_seconds) * self.pixels_per_second + HEADER_WIDTH)

    def t_for(self, x: float) -> float:
        return (x - HEADER_WIDTH) / self.pixels_per_second + self.scroll_seconds

    def _rows(self) -> list[tuple[str, int, int, int]]:
        """(kind, row, y, height) for every row, top to bottom (the ruler is not a row)."""
        p = self.project
        n_text = len(p.text_rows) if p else 1
        n_music = len(p.music_rows) if p else 1
        rows: list[tuple[str, int, int, int]] = []
        y = RULER_H
        for r in range(n_text):
            rows.append(("text", r, y, TEXT_H))
            y += TEXT_H
        rows.append(("main", 0, y, MAIN_H))
        y += MAIN_H
        for r in range(n_music):
            rows.append(("music", r, y, MUSIC_H))
            y += MUSIC_H
        return rows

    def _total_height(self) -> int:
        r = self._rows()[-1]
        return r[2] + r[3]

    def _update_size(self) -> None:
        self.setMinimumHeight(self._total_height())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(900, self._total_height())

    def row_y(self, kind: str, row: int = 0) -> int:
        for k, r, y, _h in self._rows():
            if k == kind and r == row:
                return y
        raise KeyError((kind, row))

    def _row_at(self, y: float) -> tuple[str, int, int, int] | None:
        for k, r, ry, h in self._rows():
            if ry <= y < ry + h:
                return k, r, ry, h
        return None

    def _span_rect(self, span: Span) -> QRect:
        y = self.row_y(span.row_kind, span.row)
        h = {"main": MAIN_H, "text": TEXT_H, "music": MUSIC_H}[span.row_kind]
        x0, x1 = self.x_for(span.start), self.x_for(span.end)
        return QRect(x0, y + 2, max(2, x1 - x0), h - 4)

    def add_row_rect(self, kind: str) -> QRect:
        return QRect(HEADER_WIDTH - 22, self.row_y(kind, 0) + 4, 18, 18)

    # ---- hit testing ----------------------------------------------------------------------
    def _boundary_x(self, index: int) -> int | None:
        """Pixel x of the boundary before main clip ``index`` (centre of any overlap)."""
        if not self.project or index <= 0 or index >= len(self.project.main):
            return None
        cur = self._spans[self.project.main[index].id]
        prev = self._spans[self.project.main[index - 1].id]
        return self.x_for((cur.start + prev.end) / 2)

    def hit_test(self, pos: QPoint) -> HitResult | None:
        if self.project is None or pos.x() < HEADER_WIDTH:
            return None
        row = self._row_at(pos.y())
        if row is None:
            return None
        kind, r, ry, _h = row
        if kind == "main" and pos.y() - ry < HANDLE_H:
            for i, item in enumerate(self.project.main):
                bx = self._boundary_x(i)
                if bx is not None and abs(pos.x() - bx) <= HANDLE // 2:
                    return HitResult(item.id, "transition")
        hit: HitResult | None = None
        for item_id, span in self._spans.items():
            if span.row_kind != kind or span.row != r:
                continue
            x0, x1 = self.x_for(span.start), self.x_for(span.end)
            if not x0 <= pos.x() <= x1:
                continue
            edge = min(EDGE, (x1 - x0) // 3)
            if pos.x() - x0 < edge:
                part: Part = "left"
            elif x1 - pos.x() <= edge:
                part = "right"
            else:
                part = "body"
            hit = HitResult(item_id, part)  # later items are painted on top
        return hit

    def select_at(self, pos: QPoint) -> HitResult | None:
        hit = self.hit_test(pos)
        if hit is None:
            self.set_selected(None)
        elif hit.part == "transition":
            self.set_selected(f"tr:{hit.item_id}")
        else:
            self.set_selected(hit.item_id)
        return hit

    # ---- snapping -------------------------------------------------------------------------
    def _snap(self, t: float, exclude: str, alt: bool) -> float:
        if alt:
            return t
        best, dist = t, SNAP_PX / self.pixels_per_second
        points = [self.playhead, 0.0]
        for item_id, s in self._spans.items():
            if item_id != exclude:
                points += [s.start, s.end]
        for p in points:
            if abs(p - t) <= dist:
                best, dist = p, abs(p - t)
        return best

    def _snap_move(self, start: float, dur: float, exclude: str, alt: bool) -> float:
        a = self._snap(start, exclude, alt)
        if a != start:
            return a
        b = self._snap(start + dur, exclude, alt)
        return b - dur if b != start + dur else start

    # ---- mouse ----------------------------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        for kind in ("text", "music"):
            if self.project is not None and self.add_row_rect(kind).contains(pos):
                self.edit.emit(edits.add_row(self.project, kind), "Add row")
                return
        if pos.y() < RULER_H:
            self._seeking = True
            self._seek_to(pos.x())
            return
        hit = self.select_at(pos)
        if hit is None:
            if pos.x() >= HEADER_WIDTH:
                self._seek_to(pos.x())
            return
        if hit.part == "transition":
            return
        self._drag = {
            "id": hit.item_id,
            "part": hit.part,
            "span": self._spans[hit.item_id],
            "origin": pos,
            "pos": pos,
            "moved": False,
            "alt": bool(e.modifiers() & Qt.KeyboardModifier.AltModifier),
        }

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        pos = e.position().toPoint()
        if self._seeking:
            self._seek_to(pos.x())
            return
        d = self._drag
        if d is None:
            hit = self.hit_test(pos)
            if hit and hit.part in ("left", "right"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit and hit.part == "transition":
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            return
        d["pos"] = pos
        d["alt"] = d["alt"] or bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        if not d["moved"] and (pos - d["origin"]).manhattanLength() >= DRAG_PX:
            d["moved"] = True
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._seeking = False
        d, self._drag = self._drag, None
        self.update()
        if d is None or not d["moved"] or self.project is None:
            return
        d["pos"] = e.position().toPoint()
        d["alt"] = d["alt"] or bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
        try:
            result = self._compute(d)
        except edits.EditError:
            return  # refused: cancel the drag silently
        self.edit.emit(*result)

    def _seek_to(self, x: float) -> None:
        self.seek.emit(max(0.0, self.t_for(x)))

    def _dt(self, d: dict) -> float:
        return (d["pos"].x() - d["origin"].x()) / self.pixels_per_second

    def _compute(self, d: dict) -> tuple[Project, str]:
        assert self.project is not None
        item_id, part, span = d["id"], d["part"], d["span"]
        alt = d["alt"]
        dt = self._dt(d)
        if part in ("left", "right"):
            edge: Literal["start", "end"] = "start" if part == "left" else "end"
            cur = span.start if part == "left" else span.end
            target = self._snap(cur + dt, item_id, alt)
            return edits.trim(self.project, item_id, edge, target - cur), "Trim clip"
        if span.row_kind == "main":
            return edits.move_main(self.project, item_id, self._main_index(d)), "Move clip"
        row = self._row_at(d["pos"].y())
        new_row = row[1] if row and row[0] == span.row_kind else span.row
        start = self._snap_move(max(0.0, span.start + dt), span.end - span.start, item_id, alt)
        label = "Move text" if span.row_kind == "text" else "Move music"
        return edits.move_item(self.project, item_id, start, new_row), label

    def _main_index(self, d: dict) -> int:
        """Where the dragged main clip would land: after every other clip centred before it."""
        assert self.project is not None
        t = self.t_for(d["pos"].x())
        index = 0
        for m in self.project.main:
            if m.id != d["id"]:
                s = self._spans[m.id]
                if (s.start + s.end) / 2 < t:
                    index += 1
        return index

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        delta = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._zoom(ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP, e.position().x())
        else:
            shift = delta / 120 * 60 / self.pixels_per_second
            self.scroll_seconds = max(0.0, self.scroll_seconds - shift)
            self.update()
        e.accept()

    # ---- drag & drop of sources from the media bin -----------------------------------------
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(SOURCE_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(SOURCE_MIME):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        if not e.mimeData().hasFormat(SOURCE_MIME):
            return
        source_id = bytes(e.mimeData().data(SOURCE_MIME)).decode("utf-8")
        pos = e.position().toPoint()
        row = self._row_at(pos.y())
        kind, r = ("main", 0) if row is None else (row[0], row[1])
        self.source_dropped.emit(source_id, max(0.0, self.t_for(pos.x())), kind, r)
        e.acceptProposedAction()

    # ---- painting -------------------------------------------------------------------------
    @staticmethod
    def _colour(item: object, pal: dict[str, str]) -> QColor:
        if isinstance(item, PictureClip):
            return QColor(pal["clip_picture"])
        if isinstance(item, AudioClip):
            return QColor(pal["clip_audio"])
        if isinstance(item, TextItem):
            return QColor(pal["clip_text"])
        return QColor(pal["clip_video"])

    def paintEvent(self, _e) -> None:  # noqa: N802
        pal = theme.palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(pal["bg"]))
        if self.project is None:
            return
        self._paint_rows(p, pal)
        p.save()
        p.setClipRect(QRect(HEADER_WIDTH, 0, self.width() - HEADER_WIDTH, self.height()))
        self._paint_ruler(p, pal)
        self._paint_items(p, pal)
        self._paint_ghost(p, pal)
        x = self.x_for(self.playhead)
        p.setPen(QPen(QColor(pal["playhead"]), 2))
        p.drawLine(x, 0, x, self.height())
        p.restore()
        self._paint_header(p, pal)

    def _paint_rows(self, p: QPainter, pal: dict[str, str]) -> None:
        for i, (_k, _r, y, h) in enumerate(self._rows()):
            colour = QColor(pal["surface" if i % 2 else "surface_alt"])
            p.fillRect(0, y, self.width(), h - 1, colour)

    def _paint_header(self, p: QPainter, pal: dict[str, str]) -> None:
        assert self.project is not None
        p.fillRect(0, 0, HEADER_WIDTH, self.height(), QColor(pal["bg"]))
        f = QFont(p.font())
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor(pal["muted"]))
        counts = {"text": len(self.project.text_rows), "music": len(self.project.music_rows)}
        for kind, r, y, h in self._rows():
            name = {"text": "Text", "main": "Video", "music": "Music"}[kind]
            label = f"{name} {r + 1}" if counts.get(kind, 1) > 1 else name
            p.drawText(QRect(8, y, HEADER_WIDTH - 32, h), Qt.AlignmentFlag.AlignVCenter, label)
        for kind in ("text", "music"):
            rect = self.add_row_rect(kind)
            p.setPen(QPen(QColor(pal["border"]), 1))
            p.setBrush(QColor(pal["surface"]))
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QColor(pal["text"]))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "+")

    def _paint_ruler(self, p: QPainter, pal: dict[str, str]) -> None:
        p.fillRect(HEADER_WIDTH, 0, self.width(), RULER_H, QColor(pal["bg"]))
        step = 600.0
        for cand in (0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300, 600):
            if cand * self.pixels_per_second >= 60:
                step = cand
                break
        f = QFont(p.font())
        f.setPixelSize(10)
        p.setFont(f)
        t = (self.scroll_seconds // step) * step
        end = self.t_for(self.width())
        while t <= end:
            x = self.x_for(t)
            p.setPen(QColor(pal["border"]))
            p.drawLine(x, RULER_H - 8, x, RULER_H)
            p.setPen(QColor(pal["muted"]))
            m, s = divmod(t, 60)
            p.drawText(x + 3, 12, f"{int(m)}:{s:04.1f}" if step < 1 else f"{int(m)}:{int(s):02d}")
            t += step

    def _paint_items(self, p: QPainter, pal: dict[str, str]) -> None:
        proj = self.project
        assert proj is not None
        items: dict[str, object] = {m.id: m for m in proj.main}
        for row in proj.text_rows:
            items.update({t.id: t for t in row})
        for mrow in proj.music_rows:
            items.update({a.id: a for a in mrow})
        for item_id, span in self._spans.items():
            self._paint_clip(
                p, pal, items[item_id], self._span_rect(span), item_id == self.selected_id
            )
        for i, m in enumerate(proj.main):
            bx = self._boundary_x(i)
            if bx is None:
                continue
            sel = self.selected_id == f"tr:{m.id}"
            r = QRect(bx - HANDLE // 2, self.row_y("main") + 2, HANDLE, HANDLE_H - 4)
            p.setPen(QPen(QColor(pal["accent" if sel else "border"]), 1))
            filled = m.transition_in.kind != "none" or sel
            p.setBrush(QColor(pal["accent"] if filled else pal["surface"]))
            p.drawRoundedRect(r, 3, 3)

    def _paint_clip(
        self, p: QPainter, pal: dict[str, str], item: object, rect: QRect, selected: bool
    ) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._colour(item, pal))
        p.drawRoundedRect(rect, 5, 5)
        p.save()
        p.setClipRect(rect)
        source_id = getattr(item, "source", None)
        if isinstance(item, (VideoClip, PictureClip)) and source_id in self._thumbs:
            self._paint_thumbs(p, self._thumbs[source_id], rect)
        if isinstance(item, (VideoClip, AudioClip)) and source_id in self._waves:
            wave_h = WAVE_H if isinstance(item, VideoClip) else rect.height() - 12
            wave = QRect(rect.left(), rect.bottom() - wave_h, rect.width(), wave_h)
            self._paint_wave(p, self._waves[source_id], wave, item)
        f = QFont(p.font())
        f.setPixelSize(11)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(rect.adjusted(6, 3, -4, 0), Qt.AlignmentFlag.AlignTop, self._label(item))
        p.restore()
        if selected:
            p.setPen(QPen(QColor(pal["text"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 5, 5)

    def _label(self, item: object) -> str:
        if isinstance(item, TextItem):
            return item.text.replace("\n", " ")
        assert self.project is not None
        return self.project.source(item.source).path.name  # type: ignore[attr-defined]

    @staticmethod
    def _paint_thumbs(p: QPainter, images: list[QImage], rect: QRect) -> None:
        if not images:
            return
        h = rect.height() - WAVE_H
        w = max(1, round(h * images[0].width() / max(1, images[0].height())))
        n = max(1, -(-rect.width() // w))
        for i in range(n):
            img = images[min(len(images) - 1, i * len(images) // n)]
            p.drawImage(QRect(rect.left() + i * w, rect.top(), w, h), img)

    def _paint_wave(self, p: QPainter, peaks: list[float], rect: QRect, item: object) -> None:
        if not peaks or self.project is None:
            return
        dur = max(self.project.source(item.source).duration, 1e-6)  # type: ignore[attr-defined]
        lo, hi = item.in_, item.out  # type: ignore[attr-defined]
        p.setPen(QColor(255, 255, 255, 170))
        mid = rect.center().y()
        for x in range(rect.left(), rect.right() + 1, 2):
            frac = (x - rect.left()) / max(1, rect.width())
            idx = int((lo + frac * (hi - lo)) / dur * len(peaks))
            v = peaks[max(0, min(len(peaks) - 1, idx))]
            half = max(1, int(v * rect.height() / 2))
            p.drawLine(x, mid - half, x, mid + half)

    def _paint_ghost(self, p: QPainter, pal: dict[str, str]) -> None:
        d = self._drag
        if d is None or not d["moved"] or self.project is None:
            return
        span: Span = d["span"]
        rect = self._span_rect(span)
        dt = self._dt(d)
        part = d["part"]
        if part == "left":
            rect.setLeft(self.x_for(self._snap(span.start + dt, d["id"], d["alt"])))
        elif part == "right":
            rect.setRight(self.x_for(self._snap(span.end + dt, d["id"], d["alt"])))
        elif span.row_kind == "main":
            index = self._main_index(d)
            others = [m for m in self.project.main if m.id != d["id"]]
            if index < len(others):
                bx = self.x_for(self._spans[others[index].id].start)
            elif others:
                bx = self.x_for(self._spans[others[-1].id].end)
            else:
                bx = self.x_for(0)
            p.setPen(QPen(QColor(pal["accent"]), 3))
            p.drawLine(bx, rect.top(), bx, rect.bottom())
            rect.moveLeft(rect.left() + d["pos"].x() - d["origin"].x())
        else:
            start = self._snap_move(
                max(0.0, span.start + dt), span.end - span.start, d["id"], d["alt"]
            )
            rect.moveLeft(self.x_for(start))
            row = self._row_at(d["pos"].y())
            if row and row[0] == span.row_kind:
                rect.moveTop(row[2] + 2)
        p.setOpacity(0.55)
        p.setPen(QPen(QColor(pal["text"]), 1, Qt.PenStyle.DashLine))
        p.setBrush(QColor(pal["accent_soft"]))
        p.drawRoundedRect(QRectF(rect), 5, 5)
        p.setOpacity(1.0)
