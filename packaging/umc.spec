# PyInstaller spec — build with:  pyinstaller packaging/umc.spec
# Produces a one-folder app in dist/UniversalEdit/ (see CLAUDE.md → Packaging).
from pathlib import Path

import os
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

# Editor add-ons: their modules and their own third-party packages are left out of the app;
# addon_hashes.json (scripts/build_addons.py, run before this) holds their fingerprints.
# Linux builds (the .deb) carry every editor instead: a marker file tells the app so.
sys.path.insert(0, str(ROOT / "src"))
from umc.core.addons import ADDONS  # noqa: E402

BUNDLE_EDITORS = sys.platform.startswith("linux") or os.environ.get("UMC_BUNDLE_EDITORS") == "1"
ADDON_EXCLUDES = [] if BUNDLE_EDITORS else [
    m for a in ADDONS.values() for m in (*a.modules, *a.packages)
]
# every own module except the add-ons' (code only the editors imported must stay: bgremove, …)
APP_MODULES = collect_submodules(
    "umc", filter=lambda n: not any(n == m or n.startswith(m + ".") for m in ADDON_EXCLUDES)
)
ADDON_HASHES = ROOT / "src" / "umc" / "addon_hashes.json"
BUNDLED_MARKER = ROOT / "build" / "editors_bundled"
if BUNDLE_EDITORS:
    BUNDLED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    BUNDLED_MARKER.write_text("every editor is part of this build\n", encoding="utf-8")
elif not ADDON_HASHES.is_file():
    raise SystemExit("Run scripts/build_addons.py first (it writes src/umc/addon_hashes.json).")
EDITOR_IMPORTS = (
    [*collect_submodules("pygments"), "onnxruntime", *(a.entry for a in ADDONS.values())]
    if BUNDLE_EDITORS else []
)

binaries = [(str(p), "bin") for p in BIN.glob("*") if p.is_file()] if BIN.is_dir() else []
datas = [
    (str(ROOT / "src" / "umc" / "gui" / "style.qss"), "umc/gui"),
    (str(ROOT / "src" / "umc" / "gui" / "logo.svg"), "umc/gui"),  # window / taskbar icon
    # the editor add-ons' SHA-256 fingerprints, or the marker that every editor is inside
    (str(BUNDLED_MARKER), "umc") if BUNDLE_EDITORS else (str(ADDON_HASHES), "umc"),
    (str(ROOT / "LICENSE"), "."),
]
datas += collect_data_files("pymupdf")
datas += collect_data_files("trimesh")
datas += collect_data_files("docx")  # python-docx templates (pdf2docx)
TESSDATA = ROOT / "packaging" / "tessdata"
datas += [(str(p), "tessdata") for p in TESSDATA.glob("*.traineddata")]
datas += [(str(p), "fonts") for p in (ROOT / "packaging" / "fonts").glob("*") if p.is_file()]
# File-type icons for "Open with" (scripts/make_file_icons.py; checked in)
datas += [(str(p), "fileicons") for p in (ROOT / "packaging" / "fileicons").glob("*.ico")]

a = Analysis(
    [str(ROOT / "src" / "umc" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[*APP_MODULES, *EDITOR_IMPORTS, "pillow_heif", "rawpy", "cascadio", "py7zr", "pyzipper", "brotli", "vtracer", "pdf2docx", "docx", "ezdxf", "mammoth", *collect_submodules("mutagen"), *collect_submodules("markdown_it"), "mdurl", *collect_submodules("fontTools.ttLib.tables"), "umc.gui", "umc.converters", "PySide6.QtSvg", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtNetwork", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    # editors that ship as add-ons (core/addons.py, scripts/build_addons.py) stay out of the app
    excludes=[*ADDON_EXCLUDES, "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
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
