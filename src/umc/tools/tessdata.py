"""Tesseract language data for OCR (PyMuPDF has Tesseract built in; it only needs these files).

English ships with the app (`packaging/tessdata`, fetched by `scripts/fetch_tessdata.py`).
Other languages are downloaded once into the private tools folder. Files come from the fixed
`4.1.0` tag of tesseract-ocr/tessdata_fast (Apache-2.0) and are checked against the SHA-256
digests below, so a changed or damaged download is never used.
"""

from __future__ import annotations

import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from umc.core.errors import ConversionError
from umc.core.paths import tools_dir

URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{code}.traineddata"

LANGUAGES: list[tuple[str, str]] = [
    ("eng", "English"),
    ("dan", "Danish"),
    ("deu", "German"),
    ("fra", "French"),
    ("spa", "Spanish"),
    ("ita", "Italian"),
    ("por", "Portuguese"),
    ("nld", "Dutch"),
    ("swe", "Swedish"),
    ("nor", "Norwegian"),
    ("fin", "Finnish"),
    ("isl", "Icelandic"),
    ("pol", "Polish"),
    ("ces", "Czech"),
    ("hun", "Hungarian"),
    ("ron", "Romanian"),
    ("tur", "Turkish"),
    ("ell", "Greek"),
    ("rus", "Russian"),
    ("ukr", "Ukrainian"),
    ("ara", "Arabic"),
    ("heb", "Hebrew"),
    ("hin", "Hindi"),
    ("chi_sim", "Chinese (Simplified)"),
    ("chi_tra", "Chinese (Traditional)"),
    ("jpn", "Japanese"),
    ("kor", "Korean"),
    ("vie", "Vietnamese"),
    ("tha", "Thai"),
    ("ind", "Indonesian"),
]
NAMES = dict(LANGUAGES)

# code → (sha256, size in bytes) for tessdata_fast 4.1.0
DIGESTS: dict[str, tuple[str, int]] = {
    "ara": ("e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729", 1432056),
    "ces": ("934bcaf97ef3348413263331131c9fa7f55f30db333c711929c124fb635f7e1b", 3795684),
    "chi_sim": ("a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730", 2469156),
    "chi_tra": ("529c5b5797d64b126065cd55f2bb4c7fd7b15790798091b1ff259941a829330b", 2366642),
    "dan": ("acb1fd074487a31d1294fcdfd7d7c673467ffd8aeacb2ccd61ebcbf04eb4e2fa", 2580059),
    "deu": ("19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d", 1525436),
    "ell": ("4fba8a0b461038d51f1c20d043d4f2ac38c4e778f1b90830847f7bd8fa3ba726", 1419514),
    "eng": ("7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2", 4113088),
    "fin": ("61a04cd62b507c3d9ae0e1cda399e6715ebf49dea9df47897c8acdcd3bd3e13c", 7865732),
    "fra": ("ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca", 1130365),
    "heb": ("11f9e43ab227f786352a50f75c94c2e9906f1baba86d93276da19da7ce0904db", 961404),
    "hin": ("4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825", 1122751),
    "hun": ("35067e7cfe102dcdc953f9a758fdfaa6296b17a1ee6d874ee780fa306430b9fb", 5296273),
    "ind": ("69786901da87ab8766c1ea7fbb10b28f2110c14da3f6c8f2735df131fba95d88", 1122661),
    "isl": ("5ec828c363f3f0062c3caf08216677cf5f3c0c981b05fab445123a16b98f10a9", 2278973),
    "ita": ("b8f89e1e785118dac4d51ae042c029a64edb5c3ee42ef73027a6d412748d8827", 2701314),
    "jpn": ("1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d", 2471260),
    "kor": ("6b85e11d9bbf07863b97b3523b1b112844c43e713df8b66418a081fd1060b3b2", 1677415),
    "nld": ("ced0e5e046a84c908a6aa7accbef9a232c4a5d9a8276691b81c6ee64d02963f6", 6050296),
    "nor": ("0451eb4f8049ae78196806bf878a389a2f40f1386fe038568cf4441226ba6ef2", 3610079),
    "pol": ("c4476cdbc0e33d898d32345122b7be1cbf85ace15f920f06c7714756e1ef79b2", 4765518),
    "por": ("c4932b937207a9514b7514d518b931a99938c02a28a5a5a553f8599ed58b7deb", 1982756),
    "ron": ("9adfde6b51ba4b97efd10ea37c3070fd3fc2bad7815e81f5c3c198cd96216cc9", 2376323),
    "rus": ("e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225", 3861738),
    "spa": ("6f2e04d02774a18f01bed44b1111f2cd7f3ba7ac9dc4373cd3f898a40ea6b464", 2294433),
    "swe": ("f7304988d41f833efebcc2d529df54b1903ecebbc3da1faabd19a0fddd4fe586", 4167034),
    "tha": ("294227cc2d1292b0acb28d61d4115c88252b96d466ca90b417cf4cf0c67bf07c", 1072600),
    "tur": ("7393381111e1152420fc4092cb44eef4237580d21b92bf30d7d221aad192c6b7", 4550554),
    "ukr": ("d59e53e2bded32f4445f124b4b00240fcac7e8044c003ab822ccb94f0b3db59b", 3825102),
    "vie": ("79df64caf7bcfb2a27df5042ecb6121e196eada34da774956995747636d5bfa1", 531275),
}


def bundled_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "tessdata"
    return Path(__file__).resolve().parents[3] / "packaging" / "tessdata"


def data_dir() -> Path:
    path = tools_dir() / "tessdata"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _valid(path: Path, code: str) -> bool:
    import hashlib

    sha, size = DIGESTS[code]
    if not path.is_file() or path.stat().st_size != size:
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == sha


def ensure(
    codes: list[str],
    progress: Callable[[float, str], None],
    cancel_event: threading.Event,
) -> str:
    """Make sure every language in ``codes`` is in :func:`data_dir`; return that folder."""
    from umc.tools.installer import InstallError, download

    folder = data_dir()
    for code in codes:
        if code not in DIGESTS:
            raise ConversionError(f"OCR language “{code}” isn't supported.")
        dest = folder / f"{code}.traineddata"
        if dest.is_file() and dest.stat().st_size == DIGESTS[code][1]:
            continue
        bundled = bundled_dir() / dest.name
        if _valid(bundled, code):
            shutil.copyfile(bundled, dest)
            continue
        part = dest.with_suffix(".part")
        label = f"{NAMES[code]} language data"
        try:
            download(
                URL.format(code=code),
                part,
                progress=lambda f, m: progress(f, f"{m} (first time only)"),
                cancel_event=cancel_event,
                label=label,
                expected_sha256=DIGESTS[code][0],
            )
        except InstallError as exc:
            part.unlink(missing_ok=True)
            damaged = "checksum" in str(exc)
            raise ConversionError(
                str(exc)
                if damaged
                else f"Couldn't download the {label} needed for OCR. "
                "Check your internet connection and try again.",
                details=exc.details,
            ) from exc
        part.replace(dest)
    return str(folder)
