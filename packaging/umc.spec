# PyInstaller spec — build with:  pyinstaller packaging/umc.spec
# Produces a one-folder app in dist/UniversalMediaConverter/ (see CLAUDE.md → Packaging).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
BIN = ROOT / "packaging" / "bin"
ICON = ROOT / "packaging" / "icon.ico"

binaries = [(str(p), "bin") for p in BIN.glob("*") if p.is_file()] if BIN.is_dir() else []
datas = [(str(ROOT / "src" / "umc" / "gui" / "style.qss"), "umc/gui"), (str(ROOT / "LICENSE"), ".")]
datas += collect_data_files("pymupdf")

a = Analysis(
    [str(ROOT / "src" / "umc" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pillow_heif", "umc.gui", "umc.converters"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf",
              "pymupdf4llm", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalMediaConverter",
    console=False,
    icon=str(ICON) if ICON.exists() else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="UniversalMediaConverter", upx=False)
