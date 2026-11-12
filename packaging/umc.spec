# PyInstaller spec — build with:  pyinstaller packaging/umc.spec
# Produces a one-folder app in dist/UniversalEdit/ (see CLAUDE.md → Packaging).
from pathlib import Path

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

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
                StringStruct("CompanyName", "Universal Edit (open source)"),
                StringStruct("FileDescription", "Universal Edit"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "UniversalEdit"),
                StringStruct("LegalCopyright", "Copyright (C) 2026 darkbepo. AGPL-3.0-or-later."),
                StringStruct("OriginalFilename", "UniversalEdit.exe"),
                StringStruct("ProductName", "Universal Edit"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

binaries = [(str(p), "bin") for p in BIN.glob("*") if p.is_file()] if BIN.is_dir() else []
datas = [
    (str(ROOT / "src" / "umc" / "gui" / "style.qss"), "umc/gui"),
    (str(ROOT / "src" / "umc" / "gui" / "logo.svg"), "umc/gui"),  # window / taskbar icon
    (str(ROOT / "LICENSE"), "."),
]
datas += collect_data_files("pymupdf")
datas += collect_data_files("trimesh")
datas += collect_data_files("docx")  # python-docx templates (pdf2docx)
TESSDATA = ROOT / "packaging" / "tessdata"
datas += [(str(p), "tessdata") for p in TESSDATA.glob("*.traineddata")]
datas += [(str(p), "fonts") for p in (ROOT / "packaging" / "fonts").glob("*") if p.is_file()]

a = Analysis(
    [str(ROOT / "src" / "umc" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pillow_heif", "rawpy", "cascadio", "py7zr", "pyzipper", "brotli", "vtracer", "pdf2docx", "docx", "ezdxf", "onnxruntime", "mammoth", "markdown", "markdown.extensions.tables", "markdown.extensions.fenced_code", "markdown.extensions.sane_lists", "markdown.extensions.toc", *collect_submodules("fontTools.ttLib.tables"), "umc.gui", "umc.converters", "PySide6.QtSvg", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtNetwork", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf",
              "pymupdf4llm", "pytest"],
    noarchive=False,
)
# OpenCV (pulled in by pdf2docx for table/image detection) ships its own 29 MB FFmpeg for reading
# video files. pdf2docx only uses image functions, and the app has its own FFmpeg in bin/.
a.binaries = [b for b in a.binaries if "opencv_videoio_ffmpeg" not in b[0].lower()]
# The shared FFmpeg DLLs live in bin/ next to ffmpeg.exe (which loads them from there).
# PyInstaller also copies them to the top folder as "dependencies" of the exe: a ~180 MB duplicate.
_ffmpeg_dlls = {p.name.lower() for p in BIN.glob("*.dll")} if BIN.is_dir() else set()
# Only copies *from bin/*: Qt Multimedia (viewer) brings its own LGPL FFmpeg DLLs, which can have
# the very same names (avcodec-61.dll …) and must stay.
_bin = BIN.resolve()
a.binaries = [
    b for b in a.binaries
    if not ("/" not in b[0].replace("\\", "/") and b[0].lower() in _ffmpeg_dlls
            and Path(b[1]).resolve().parent == _bin)
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalEdit",
    console=False,
    icon=str(ICON) if ICON.exists() else None,
    version=VERSION_INFO,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="UniversalEdit", upx=False)
