"""Offline tests for the one-click installer (network is served from a local HTTP server)."""

import hashlib
import http.server
import threading
import zipfile
from functools import partial

import pytest

from umc.core.errors import ConversionCancelled
from umc.tools import installer


@pytest.fixture
def tools(tmp_path, monkeypatch):
    d = tmp_path / "tools"
    monkeypatch.setenv("UMC_TOOLS_DIR", str(d))
    return d


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_pick_libreoffice_version_takes_newest():
    html = '<a href="24.8.7/">24.8.7/</a> <a href="25.2.10/">25.2.10/</a> <a href="25.2.9/">x</a>'
    assert installer.pick_libreoffice_version(html) == "25.2.10"
    assert installer.libreoffice_msi_url("25.2.10").endswith(
        "/25.2.10/win/x86_64/LibreOffice_25.2.10_Win_x86-64.msi"
    )


def test_pick_pandoc_asset_uses_windows_zip_and_digest():
    release = {
        "tag_name": "3.8.2",
        "assets": [
            {"name": "pandoc-3.8.2-windows-x86_64.msi", "browser_download_url": "msi"},
            {
                "name": "pandoc-3.8.2-windows-x86_64.zip",
                "browser_download_url": "zip",
                "digest": "sha256:abc123",
            },
            {"name": "pandoc-3.8.2-linux-amd64.tar.gz", "browser_download_url": "tgz"},
        ],
    }
    assert installer.pick_pandoc_asset(release) == ("3.8.2", "zip", "abc123")


def test_download_verifies_checksum_and_reports_progress(server, tmp_path):
    root, base = server
    data = b"x" * (3 << 20)
    (root / "file.bin").write_bytes(data)
    seen = []
    out = installer.download(
        f"{base}/file.bin",
        tmp_path / "out.bin",
        label="test",
        expected_sha256=hashlib.sha256(data).hexdigest(),
        progress=lambda f, m: seen.append(f),
        cancel_event=threading.Event(),
    )
    assert out.read_bytes() == data
    assert seen and seen[-1] == pytest.approx(1.0)


def test_download_rejects_bad_checksum(server, tmp_path):
    root, base = server
    (root / "file.bin").write_bytes(b"hello")
    with pytest.raises(installer.InstallError, match="damaged"):
        installer.download(
            f"{base}/file.bin",
            tmp_path / "out.bin",
            label="test",
            expected_sha256="0" * 64,
            progress=lambda f, m: None,
            cancel_event=threading.Event(),
        )
    assert not (tmp_path / "out.bin").exists()


def test_download_can_be_cancelled(server, tmp_path):
    root, base = server
    (root / "file.bin").write_bytes(b"x" * (2 << 20))
    ev = threading.Event()
    ev.set()
    with pytest.raises(ConversionCancelled):
        installer.download(
            f"{base}/file.bin",
            tmp_path / "out.bin",
            label="t",
            progress=lambda f, m: None,
            cancel_event=ev,
        )


def test_unreachable_server_gives_friendly_error(tmp_path):
    with pytest.raises(installer.InstallError, match="internet connection"):
        installer.download(
            "http://127.0.0.1:9/nothing",
            tmp_path / "x",
            label="t",
            progress=lambda f, m: None,
            cancel_event=threading.Event(),
        )


def test_install_pandoc_end_to_end_offline(server, tools, monkeypatch):
    root, base = server
    archive = root / "pandoc.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pandoc-9.9/pandoc.exe", b"fake")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(installer, "latest_pandoc", lambda: ("9.9", f"{base}/pandoc.zip", sha))
    exe = installer.install_pandoc(lambda f, m: None, threading.Event())
    assert exe.endswith("pandoc.exe") and str(tools) in exe


def test_zip_with_unsafe_paths_is_rejected(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.txt", b"x")
    with zipfile.ZipFile(archive) as zf, pytest.raises(installer.InstallError):
        installer._safe_extract(zf, tmp_path / "out")


def test_private_copy_is_preferred(tools, monkeypatch):
    from umc.tools import libreoffice, pandoc, process

    exe_name = "soffice.exe" if process.IS_WINDOWS else "soffice"
    fake = tools / "libreoffice" / "LibreOffice" / "program" / exe_name
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    assert libreoffice.soffice_path() == str(fake)
    assert pandoc.pandoc_path() != str(fake)


def test_not_enough_disk_space(tools, monkeypatch):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(1, 1, 10_000))
    with pytest.raises(installer.InstallError, match="disk space"):
        installer.install_pandoc(lambda f, m: None, threading.Event())


def test_dialog_install_button_installs_and_accepts(qtbot, monkeypatch):
    from umc.gui import tool_dialog

    state = {"installed": False}

    def fake_install(progress, cancel_event):
        progress(0.5, "Downloading Pandoc…")
        state["installed"] = True
        return "pandoc.exe"

    monkeypatch.setattr(installer, "supported", lambda tool: True)
    monkeypatch.setitem(installer.INSTALLERS, "Pandoc", fake_install)
    monkeypatch.setitem(tool_dialog._CHECKS, "Pandoc", lambda: state["installed"])
    dlg = tool_dialog.ToolMissingDialog("Pandoc", "https://pandoc.org", "Pandoc is needed.")
    qtbot.addWidget(dlg)
    assert dlg.install_button.isVisible() or not dlg.isVisible()
    with qtbot.waitSignal(dlg.accepted, timeout=10000):
        dlg.install_button.click()


def test_dialog_shows_error_when_install_fails(qtbot, monkeypatch):
    from umc.gui import tool_dialog

    def failing(progress, cancel_event):
        raise installer.InstallError("Couldn't reach the download server.")

    monkeypatch.setattr(installer, "supported", lambda tool: True)
    monkeypatch.setitem(installer.INSTALLERS, "Pandoc", failing)
    monkeypatch.setitem(tool_dialog._CHECKS, "Pandoc", lambda: False)
    dlg = tool_dialog.ToolMissingDialog("Pandoc", "https://pandoc.org", "Pandoc is needed.")
    qtbot.addWidget(dlg)
    dlg.install_button.click()
    qtbot.waitUntil(
        lambda: dlg._worker is None and "download server" in dlg.status.text(), timeout=10000
    )
    assert dlg.install_button.isEnabled()
    assert dlg.result() == 0  # still open, not accepted
