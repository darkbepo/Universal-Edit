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


@pytest.fixture(autouse=True)
def _show_details():
    from umc.core.errors import ConversionError

    try:
        yield
    except ConversionError as exc:  # pragma: no cover - diagnostics only
        print("DETAILS:", exc.details, flush=True)
        raise


_last = {"msg": ""}


def _progress(fraction, message):
    # Only print when the message text changes (download progress is chatty).
    key = message.split("…")[0]
    if key == _last["msg"]:
        return
    _last["msg"] = key
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

    from umc.core.errors import ConversionError
    from umc.tools import installer, libreoffice

    try:
        exe = installer.install_libreoffice(_progress, threading.Event())
    except ConversionError as exc:
        pytest.fail(f"{exc}\nDETAILS: {exc.details}")
    print("soffice:", exe, flush=True)
    assert libreoffice.soffice_path() == exe
    [docx] = convert([media / "notes.txt"], "doc:docx", tmp_path)
    [pdf] = convert([docx], "doc:pdf", tmp_path)
    with pymupdf.open(pdf) as doc:
        assert "Plain text line 1" in doc[0].get_text()
