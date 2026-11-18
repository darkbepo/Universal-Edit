"""The viewer window: the file large, the folder's other files beside it, Convert / Edit below.

Opened by ``UniversalEdit --view FILE`` (what "Open with" runs). The side list groups the
folder's files by kind (folds remembered) with its sub-folders on top; Back / Forward / Up and
an address line browse to other folders, and the list follows files appearing or going away.
← / → (or Page Up / Down) go through the files of the open groups; Home / End jump to the
first / last; Alt+← / Alt+→ / Alt+↑ (and the mouse's side buttons) are Back / Forward / Up;
F11 is full screen. A song or video left while playing goes on in the mini player under the
list."""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QImage, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from umc.core.detect import DetectedFile, detect
from umc.gui import branding, icons, theme
from umc.gui.viewer.media import MediaView, MiniPlayer, Playback, clock
from umc.gui.viewer.tasks import TaskRunner
from umc.gui.viewer.views import (
    ImageCanvas,
    InfoView,
    PagesView,
    TextView,
    is_animated,
    load_image,
)
from umc.viewer import browse, preview, waveform
from umc.viewer import files as vf

log = logging.getLogger(__name__)

THUMB = 44
SIDE_WIDTH = 290
KIND_NAMES = {
    vf.IMAGE: "Picture",
    vf.VIDEO: "Video",
    vf.AUDIO: "Sound",
    vf.PAGES: "Document",
    vf.CONVERT: "Document",
    vf.TEXT: "Text",
    vf.MODEL: "3D model",
    vf.OTHER: "File",
}
PATH_ROLE = Qt.ItemDataRole.UserRole  # file / folder items: the path (str)
KIND_ROLE = Qt.ItemDataRole.UserRole + 1  # "file", "folder" or "group"
GROUP_ROLE = Qt.ItemDataRole.UserRole + 2  # group items: the category id
TITLE_ROLE = Qt.ItemDataRole.UserRole + 3  # group items: "Pictures  ·  12"
FOLDED_KEY = "viewer/folded"
VOLUME_KEY = "viewer/volume"
REFRESH_MS = 300  # wait after a change in the folder before listing it again


def fmt_size(n: int) -> str:
    from umc.gui.widgets.file_queue import fmt_size as _fmt

    return _fmt(n)


def _thumbnail(path: Path) -> QImage | None:
    """Worker thread: a small picture for the side list (formats Qt reads directly)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    size = reader.size()
    if size.isValid() and min(size.width(), size.height()) > THUMB * 3:
        scale = THUMB * 3 / min(size.width(), size.height())
        reader.setScaledSize(QSize(round(size.width() * scale), round(size.height() * scale)))
    image = reader.read()
    return None if image.isNull() else image


def _warm_up() -> None:
    import umc.tools.pdf  # noqa: F401


def _mark_fold(group: QTreeWidgetItem) -> None:
    """▾ before an open group's title, ▸ before a folded one."""
    title = group.data(0, TITLE_ROLE) or ""
    group.setText(0, f"{'▾' if group.isExpanded() else '▸'}  {title}")


def _settings() -> QSettings:
    return QSettings()


