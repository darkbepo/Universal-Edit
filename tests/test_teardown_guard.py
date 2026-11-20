"""The guard in conftest.pytest_runtest_teardown: a window that asks "Save changes?" when it is
closed must not hang the run (pytest-qt closes windows before the test's fixtures clean up)."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

KEEP: list = []  # pytest-qt only holds a weak reference


@pytest.fixture
def answers():
    """Checked after pytest-qt (and the guard) closed the window: the question got "Don't save"."""
    got: list[str] = []
    yield got
    assert got == ["Discard"]


def test_a_window_that_asks_when_closed_gets_dont_save(answers, qtbot) -> None:
    from PySide6.QtWidgets import QMessageBox, QWidget

    class Asks(QWidget):
        def closeEvent(self, event) -> None:  # noqa: N802
            box = QMessageBox(self)
            box.setText("Save changes?")
            box.setStandardButtons(
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            )
            answers.append(QMessageBox.StandardButton(box.exec()).name)
            event.accept()

    w = Asks()
    qtbot.addWidget(w)
    KEEP.append(w)
    w.show()  # left open with a question to ask: teardown closes it
