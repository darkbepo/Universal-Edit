"""Real downloads + installs. Only runs when UMC_LIVE_INSTALL=1 (CI on windows-latest)."""

import os
import sys
import threading

import pytest

from tests.conftest import convert

pytestmark = pytest.mark.skipif(
    os.environ.get("UMC_LIVE_INSTALL") != "1" or sys.platform != "win32",
    reason="live installer test: set UMC_LIVE_INSTALL=1 on Windows",
)


def _progress(fraction, message):
    print(f"{fraction:6.1%}  {message}", flush=True)


def test_install_pandoc_and_convert(media, tmp_path):
    from umc.core.paths import tools_dir
    from umc.tools import installer, pandoc

    exe = installer.install_pandoc(_progress, threading.Event())
    assert exe.startswith(str(tools_dir()))
    assert pandoc.pandoc_path() == exe
    [out] = convert([media / "doc.md"], "doc:epub", tmp_path)
    assert out.stat().st_size > 0


def test_install_libreoffice_and_convert(media, tmp_path):
    import pymupdf

    from umc.tools import installer, libreoffice

    exe = installer.install_libreoffice(_progress, threading.Event())
    print("soffice:", exe, flush=True)
    assert libreoffice.soffice_path() == exe
    [docx] = convert([media / "notes.txt"], "doc:docx", tmp_path)
    [pdf] = convert([docx], "doc:pdf", tmp_path)
    with pymupdf.open(pdf) as doc:
        assert "Plain text line 1" in doc[0].get_text()
