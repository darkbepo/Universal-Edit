"""The queue: one painted row per input file (thumbnail, name, details or progress, state)."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from umc.core.detect import DetectedFile, MediaFamily
from umc.gui import icons, theme

ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_FAMILY = Qt.ItemDataRole.UserRole + 1
ROLE_DETAIL = Qt.ItemDataRole.UserRole + 2
ROLE_PROGRESS = Qt.ItemDataRole.UserRole + 3  # float 0..1, or None when idle
ROLE_STATE = Qt.ItemDataRole.UserRole + 4  # idle | waiting | running | done | error

ROW_H = 56
THUMB = 36
THUMB_MAX_BYTES = 80 * 1024 * 1024

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="umc-thumb")


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024**3:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024**3:.2f} GB"


def _load_thumb(path: str) -> tuple[QImage | None, tuple[int, int] | None]:
    """Runs on a worker thread: a small preview + the pixel size, for formats Qt reads."""
    reader = QImageReader(path)
    if not reader.canRead():
        return None, None
    size = reader.size()
    dims = (size.width(), size.height()) if size.isValid() else None
    try:
        if Path(path).stat().st_size > THUMB_MAX_BYTES:
            return None, dims
    except OSError:
        return None, dims
    if size.isValid() and size.width() > 0 and size.height() > 0:
        side = THUMB * 3  # crisp on HiDPI screens
        scale = side / min(size.width(), size.height())
        if scale < 1:
            reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
    image = reader.read()
    return (None if image.isNull() else image), dims


def _file_detail(f: DetectedFile, dims: tuple[int, int] | None) -> str:
    try:
        parts = [fmt_size(f.path.stat().st_size)]
    except OSError:
        parts = []
    if dims:
        parts.append(f"{dims[0]}×{dims[1]}")
    return " · ".join(parts)


class _RowDelegate(QStyledItemDelegate):
    remove_clicked = Signal(int)

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_H)

    @staticmethod
    def close_rect(row: QRect) -> QRect:
        return QRect(row.right() - 30, row.center().y() - 11, 22, 22)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        pal = theme.palette()
        r = option.rect.adjusted(2, 2, -2, -2)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        state = index.data(ROLE_STATE) or "idle"
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected or hovered:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal["accent_soft"] if selected else pal["surface_alt"]))
            p.drawRoundedRect(QRectF(r), 10, 10)

        # thumbnail / family icon
        t = QRect(r.left() + 8, r.center().y() - THUMB // 2, THUMB, THUMB)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(t), 8, 8)
        thumb = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(thumb, QPixmap) and not thumb.isNull():
            p.setClipPath(clip)
            src = thumb.rect()
            side = min(src.width(), src.height())
            dx, dy = (src.width() - side) // 2, (src.height() - side) // 2
            crop = QRect(src.x() + dx, src.y() + dy, side, side)
            p.drawPixmap(t, thumb, crop)
            p.setClipping(False)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal["surface_alt"] if not (selected or hovered) else pal["surface"]))
            p.drawPath(clip)
            fam = index.data(ROLE_FAMILY)
            name = icons.FAMILY_ICON.get(fam, "file") if isinstance(fam, MediaFamily) else "file"
            glyph = icons.pixmap(name, 20, pal["muted"])
            p.drawPixmap(t.center().x() - 10, t.center().y() - 10, glyph)

        # right-hand state
        right = r.right() - 8
        cr = self.close_rect(option.rect)
        progress = index.data(ROLE_PROGRESS)
        small = QFont(option.font)
        small.setPixelSize(12)
        if state == "done":
            p.drawPixmap(cr.x() + 1, cr.y() + 1, icons.pixmap("check", 20, pal["success"]))
        elif state == "error":
            p.drawPixmap(cr.x() + 1, cr.y() + 1, icons.pixmap("alert", 20, pal["danger"]))
        elif state == "running" and progress is not None:
            p.setFont(small)
            p.setPen(QColor(pal["muted"]))
            p.drawText(
                QRect(right - 48, r.top(), 48, r.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                f"{progress * 100:.0f} %",
            )
        elif state == "waiting":
            p.setFont(small)
            p.setPen(QColor(pal["muted"]))
            p.drawText(
                QRect(right - 60, r.top(), 60, r.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "Waiting",
            )
        else:
            color = pal["text"] if hovered else pal["muted"]
            p.drawPixmap(cr.x() + 3, cr.y() + 3, icons.pixmap("close", 16, color))

        # name + details / progress
        x = t.right() + 12
        w = cr.left() - 12 - x - (30 if state in ("running", "waiting") else 0)
        name_font = QFont(option.font)
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.Weight.Medium)
        p.setFont(name_font)
        p.setPen(QColor(pal["text"]))
        name = p.fontMetrics().elidedText(
            index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideMiddle, w
        )
        p.drawText(QRect(x, r.top() + 9, w, 18), Qt.AlignmentFlag.AlignVCenter, name)
        if state == "idle" or progress is None:
            p.setFont(small)
            p.setPen(QColor(pal["muted"]))
            p.drawText(
                QRect(x, r.top() + 28, w, 16),
                Qt.AlignmentFlag.AlignVCenter,
                p.fontMetrics().elidedText(
                    index.data(ROLE_DETAIL) or "", Qt.TextElideMode.ElideRight, w
                ),
            )
        else:
            track = QRectF(x, r.top() + 34, w, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal["surface_alt"] if not hovered else pal["border"]))
            p.drawRoundedRect(track, 2, 2)
            fill = {"done": pal["success"], "error": pal["danger"]}.get(state, pal["accent"])
            frac = 1.0 if state == "done" else max(0.0, min(1.0, float(progress)))
            if frac > 0:
                p.setBrush(QColor(fill))
                p.drawRoundedRect(QRectF(x, track.y(), max(4.0, w * frac), 4), 2, 2)
        p.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and (index.data(ROLE_STATE) or "idle") == "idle"
            and self.close_rect(option.rect).contains(event.position().toPoint())
        ):
            self.remove_clicked.emit(index.row())
            return True
        return super().editorEvent(event, model, option, index)


class FileQueue(QListWidget):
    """Drag to reorder; × or Delete removes; ``set_progress`` drives the per-file bars."""

    remove_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FileQueue")
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setUniformItemSizes(True)
        self._delegate = _RowDelegate(self)
        self._delegate.remove_clicked.connect(self.remove_requested)
        self.setItemDelegate(self._delegate)
        self._thumbs: dict[str, tuple[QPixmap | None, tuple[int, int] | None]] = {}
        self._pending: dict[str, Future] = {}
        self._poll = QTimer(self, interval=80)
        self._poll.timeout.connect(self._collect)

    # ------------------------------------------------------------------ rows
    def set_files(self, files: list[DetectedFile]) -> None:
        self.clear()
        for f in files:
            key = str(f.path)
            item = QListWidgetItem(f.name)
            item.setData(ROLE_PATH, key)
            item.setData(ROLE_FAMILY, f.family)
            item.setData(ROLE_STATE, "idle")
            item.setData(ROLE_PROGRESS, None)
            item.setToolTip(key)
            thumb, dims = self._thumbs.get(key, (None, None))
            item.setData(ROLE_DETAIL, _file_detail(f, dims))
            if thumb is not None:
                item.setData(Qt.ItemDataRole.DecorationRole, thumb)
            self.addItem(item)
            previewable = f.family in (MediaFamily.IMAGE, MediaFamily.VECTOR)
            if previewable and key not in self._thumbs and key not in self._pending:
                self._pending[key] = _pool.submit(_load_thumb, key)
        if self._pending:
            self._poll.start()

    def paths(self) -> list[str]:
        return [self.item(i).data(ROLE_PATH) for i in range(self.count())]

    def _collect(self) -> None:
        for key, fut in list(self._pending.items()):
            if not fut.done():
                continue
            del self._pending[key]
            try:
                image, dims = fut.result()
            except Exception:  # noqa: BLE001 — a preview is never worth an error
                image, dims = None, None
            pm = QPixmap.fromImage(image) if image is not None else None
            self._thumbs[key] = (pm, dims)
            for i in range(self.count()):
                item = self.item(i)
                if item.data(ROLE_PATH) == key:
                    if pm is not None:
                        item.setData(Qt.ItemDataRole.DecorationRole, pm)
                    if dims:
                        parts = [item.data(ROLE_DETAIL) or "", f"{dims[0]}×{dims[1]}"]
                        item.setData(ROLE_DETAIL, " · ".join(filter(None, parts)))
        if not self._pending:
            self._poll.stop()

    def thumbnails_pending(self) -> bool:
        return bool(self._pending)

    # ------------------------------------------------------------------ progress
    def states(self) -> list[str]:
        return [self.item(i).data(ROLE_STATE) for i in range(self.count())]

    def set_progress(self, fractions: list[float]) -> None:
        for i, frac in enumerate(fractions[: self.count()]):
            item = self.item(i)
            state = "done" if frac >= 1.0 else "running"
            if state == "running" and frac <= 0 and i > 0 and fractions[i - 1] < 1.0:
                state, frac = "waiting", None  # still waiting its turn
            item.setData(ROLE_STATE, state)
            item.setData(ROLE_PROGRESS, frac)

    def mark_done(self) -> None:
        self.set_progress([1.0] * self.count())

    def mark_failed(self, message: str) -> None:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(ROLE_STATE) != "done":
                for j in range(i + 1, self.count()):
                    if self.item(j).data(ROLE_STATE) == "waiting":
                        self.item(j).setData(ROLE_STATE, "idle")
                item.setData(ROLE_STATE, "error")
                if item.data(ROLE_PROGRESS) is None:
                    item.setData(ROLE_PROGRESS, 0.0)
                item.setToolTip(message)
                return

    def reset_states(self) -> None:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(ROLE_STATE) != "idle":
                item.setData(ROLE_STATE, "idle")
                item.setData(ROLE_PROGRESS, None)
                item.setToolTip(item.data(ROLE_PATH))

    def cancel_running(self) -> None:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(ROLE_STATE) in ("running", "waiting"):
                item.setData(ROLE_STATE, "idle")
                item.setData(ROLE_PROGRESS, None)
