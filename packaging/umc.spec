# PyInstaller spec — build with:  pyinstaller packaging/umc.spec
# Produces a one-folder app in dist/UniversalMediaConverter/ (see CLAUDE.md → Packaging).
from pathlib import Path

import sys

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
BIN = ROOT / "packaging" / "bin"
ICON = ROOT / "packaging" / "icon.ico"

# Windows "Details" tab metadata. Executables without any look more suspicious to antivirus
# heuristics (see CLAUDE.md → Packaging → antivirus).
_ver = {}
exec((ROOT / "src" / "umc" / "__init__.py").read_text(encoding="utf-8"), _ver)
VERSION = _ver["__version__"]
_nums = tuple(int(p) for p in (VERSION.split(".") + ["0", "0", "0"])[:4])
VERSION_INFO = None
if sys.platform == "win32":  # the version-resource helpers need pefile (Windows builds only)
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    VERSION_INFO = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_nums, prodvers=_nums, mask=0x3F, flags=0x0, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "Universal Media Converter (open source)"),
                StringStruct("FileDescription", "Universal Media Converter"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "UniversalMediaConverter"),
                StringStruct("LegalCopyright", "Copyright (C) 2026 darkbepo. AGPL-3.0-or-later."),
                StringStruct("OriginalFilename", "UniversalMediaConverter.exe"),
                StringStruct("ProductName", "Universal Media Converter"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

binaries = [(str(p), "bin") for p in BIN.glob("*") if p.is_file()] if BIN.is_dir() else []
datas = [(str(ROOT / "src" / "umc" / "gui" / "style.qss"), "umc/gui"), (str(ROOT / "LICENSE"), ".")]
datas += collect_data_files("pymupdf")
datas += collect_data_files("trimesh")
TESSDATA = ROOT / "packaging" / "tessdata"
datas += [(str(p), "tessdata") for p in TESSDATA.glob("*.traineddata")]

a = Analysis(
    [str(ROOT / "src" / "umc" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pillow_heif", "rawpy", "cascadio", "py7zr", "pyzipper", "umc.gui", "umc.converters"],
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
    version=VERSION_INFO,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="UniversalMediaConverter", upx=False)
