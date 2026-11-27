"""Touch screens and narrow windows: finger-sized controls, pinch, folding side panels."""

import pytest

pytest.importorskip("pytestqt")


def test_env_switches_touch(monkeypatch, qapp):
    from umc.gui import touch

    monkeypatch.setenv("UMC_TOUCH", "1")
    assert touch.enabled()
    assert "min-height" in touch.stylesheet()
    monkeypatch.setenv("UMC_TOUCH", "0")
    assert not touch.enabled()
    assert touch.stylesheet() == ""


def test_touch_stylesheet_parses(monkeypatch, qapp):
    """A broken rule makes Qt drop everything after it (see test_stylesheet_parses)."""
    from PySide6.QtWidgets import QPushButton

    from umc.gui import theme, touch

    monkeypatch.setenv("UMC_TOUCH", "1")
    sheet = theme.stylesheet() + touch.stylesheet()
    assert sheet.count("{") == sheet.count("}")
    b = QPushButton("x")
    b.setStyleSheet(touch.stylesheet())
    b.ensurePolished()
    assert b.minimumSizeHint().height() >= 34


def test_pinch_zooms_and_pans(qtbot):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QWidget

    from umc.gui.touch import PinchZoom

    w = QWidget()
    qtbot.addWidget(w)
    w.resize(400, 300)
    log = []
    pinch = PinchZoom(
        w,
        lambda f, at: log.append(("zoom", round(f, 3))),
        lambda d: log.append(("pan", d.x(), d.y())),
        lambda: log.append(("start",)),
    )
    g = w.mapToGlobal(QPointF(100, 100))
    pinch.handle(Qt.GestureState.GestureStarted, 1.0, g)
    pinch.handle(Qt.GestureState.GestureUpdated, 1.5, g + QPointF(10, -5))
    pinch.handle(Qt.GestureState.GestureFinished, 1.0, g)
    assert log == [("start",), ("zoom", 1.5), ("pan", 10.0, -5.0)]


def test_side_panels_fold_when_narrow(qtbot):
    from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

    from umc.gui.touch import Foldable

    page = QWidget()
    qtbot.addWidget(page)
    lay = QHBoxLayout(page)
    side, button = QWidget(), QPushButton("Panels")
    lay.addWidget(button)
    lay.addWidget(side)
    folds = []
    fold = Foldable(page, [side], button, below=1000, on_fold=folds.append)
    page.resize(1200, 600)
    page.show()
    qtbot.waitUntil(lambda: fold.narrow is False)
    assert side.isVisible() and not button.isVisible()
    page.resize(800, 600)
    qtbot.waitUntil(lambda: fold.narrow is True)
    assert not side.isVisible() and button.isVisible()
    button.click()
    assert side.isVisible()
    button.click()
    assert not side.isVisible()
    page.resize(1200, 600)
    qtbot.waitUntil(lambda: fold.narrow is False)
    assert side.isVisible() and not button.isVisible()
    assert folds == [False, True, False]


def test_fold_width_follows_touch_mode(monkeypatch, qtbot):
    from PySide6.QtWidgets import QPushButton, QWidget

    from umc.gui import touch

    page = QWidget()
    qtbot.addWidget(page)
    fold = touch.Foldable(page, [], QPushButton())
    monkeypatch.setenv("UMC_TOUCH", "1")
    assert fold.threshold() == touch.FOLD_BELOW
    monkeypatch.setenv("UMC_TOUCH", "0")
    assert fold.threshold() == touch.FOLD_BELOW_MOUSE  # a 1366 px laptop keeps its panels
