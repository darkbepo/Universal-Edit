"""The GUI-thread garbage collector."""

import gc
import weakref

import pytest

pytest.importorskip("pytestqt")

from umc.gui import gc_guard  # noqa: E402


class _Node:
    pass


def test_gc_runs_on_the_gui_thread_timer(qapp, qtbot):
    gc_guard.install_gui_gc(qapp, interval_ms=20)
    assert not gc.isenabled()
    a, b = _Node(), _Node()
    a.other, b.other = b, a
    ref = weakref.ref(a)
    del a, b
    assert ref() is not None  # a cycle: only the collector frees it
    saved = gc.get_threshold()
    gc.set_threshold(1, 1, 1)  # make the timer see "enough garbage" at once
    try:
        qtbot.waitUntil(lambda: ref() is None, timeout=3000)
    finally:
        gc.set_threshold(*saved)


def test_installing_twice_keeps_one_timer(qapp):
    gc_guard.install_gui_gc(qapp)
    first = gc_guard._timer
    gc_guard.install_gui_gc(qapp)
    assert gc_guard._timer is first
    assert len(qapp.findChildren(type(first))) >= 1
    assert not gc.isenabled()


# The guard is per test: on for tests that use Qt (qtbot / qapp), off again for the others so
# their reference cycles are collected as usual. The order matters: Qt test first.


def test_a_qt_test_runs_with_the_guard(qtbot):
    assert not gc.isenabled()


def test_a_plain_test_after_it_has_automatic_gc():
    assert gc.isenabled()


def test_a_qapp_test_runs_with_the_guard(qapp):
    assert not gc.isenabled()
