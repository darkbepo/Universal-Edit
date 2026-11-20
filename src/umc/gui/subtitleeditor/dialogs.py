"""The Shift times… and Sync… windows. They only collect numbers; the page does the edit."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from umc.audioedit.ops import parse_time
from umc.subedit.formats import Cue, clock


def parse_ms(text: str) -> int | None:
    seconds = parse_time(text)
    return None if seconds is None else int(round(seconds * 1000))


class ShiftDialog(QDialog):
    """Move all subtitles, or the chosen ones, by a number of seconds."""

    def __init__(self, parent, chosen: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shift times")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Move the subtitles by (seconds; negative = earlier):"))
        self.amount = QDoubleSpinBox()
        self.amount.setAccessibleName("Seconds")
        self.amount.setRange(-36000, 36000)
        self.amount.setDecimals(3)
        self.amount.setSingleStep(0.1)
        lay.addWidget(self.amount)
        self.all = QRadioButton("All subtitles")
        self.only = QRadioButton(
            f"Only the {chosen} chosen" if chosen > 1 else "Only the chosen one"
        )
        group = QButtonGroup(self)
        for b in (self.all, self.only):
            group.addButton(b)
            lay.addWidget(b)
        self.all.setChecked(True)
        self.only.setEnabled(chosen > 0)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> tuple[int, bool]:
        return int(round(self.amount.value() * 1000)), self.only.isChecked()


class SyncDialog(QDialog):
    """Two subtitles and the times they should start at; everything is stretched between."""

    def __init__(self, parent, cues: tuple[Cue, ...], first: int, last: int, play_ms) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sync with two subtitles")
        self.cues = cues
        lay = QVBoxLayout(self)
        note = QLabel(
            "Fixes subtitles that drift out of step. Pick a subtitle near the start and one near "
            "the end, and type when each should really start; every subtitle is moved and "
            "stretched to match."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        grid = QGridLayout()
        lay.addLayout(grid)
        self.rows: list[tuple[QSpinBox, QLineEdit, QLabel]] = []
        for r, (label, index) in enumerate((("First", first), ("Last", last))):
            spin = QSpinBox()
            spin.setAccessibleName(f"{label} subtitle number")
            spin.setRange(1, max(1, len(cues)))
            spin.setValue(index + 1)
            words = QLabel(objectName="Muted")
            box = QLineEdit()
            box.setAccessibleName(f"{label} subtitle's new start")
            grid.addWidget(QLabel(f"{label} subtitle"), r * 2, 0)
            grid.addWidget(spin, r * 2, 1)
            grid.addWidget(QLabel("should start at"), r * 2, 2)
            grid.addWidget(box, r * 2, 3)
            if play_ms is not None:
                use = QPushButton("Video position", objectName="Small")
                use.setToolTip("Use where the video is now")
                use.clicked.connect(lambda _c=False, b=box: b.setText(clock(play_ms())))
                grid.addWidget(use, r * 2, 4)
            grid.addWidget(words, r * 2 + 1, 1, 1, 4)
            spin.valueChanged.connect(lambda _v, s=spin, b=box, w=words: self._fill(s, b, w))
            self._fill(spin, box, words)
            self.rows.append((spin, box, words))
        self.problem = QLabel(objectName="Error")
        lay.addWidget(self.problem)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _fill(self, spin: QSpinBox, box: QLineEdit, words: QLabel) -> None:
        c = self.cues[spin.value() - 1]
        box.setText(clock(c.start))
        words.setText(f"now {clock(c.start)}: {c.text.replace(chr(10), ' ')[:60]}")

    def values(self) -> tuple[int, int, int, int] | None:
        (sa, ba, _), (sb, bb, _) = self.rows
        ta, tb = parse_ms(ba.text()), parse_ms(bb.text())
        if ta is None or tb is None:
            return None
        return sa.value() - 1, ta, sb.value() - 1, tb

    def _ok(self) -> None:
        if self.values() is None:
            self.problem.setText("Type the times like 1:05.250 or 65.25.")
            return
        self.accept()
