"""Round trips through real files for the final review fixes (no Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.doceditor_samples import PNG_1PX
from umc.doceditor.html_subset import normalise_html


@pytest.mark.parametrize(
    "src", [r"\host\share\a.png", "//host/share/a.png", r"C:\x.png", "/etc/x.png", "C:/x.png"]
)
def test_picture_paths_never_touch_other_places(tmp_path, monkeypatch, src):
    def guard(name):
        real = getattr(Path, name)

        def check(self, *a, **k):
            text = str(self)
            if "host" in text or "x.png" in text:
                raise AssertionError(f"{name} called with {text}")
            return real(self, *a, **k)

        return check

    for name in ("resolve", "is_file", "stat", "read_bytes"):
        monkeypatch.setattr(Path, name, guard(name))
    html, imgs = normalise_html(f'<p>a<img src="{src}"></p>', base_dir=tmp_path)
    assert imgs == {} and html == "<p>a</p>"


def test_relative_picture_still_read(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "p.png").write_bytes(PNG_1PX)
    _, imgs = normalise_html('<p><img src="sub/p.png"></p>', base_dir=tmp_path)
    assert list(imgs) == ["img/1.png"]
