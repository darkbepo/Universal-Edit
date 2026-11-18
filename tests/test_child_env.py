"""The built Linux app gives programs it starts their normal library path back."""

from __future__ import annotations

import sys

import pytest

from umc.app import forget_bundled_qt_paths, restore_child_library_path


@pytest.fixture
def frozen_linux(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")


def test_original_path_comes_back(frozen_linux):
    env = {"LD_LIBRARY_PATH": "/opt/universal-edit/_internal", "LD_LIBRARY_PATH_ORIG": "/usr/x"}
    restore_child_library_path(env)
    assert env == {"LD_LIBRARY_PATH": "/usr/x"}


def test_no_original_means_none(frozen_linux):
    env = {"LD_LIBRARY_PATH": "/opt/universal-edit/_internal", "PATH": "/usr/bin"}
    restore_child_library_path(env)
    assert env == {"PATH": "/usr/bin"}


def test_left_alone_from_source_and_on_windows(monkeypatch):
    env = {"LD_LIBRARY_PATH": "/somewhere"}
    restore_child_library_path(env)  # not frozen (from source)
    assert env == {"LD_LIBRARY_PATH": "/somewhere"}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    restore_child_library_path(env)
    assert env == {"LD_LIBRARY_PATH": "/somewhere"}


def test_bundled_qt_paths_are_dropped_after_qt_read_them(frozen_linux):
    asked = []

    class FakeApp:
        def libraryPaths(self):  # noqa: N802 - Qt's name
            asked.append(True)
            return ["/opt/universal-edit/_internal/PySide6/Qt/plugins"]

    env = {"QT_PLUGIN_PATH": "/opt/x/plugins", "QML2_IMPORT_PATH": "/opt/x/qml", "HOME": "/h"}
    forget_bundled_qt_paths(FakeApp(), env)
    assert asked and env == {"HOME": "/h"}