class ViewerWindow(QMainWindow):
    convert_requested = Signal(object)  # Path
    edit_requested = Signal(object)  # Path
    properties_requested = Signal(object)  # Path

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(branding.APP_NAME)
        self.setMinimumSize(720, 460)
        self.resize(1240, 800)
        self.tasks = TaskRunner(self)
        self.location: browse.Location | None = None
        self.listing: browse.Listing | None = None
        self.history = browse.History()
        self.items: list[Path] = []  # the files, in the side list's order
        self.current: DetectedFile | None = None
        self.shown: Path | None = None  # the file shown (also when it can't be identified)
        self.kind = vf.OTHER
        self.details = ""  # "1920 × 1080", "12 pages", …
        self._model_view = None  # made on first 3D file (needs OpenGL)
        self._preview_dir: Path | None = None
        self._preview_cancel: threading.Event | None = None
        self._wave_cancel: threading.Event | None = None
        self._pdf_for_pages: Path | None = None
        self._thumbs_requested: set[Path] = set()
        self._thumb_icons: dict[Path, QIcon] = {}
        self._file_items: dict[Path, QTreeWidgetItem] = {}
        self._folded: set[str] = set(_settings().value(FOLDED_KEY, [], list) or [])
        self._volume = int(_settings().value(VOLUME_KEY, 80) or 80)

        root = QWidget(objectName="Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_nav_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.stack = QStackedWidget(objectName="ViewerStage")
        self.canvas = ImageCanvas()
        self.media = MediaView()
        self.media.set_volume(self._volume)
        self.media.toggled.connect(self._toggle_playback)
        self.media.volume_changed.connect(self._set_volume)
        self.media.duration_changed.connect(self._on_duration)
        self.pages = PagesView()
        self.pages.request_page.connect(self._render_page)
        self.text = TextView()
        self.info = InfoView()
        for w in (self.canvas, self.media, self.pages, self.text, self.info):
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)

        side = QFrame(objectName="ViewerSide")
        side.setFixedWidth(SIDE_WIDTH)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.list = QTreeWidget(objectName="ViewerList")
        self.list.setHeaderHidden(True)
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setIndentation(0)
        self.list.setRootIsDecorated(False)
        self.list.setExpandsOnDoubleClick(False)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.currentItemChanged.connect(self._on_item)
        self.list.itemClicked.connect(self._on_click)
        self.list.itemExpanded.connect(lambda it: self._on_fold(it, False))
        self.list.itemCollapsed.connect(lambda it: self._on_fold(it, True))
        self.list.verticalScrollBar().valueChanged.connect(lambda _v: self._thumb_timer.start())
        sl.addWidget(self.list, 1)
        self.mini = MiniPlayer()
        self.mini.reopen.connect(self._reopen_from_mini)
        sl.addWidget(self.mini)
        body.addWidget(side)
        outer.addLayout(body, 1)

        bar = QFrame(objectName="BottomBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(22, 10, 22, 10)
        bl.setSpacing(12)
        self.name_label = QLabel(objectName="CardTitle")
        self.detail_label = QLabel(objectName="Muted")
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.properties_button = QPushButton("Properties", objectName="Secondary")
        self.properties_button.setToolTip("See this file's details")
        self.convert_button = QPushButton("Convert", objectName="Secondary")
        self.convert_button.setToolTip("Open this file in the converter")
        self.edit_button = QPushButton("Edit", objectName="Primary")
        for b in (self.properties_button, self.convert_button, self.edit_button):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumWidth(110)
        self.convert_button.clicked.connect(self._convert)
        self.properties_button.clicked.connect(self._properties)
        self.edit_button.clicked.connect(self._edit)
        bl.addWidget(self.name_label)
        bl.addWidget(self.detail_label, 1)
        bl.addWidget(self.properties_button)
        bl.addWidget(self.convert_button)
        bl.addWidget(self.edit_button)
        outer.addWidget(bar)
        self.setCentralWidget(root)

        # PyMuPDF takes about a second to import: do it early, so the first PDF opens quickly
        QTimer.singleShot(
            800, self, lambda: self.tasks.submit("warm", _warm_up, lambda _r: None, lane="pdf")
        )
        self._thumb_timer = QTimer(self, singleShot=True, interval=50)
        self._thumb_timer.timeout.connect(self._request_thumbs)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._refresh_timer.start())
        self._refresh_timer = QTimer(self, singleShot=True, interval=REFRESH_MS)
        self._refresh_timer.timeout.connect(self.refresh)
        keys = (
            (("Right", "PgDown"), lambda: self.step(1)),
            (("Left", "PgUp"), lambda: self.step(-1)),
            (("Home",), self.first),
            (("End",), self.last),
            (("Alt+Left", "Back"), self.go_back),
            (("Alt+Right", "Forward"), self.go_forward),
            (("Alt+Up", "Backspace"), self.go_up),
            (("Ctrl+L", "F4"), self._focus_address),
            (("F11",), self.toggle_full_screen),
            (("Esc",), self._leave_full_screen),
            (("Space",), self._space),
        )
        for seqs, fn in keys:
            for seq in seqs:
                QShortcut(QKeySequence(seq), self, fn).setContext(Qt.ShortcutContext.WindowShortcut)
        QApplication.instance().installEventFilter(self)  # the mouse's back / forward buttons
        theme.notifier().changed.connect(self._retint)
        self._retint()

    def _build_nav_bar(self) -> QFrame:
        bar = QFrame(objectName="ViewerNav")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        self.back_button = QPushButton(objectName="NavButton")
        self.back_button.setToolTip("Back (Alt+←)")
        self.back_button.clicked.connect(self.go_back)
        self.forward_button = QPushButton(objectName="NavButton")
        self.forward_button.setToolTip("Forward (Alt+→)")
        self.forward_button.clicked.connect(self.go_forward)
        self.up_button = QPushButton(objectName="NavButton")
        self.up_button.setToolTip("Up to the folder above (Alt+↑)")
        self.up_button.clicked.connect(self.go_up)
        for b in (self.back_button, self.forward_button, self.up_button):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIconSize(QSize(18, 18))
            b.setEnabled(False)
            lay.addWidget(b)
        self.address = QLineEdit(objectName="ViewerAddress")
        self.address.setPlaceholderText("Type a folder and press Enter")
        self.address.setClearButtonEnabled(True)
        # only a click (or Ctrl+L) puts the cursor there: Space and the arrows stay the viewer's
        self.address.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.address.returnPressed.connect(self._address_entered)
        lay.addWidget(self.address, 1)
        self.address_note = QLabel(objectName="ViewerAddressNote")
        self.address_note.hide()
        lay.addWidget(self.address_note)
        self._note_timer = QTimer(self, singleShot=True, interval=3500)
        self._note_timer.timeout.connect(self.address_note.hide)
        return bar

    def _retint(self) -> None:
        pal = theme.palette()
        for b, name in (
            (self.back_button, "arrow-left"),
            (self.forward_button, "arrow-right"),
            (self.up_button, "arrow-up"),
        ):
            b.setIcon(icons.icon(name, pal["text"], 18))

    # ------------------------------------------------------------ opening
    def open_path(self, path: str | Path) -> None:
        """Show ``path`` (a file, or a folder's first file) with its folder in the side list."""
        path = Path(path).resolve()
        if path.is_dir():
            self.navigate(path)
        else:
            self.navigate(path.parent, select=path)

    def navigate(
        self, location: browse.Location, select: Path | None = None, record: bool = True
    ) -> None:
        """List ``location`` in the side list and show ``select`` (else its first file)."""
        if record:
            self.history.visit(location)
        self.location = location
        self._watch(location)
        self._fill(browse.list_location(location))
        self._update_nav()
        if select is not None and select not in self._file_items:
            self.items.append(select)  # e.g. a hidden file opened on purpose: still shown
            self._add_file_item(self._group_item("other", "Other", "file"), select)
        target = select if select is not None else self._first_allowed()
        if target is not None:
            self._select(target)
        else:
            self._show_nothing()
        self._thumb_timer.start()

    def _watch(self, location: browse.Location) -> None:
        old = self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        if isinstance(location, Path):
            self.watcher.addPath(str(location))

    def refresh(self) -> None:
        """List the folder again (something appeared, went away or was renamed)."""
        if self.location is None:
            return
        listing = browse.list_location(self.location)
        if (
            self.listing is not None
            and listing.folders == self.listing.folders
            and listing.groups == self.listing.groups
        ):
            return
        if isinstance(self.location, Path) and not self.location.is_dir():
            # the folder itself went away: show the nearest one still there
            parent = self.location
            while not parent.is_dir() and parent.parent != parent:
                parent = parent.parent
            self.navigate(parent, record=True)
            return
        old_items = self.items
        current = self.shown
        self._fill(listing)
        if current is not None and current in self._file_items:
            self._select(current, show=False)
        elif self.items:
            # the file shown went away: the one now at its place (or the last)
            index = old_items.index(current) if current in old_items else 0
            survivors = [p for p in old_items[index:] if p in self._file_items]
            self._select(survivors[0] if survivors else self.items[-1])
        else:
            self._show_nothing()
        self._update_details()
        self._thumb_timer.start()

    def _fill(self, listing: browse.Listing) -> None:
        self.listing = listing
        self.items = listing.files()
        self.tasks.cancel_group("thumb")
        self._thumbs_requested.clear()
        self._thumb_icons = {p: ic for p, ic in self._thumb_icons.items() if p in set(self.items)}
        self._file_items = {}
        self.list.blockSignals(True)
        self.list.clear()
        pal = theme.palette()
        for cat in listing.shown_categories():
            members = listing.folders if cat is browse.FOLDERS else listing.groups[cat.id]
            count = len(members)
            group = self._group_item(cat.id, f"{cat.title}  ·  {count}", cat.icon)
            if cat is browse.FOLDERS:
                for folder in listing.folders:
                    name = str(folder) if listing.location is browse.COMPUTER else folder.name
                    item = QTreeWidgetItem(group, [name])
                    item.setData(0, PATH_ROLE, str(folder))
                    item.setData(0, KIND_ROLE, "folder")
                    item.setToolTip(0, f"Open {name}")
                    item.setIcon(0, icons.icon("folder", pal["muted"], 28))
                    item.setSizeHint(0, QSize(0, 34))
            else:
                for path in listing.groups[cat.id]:
                    self._add_file_item(group, path)
        self.list.blockSignals(False)
        self.address.setText(browse.display(listing.location))
        title = browse.display(listing.location)
        self.address.setToolTip(title)

    def _group_item(self, cat_id: str, text: str, icon: str) -> QTreeWidgetItem:
        for i in range(self.list.topLevelItemCount()):
            it = self.list.topLevelItem(i)
            if it.data(0, GROUP_ROLE) == cat_id:
                return it
        pal = theme.palette()
        group = QTreeWidgetItem(self.list, [text])
        group.setData(0, TITLE_ROLE, text)
        group.setData(0, KIND_ROLE, "group")
        group.setData(0, GROUP_ROLE, cat_id)
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = QFont(group.font(0))
        font.setBold(True)
        group.setFont(0, font)
        group.setIcon(0, icons.icon(icon, pal["accent"], 18))
        group.setToolTip(0, "Click to fold or unfold")
        group.setSizeHint(0, QSize(0, 32))
        group.setExpanded(cat_id not in self._folded)
        _mark_fold(group)
        return group

    def _add_file_item(self, group: QTreeWidgetItem, path: Path) -> None:
        pal = theme.palette()
        item = QTreeWidgetItem(group, [path.name])
        item.setData(0, PATH_ROLE, str(path))
        item.setData(0, KIND_ROLE, "file")
        item.setToolTip(0, path.name)
        icon = self._thumb_icons.get(path)
        if icon is None:
            icon = icons.icon(vf.ICON_OF_KIND[vf.kind_of_path(path)], pal["muted"], THUMB)
        item.setIcon(0, icon)
        item.setSizeHint(0, QSize(0, THUMB + 10))
        self._file_items[path] = item
        if path not in self.items:
            self.items.append(path)

    def _show_nothing(self) -> None:
        self._stop_current()
        self.current = None
        self.shown = None
        self.kind = vf.OTHER
        self.details = ""
        where = browse.display(self.location) if self.location is not None else ""
        name = Path(where).name or where
        self.setWindowTitle(f"{name} — {branding.APP_NAME}")
        self.name_label.setText(name)
        self._update_buttons()
        self._update_details()
        if self.listing is not None and self.listing.folders:
            text = "Pick a folder on the right, or type one above."
        else:
            text = "There's nothing here the viewer can show."
        self._show_info("folder", text, title=name)

    # ------------------------------------------------------------ the side list
    def _on_item(self, item: QTreeWidgetItem | None, _previous) -> None:
        if item is not None and item.data(0, KIND_ROLE) == "file":
            path = Path(item.data(0, PATH_ROLE))
            if self.shown != path:
                self.show_file(path)

    def _on_click(self, item: QTreeWidgetItem, _column: int) -> None:
        kind = item.data(0, KIND_ROLE)
        if kind == "group":
            item.setExpanded(not item.isExpanded())
        elif kind == "folder":
            self.navigate(Path(item.data(0, PATH_ROLE)))

    def _on_fold(self, item: QTreeWidgetItem, folded: bool) -> None:
        cat = item.data(0, GROUP_ROLE)
        if not cat:
            return
        _mark_fold(item)
        (self._folded.add if folded else self._folded.discard)(cat)
        _settings().setValue(FOLDED_KEY, sorted(self._folded))
        self._thumb_timer.start()

    def _folded_files(self) -> set[Path]:
        out = set()
        for i in range(self.list.topLevelItemCount()):
            group = self.list.topLevelItem(i)
            if not group.isExpanded():
                for j in range(group.childCount()):
                    child = group.child(j)
                    if child.data(0, KIND_ROLE) == "file":
                        out.add(Path(child.data(0, PATH_ROLE)))
        return out

    def _first_allowed(self) -> Path | None:
        skip = self._folded_files()
        shown = [p for p in self.items if p not in skip]
        return shown[0] if shown else (self.items[0] if self.items else None)

    def _select(self, path: Path, show: bool = True) -> None:
        item = self._file_items.get(path)
        if item is None:
            return
        group = item.parent()
        if group is not None and not group.isExpanded():
            group.setExpanded(True)  # an explicit pick unfolds its group
        self.list.blockSignals(True)
        self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.list.scrollToItem(item)
        if show and self.shown != path:
            self.show_file(path)

    def current_index(self) -> int:
        if self.shown is None or self.shown not in self.items:
            return -1
        return self.items.index(self.shown)

    def go_to(self, row: int) -> None:
        if self.items:
            self._select(self.items[max(0, min(row, len(self.items) - 1))])

    def step(self, delta: int) -> None:
        if not self.items:
            return
        index = browse.step_index(self.items, self.shown, delta, self._folded_files())
        if index >= 0:
            self._select(self.items[index])

    def first(self) -> None:
        self.step(-len(self.items))

    def last(self) -> None:
        self.step(len(self.items))

    # ------------------------------------------------------------ folders
    def go_back(self) -> None:
        location = self.history.back()
        if location is not None:
            self._go(location)

    def go_forward(self) -> None:
        location = self.history.forward()
        if location is not None:
            self._go(location)

    def go_up(self) -> None:
        if self.location is None:
            return
        parent = browse.parent_of(self.location)
        if parent is not None:
            came_from = self.location
            self.navigate(parent)
            if isinstance(came_from, Path):
                item = self._folder_item(came_from)
                if item is not None:
                    self.list.scrollToItem(item)

    def _go(self, location: browse.Location) -> None:
        if isinstance(location, Path) and not location.is_dir():
            self._note("That folder isn't there any more.")
            self._update_nav()
            return
        self.navigate(location, record=False)

    def _folder_item(self, folder: Path) -> QTreeWidgetItem | None:
        for i in range(self.list.topLevelItemCount()):
            group = self.list.topLevelItem(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if (
                    child.data(0, KIND_ROLE) == "folder"
                    and Path(child.data(0, PATH_ROLE)) == folder
                ):
                    return child
        return None

    def _update_nav(self) -> None:
        self.back_button.setEnabled(self.history.can_back())
        self.forward_button.setEnabled(self.history.can_forward())
        self.up_button.setEnabled(
            self.location is not None and browse.parent_of(self.location) is not None
        )

    def _address_entered(self) -> None:
        current = self.location if self.location is not None else Path.home()
        found = browse.resolve_address(self.address.text(), current)
        if found is None:
            self._note("There's no folder or file there.")
            self.address.setText(browse.display(current))
            return
        folder, file = found
        self.navigate(folder, select=file)
        self.stack.setFocus()

    def _focus_address(self) -> None:
        self.address.setFocus()
        self.address.selectAll()

    def _note(self, text: str) -> None:
        self.address_note.setText(text)
        self.address_note.show()
        self._note_timer.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(obj, QWidget)
            and obj.window() is self
        ):
            if event.button() == Qt.MouseButton.BackButton:
                self.go_back()
                return True
            if event.button() == Qt.MouseButton.ForwardButton:
                self.go_forward()
                return True
        return False

    # ------------------------------------------------------------ showing one file
    def show_file(self, path: Path) -> None:
        self._stop_current()
        f = detect(path)
        self.current = f
        self.shown = path
        self.kind = vf.view_kind(f) if f is not None else vf.OTHER
        self.details = ""
        self.setWindowTitle(f"{path.name} — {branding.APP_NAME}")
        self.name_label.setText(path.name)
        self._update_buttons()
        self._update_details()
        if f is None or self.kind == vf.OTHER:
            self._show_info("file", "There's no preview for this kind of file.")
            return
        getattr(self, f"_show_{self.kind}")(f)

    def _stop_current(self) -> None:
        for group in ("main", "page", "wave"):
            self.tasks.cancel_group(group)
        for event in (self._preview_cancel, self._wave_cancel):
            if event is not None:
                event.set()
        self._preview_cancel = self._wave_cancel = None
        playback = self.media.detach()
        if playback is not None:
            if playback.playing() and not playback.error:
                self.mini.attach(playback)  # goes on under the list
            else:
                playback.close()
        self.canvas.clear()

    def _show_info(self, icon: str, text: str, title: str | None = None) -> None:
        name = title if title is not None else (self.current.name if self.current else "")
        self.info.show_info(icon, name, text)
        self.stack.setCurrentWidget(self.info)

    def _failed(self, default: str):
        def show(message: str) -> None:
            self._show_info("alert", message or default)

        return show

    # ------------------------------------------------------------ songs and videos
    def _play(self, f: DetectedFile, sound_only: bool) -> None:
        mini = self.mini.playback
        if mini is not None and mini.path == f.path:
            playback = self.mini.detach()  # back from the mini player, still going
        else:
            playback = Playback(f.path, sound_only, self._volume, self)
        self.media.attach(playback)  # picture output first, then the file opens
        if (mini is None or mini.path != f.path) and not self.mini.playing():
            playback.player.play()  # (with the mini player going, this one waits)
        self.stack.setCurrentWidget(self.media)
        if sound_only and playback.levels is None:
            self._load_waveform(playback)

    def _load_waveform(self, playback: Playback) -> None:
        cancel = threading.Event()
        self._wave_cancel = cancel

        def done(levels: list[float]) -> None:
            self.media.set_levels(playback, levels)

        self.tasks.submit(
            "wave",
            lambda: waveform.levels(playback.path, cancel=cancel),
            done,
            lambda message: log.info("viewer: no waveform for %s: %s", playback.path, message),
        )

    def _toggle_playback(self) -> None:
        playback = self.media.playback
        if playback is None:
            return
        if not playback.playing() and self.mini.playback is not None:
            self.mini.stop()  # one thing plays at a time
        playback.toggle()

    def _set_volume(self, percent: int) -> None:
        self._volume = percent
        _settings().setValue(VOLUME_KEY, percent)
        for pb in (self.media.playback, self.mini.playback):
            if pb is not None:
                pb.set_volume(percent)

    def _reopen_from_mini(self, path: Path) -> None:
        if path in self._file_items:
            self._select(path)
        else:
            self.open_path(path)

    # ------------------------------------------------------------ each kind
    def _show_image(self, f: DetectedFile) -> None:
        self._show_info("photo", "Opening…")

        def load() -> tuple[QImage | None, bool]:
            # imageCount() reads a whole GIF: decide here, not on the UI thread
            if is_animated(f.path):
                return None, True
            return load_image(f.path), False

        def done(result: tuple[QImage | None, bool]) -> None:
            image, animated = result
            if animated:
                self.canvas.set_movie(f.path)
                image = self.canvas.image
            else:
                self.canvas.set_image(image)
            self._image_ready(image)

        self.tasks.submit("main", load, done, self._failed("This picture can't be opened."))

    def _image_ready(self, image: QImage | None) -> None:
        self.stack.setCurrentWidget(self.canvas)
        self.canvas.setFocus()
        if image is not None and not image.isNull():
            self.details = f"{image.width()} × {image.height()}"
            self._update_details()

    def _show_video(self, f: DetectedFile) -> None:
        self._play(f, sound_only=False)

    def _show_audio(self, f: DetectedFile) -> None:
        self._play(f, sound_only=True)

    def _show_pages(self, f: DetectedFile) -> None:
        self._open_pages(f.path)

    def _open_pages(self, pdf_path: Path) -> None:
        self._show_info("pdf", "Opening…")
        self.tasks.cancel_group("page")  # renders of the document shown before
        self._pdf_for_pages = pdf_path

        def done(sizes: list[tuple[float, float]]) -> None:
            if not sizes:
                self._show_info("alert", "This document has no pages.")
                return
            self.stack.setCurrentWidget(self.pages)
            self.pages.set_pages(sizes)
            self.details = "1 page" if len(sizes) == 1 else f"{len(sizes)} pages"
            self._update_details()

        self.tasks.submit(
            "main",
            lambda: preview.page_sizes(pdf_path),
            done,
            self._failed("This document can't be opened."),
            lane="pdf",
        )

    def _render_page(self, index: int, width: int) -> None:
        pdf_path = self._pdf_for_pages
        if pdf_path is None:
            return

        def render() -> QImage:
            img = preview.render_page(pdf_path, index, width).convert("RGB")
            q = QImage(
                img.tobytes(), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888
            )
            return q.copy()

        self.tasks.submit(
            "page",
            render,
            lambda image: self.pages.set_page(index, image),
            exclusive=False,
            lane="pdf",
        )

    def _show_convert(self, f: DetectedFile) -> None:
        """Word, Excel, PowerPoint, … : made into a PDF first (the converter's own route)."""
        self._show_info("document", "Preparing a preview…")
        if self._preview_dir is None:
            self._preview_dir = Path(tempfile.mkdtemp(prefix="umc-view-"))
        out = Path(tempfile.mkdtemp(prefix="p", dir=self._preview_dir))
        cancel = threading.Event()
        self._preview_cancel = cancel

        def failed(message: str) -> None:
            self._show_info(
                "alert",
                message or "There's no preview for this file. Convert can still change it.",
            )

        self.tasks.submit(
            "main",
            lambda: preview.preview_pdf(f, out, cancel),
            self._open_pages_later,
            failed,
            lane="pdf",
        )

    def _open_pages_later(self, pdf_path: Path) -> None:
        self._open_pages(pdf_path)

    def _show_text(self, f: DetectedFile) -> None:
        def done(result: tuple[str, bool]) -> None:
            text, cut = result
            self.text.setPlainText(text)
            self.stack.setCurrentWidget(self.text)
            lines = text.count("\n") + 1
            self.details = f"{lines:,} lines" + (" (first part shown)" if cut else "")
            self._update_details()

        self._show_info("document", "Opening…")
        self.tasks.submit(
            "main", lambda: preview.read_text(f.path), done, self._failed("Couldn't read the text.")
        )

    def _show_model(self, f: DetectedFile) -> None:
        from umc.gui.editor3d.gl_view import ModelGLView, gl_problem

        problem = gl_problem()
        if problem:
            self._show_info("cube", "The 3D view needs OpenGL, which isn't working here.")
            return
        if self._model_view is None:
            self._model_view = ModelGLView()
            self.stack.addWidget(self._model_view)
        self._show_info("cube", "Opening…")

        def load():
            from umc.editor3d.mesh import load_view_model

            model = load_view_model(f.path, f.ext)
            model.triangle_buffer()
            model.edge_buffer()
            return model

        def done(model) -> None:
            self._model_view.set_model(model)
            self.stack.setCurrentWidget(self._model_view)
            w, d, h = model.size
            self.details = f"{w:.1f} × {d:.1f} × {h:.1f} mm"
            self._update_details()

        self.tasks.submit("main", load, done, self._failed("This 3D model can't be opened."))

    # ------------------------------------------------------------ bottom bar
    def _update_details(self) -> None:
        if self.shown is None:
            self.detail_label.setText(
                f"{len(self.items)} files" if self.items else "No files to show here"
            )
            return
        parts = [KIND_NAMES.get(self.kind, "File")]
        if self.details:
            parts.append(self.details)
        with contextlib.suppress(OSError):
            parts.append(fmt_size(self.shown.stat().st_size))
        index = self.current_index()
        if index >= 0:
            parts.append(f"{index + 1} of {len(self.items)}")
        self.detail_label.setText(" · ".join(parts))

    def _on_duration(self, ms: int) -> None:
        if self.kind in (vf.VIDEO, vf.AUDIO) and ms > 0:
            self.details = clock(ms)
            self._update_details()

    def _update_buttons(self) -> None:
        self.convert_button.setEnabled(self.current is not None)
        self.properties_button.setEnabled(self.current is not None)
        editor = vf.editor_for(self.kind, self.current.path if self.current else None)
        can_edit = self.current is not None and vf.can_edit(self.kind, self.current.path)
        self.edit_button.setEnabled(can_edit)
        if can_edit:
            self.edit_button.setToolTip(f"Open this file in the {editor.title.lower()}")
        elif editor is not None and editor.available:
            self.edit_button.setToolTip("This kind of file can't be edited here")
        elif editor is not None:
            self.edit_button.setToolTip(f"The {editor.title.lower()} is coming soon")
        else:
            self.edit_button.setToolTip("There's no editor for this kind of file yet")

    def _convert(self) -> None:
        if self.current is not None:
            self.convert_requested.emit(self.current.path)

    def _properties(self) -> None:
        if self.current is not None:
            self.properties_requested.emit(self.current.path)

    def _edit(self) -> None:
        if self.current is not None and self.edit_button.isEnabled():
            self.edit_requested.emit(self.current.path)

    def _space(self) -> None:
        if self.address.hasFocus():
            return
        if self.stack.currentWidget() is self.media:
            self._toggle_playback()
        else:
            self.step(1)

    def toggle_full_screen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _leave_full_screen(self) -> None:
        if self.address.hasFocus():
            self.address.setText(browse.display(self.location) if self.location else "")
            self.stack.setFocus()
        elif self.isFullScreen():
            self.showNormal()

    # ------------------------------------------------------------ side list thumbnails
    def _request_thumbs(self) -> None:
        """Pictures in view (and a screen ahead) get a real thumbnail; others keep the icon."""
        view = self.list.viewport().rect()
        ahead = view.adjusted(0, -view.height(), 0, view.height())
        for path, item in self._file_items.items():
            if path in self._thumbs_requested or vf.kind_of_path(path) != vf.IMAGE:
                continue
            if path in self._thumb_icons:
                continue
            rect = self.list.visualItemRect(item)
            if rect.isEmpty() or not rect.intersects(ahead):
                continue
            self._thumbs_requested.add(path)

            def done(image: QImage | None, path: Path = path) -> None:
                if image is None:
                    return
                icon = QIcon(QPixmap.fromImage(image))
                self._thumb_icons[path] = icon
                item = self._file_items.get(path)
                if item is not None:
                    item.setIcon(0, icon)

            self.tasks.submit("thumb", lambda p=path: _thumbnail(p), done, exclusive=False)

    # ------------------------------------------------------------ closing
    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_current()
        self.mini.stop()
        old = self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.tasks.shutdown()  # doesn't wait: a slow preview conversion mustn't freeze closing
        folder, self._preview_dir = self._preview_dir, None
        if folder is not None:
            tasks = self.tasks

            def remove_when_idle() -> None:  # after any conversion still writing into it
                tasks.wait(120_000)
                shutil.rmtree(folder, ignore_errors=True)

            threading.Thread(target=remove_when_idle, name="umc-view-cleanup", daemon=True).start()
        super().closeEvent(event)
