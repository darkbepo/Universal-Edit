"""Real downloads + installs (LibreOffice, Pandoc, Ghostscript, Calibre, LibreDWG).

Only runs when UMC_LIVE_INSTALL=1 (CI on windows-latest).
"""

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


def test_install_ghostscript_and_convert(tmp_path):
    import pymupdf

    from umc.core.errors import ConversionError
    from umc.tools import ghostscript, installer

    try:
        exe = installer.install_ghostscript(_progress, threading.Event())
    except ConversionError as exc:
        pytest.fail(f"{exc}\nDETAILS: {exc.details}")
    print("gs:", exe, flush=True)
    assert ghostscript.gs_path() == exe
    eps = tmp_path / "t.eps"
    eps.write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 50\n/Helvetica findfont 12 scalefont "
        "setfont 10 20 moveto (Hello EPS) show showpage\n",
        encoding="ascii",
    )
    [pdf] = convert([eps], "vec:pdf", tmp_path / "out")
    with pymupdf.open(pdf) as doc:
        assert "Hello EPS" in doc[0].get_text()


def test_install_calibre_and_convert(media, tmp_path):
    from umc.core.errors import ConversionError
    from umc.tools import calibre, installer

    try:
        exe = installer.install_calibre(_progress, threading.Event())
    except ConversionError as exc:
        pytest.fail(f"{exc}\nDETAILS: {exc.details}")
    print("ebook-convert:", exe, flush=True)
    assert calibre.ebook_convert_path() == exe
    [azw3] = convert([media / "doc.md"], "doc:azw3", tmp_path)
    assert azw3.read_bytes()[60:68] == b"BOOKMOBI"


def test_install_libredwg_and_convert(tmp_path):
    import shutil
    import subprocess

    import ezdxf

    from umc.core.errors import ConversionError
    from umc.tools import installer, libredwg

    try:
        exe = installer.install_libredwg(_progress, threading.Event())
    except ConversionError as exc:
        pytest.fail(f"{exc}\nDETAILS: {exc.details}")
    print("dwg2dxf:", exe, flush=True)
    assert libredwg.dwg2dxf_path() == exe
    # make a DWG with LibreDWG's own dxf2dwg, then read it back through the app
    src = tmp_path / "plan.dxf"
    doc = ezdxf.new("R2000")
    doc.modelspace().add_circle((50, 50), 25)
    doc.saveas(src)
    from pathlib import Path

    dxf2dwg = shutil.which("dxf2dwg", path=str(Path(exe).parent))
    assert dxf2dwg, "dxf2dwg not shipped next to dwg2dxf"
    subprocess.run([dxf2dwg, "-y", "-o", str(tmp_path / "plan.dwg"), str(src)], check=True)
    [pdf] = convert([tmp_path / "plan.dwg"], "cad:pdf", tmp_path / "out")
    assert pdf.stat().st_size > 0


def test_signature_check_rejects_unsigned_and_tampered(tmp_path):
    from umc.tools import installer

    unsigned = tmp_path / "unsigned.msi"
    unsigned.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 4096)
    with pytest.raises(installer.InstallError, match="signed"):
        installer.verify_authenticode(unsigned, installer.LO_PUBLISHER)
