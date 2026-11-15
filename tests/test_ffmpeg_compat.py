"""FFmpeg version differences (Linux distributions ship older FFmpeg than the bundled one)."""

from __future__ import annotations

import subprocess

import pytest

from umc.tools import ffmpeg

GRAPH_ARGS = ["-i", "a.mp4", "-/filter_complex", "graph_1.txt", "-map", "[v]"]


@pytest.fixture(autouse=True)
def _fresh_version_cache():
    ffmpeg.major_version.cache_clear()
    yield
    ffmpeg.major_version.cache_clear()


@pytest.mark.parametrize(
    ("first_line", "major"),
    [
        ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 the FFmpeg developers", 6),
        ("ffmpeg version 5.1.6-0+deb12u1 Copyright (c) 2000-2024", 5),
        ("ffmpeg version n7.1-152-gd72536008a-20250105 Copyright (c) 2000-2024", 7),
        ("ffmpeg version 7.1.1-1+b1 Copyright (c) 2000-2025", 7),
        ("ffmpeg version N-117854-g1bd1a5cf5b-20241130 Copyright (c) 2000-2024", None),
    ],
)
def test_major_version_is_read_from_the_banner(monkeypatch, first_line, major):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=first_line + "\nbuilt with gcc\n")

    monkeypatch.setattr(ffmpeg, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    assert ffmpeg.major_version() == major


@pytest.mark.parametrize("major", [5, 6])
def test_old_ffmpeg_reads_the_graph_file_with_filter_complex_script(monkeypatch, major):
    monkeypatch.setattr(ffmpeg, "major_version", lambda: major)
    out = ffmpeg._compat(GRAPH_ARGS)
    assert out == ["-i", "a.mp4", "-filter_complex_script", "graph_1.txt", "-map", "[v]"]


@pytest.mark.parametrize("major", [7, 8, None])
def test_new_or_unknown_ffmpeg_keeps_the_args(monkeypatch, major):
    monkeypatch.setattr(ffmpeg, "major_version", lambda: major)
    assert ffmpeg._compat(GRAPH_ARGS) == GRAPH_ARGS


def test_args_without_a_graph_file_never_ask_for_the_version(monkeypatch):
    def boom():
        raise AssertionError("version asked")

    monkeypatch.setattr(ffmpeg, "major_version", boom)
    assert ffmpeg._compat(["-i", "a.mp4", "out.mkv"]) == ["-i", "a.mp4", "out.mkv"]
