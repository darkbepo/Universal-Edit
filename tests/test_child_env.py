"""The built Linux app gives programs it starts their normal library path back."""

from __future__ import annotations

import sys

import pytest

from umc.app import restore_child_library_path


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
