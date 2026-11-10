"""Background removal: a segmentation model (IS-Net "general use", Apache-2.0, from the DIS
project) run with ONNX Runtime (MIT).

The model (~179 MB) isn't bundled: it is downloaded once into the private tools folder from the
fixed ``v0.0.0`` release of rembg (where the ONNX export is published) and checked against the
SHA-256 digest below, so a changed or damaged download is never used.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

from umc.core.errors import ConversionError
from umc.core.paths import tools_dir

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/isnet-general-use.onnx"
MODEL_SHA256 = "60920e99c45464f2ba57bee2ad08c919a52bbf852739e96947fbb4358c0d964a"
MODEL_SIZE = 178_648_008
MODEL_MB = round(MODEL_SIZE / 1e6)
INPUT = 1024  # the model's square input size
MEAN = (0.485, 0.456, 0.406)

_session = None
_session_lock = threading.Lock()


def model_path() -> Path:
    folder = tools_dir() / "models"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "isnet-general-use.onnx"


def model_ready() -> bool:
    p = model_path()
    return p.is_file() and p.stat().st_size == MODEL_SIZE


def ensure_model(progress: Callable[[float, str], None], cancel_event: threading.Event) -> Path:
    """Download the model if it isn't there yet (verified); return its path."""
    from umc.tools.installer import InstallError, download

    dest = model_path()
    if model_ready():
        return dest
    part = dest.with_suffix(".part")
    try:
        download(
            MODEL_URL,
            part,
            progress=progress,
            cancel_event=cancel_event,
            label="background-removal model",
            expected_sha256=MODEL_SHA256,
        )
    except InstallError as exc:
        part.unlink(missing_ok=True)
        raise ConversionError(
            str(exc)
            if "checksum" in str(exc)
            else "Couldn't download the background-removal model. "
            "Check your internet connection and try again.",
            details=exc.details,
        ) from exc
    part.replace(dest)
    return dest


def _get_session(path: Path):
    global _session
    with _session_lock:
        if _session is None:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            _session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        return _session


def subject_mask(image: Image.Image, model: Path | None = None) -> Image.Image:
    """An "L" mask the size of ``image``: white where the main subject is, black behind it."""
    path = model or model_path()
    if not model_ready() and model is None:
        raise ConversionError("The background-removal model isn't downloaded yet.")
    session = _get_session(path)
    rgb = image.convert("RGB")
    small = np.asarray(rgb.resize((INPUT, INPUT), Image.Resampling.LANCZOS), dtype=np.float32)
    small = small / max(1.0, float(small.max()))
    x = ((small - np.array(MEAN, dtype=np.float32)) / 1.0).transpose(2, 0, 1)[None]
    name = session.get_inputs()[0].name
    pred = session.run(None, {name: x.astype(np.float32)})[0][0, 0]
    lo, hi = float(pred.min()), float(pred.max())
    pred = (pred - lo) / (hi - lo) if hi > lo else np.zeros_like(pred)
    mask = Image.fromarray(np.clip(pred * 255 + 0.5, 0, 255).astype(np.uint8), "L")
    return mask.resize(image.size, Image.Resampling.LANCZOS)
