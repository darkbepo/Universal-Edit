"""Maps a set of input files to the output targets that can be produced from them."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING

from umc.core.detect import DetectedFile

if TYPE_CHECKING:
    from umc.converters.base import Converter


@dataclass(frozen=True)
class Target:
    id: str  # unique among the targets offered for one input set, e.g. "png", "slideshow:mp4"
    label: str  # short button text, e.g. "PNG"
    ext: str  # output extension ("" for folder outputs)
    group: str  # heading under which the GUI lists it ("Image", "Video", ...)
    converter: Converter = field(compare=False, hash=False)
    hint: str = ""  # tooltip / one-line description


@cache
def converters() -> tuple[Converter, ...]:
    from umc.converters.archive import ArchiveConverter, PackConverter
    from umc.converters.audio import AudioConverter, AudioExtractConverter
    from umc.converters.data import DataConverter
    from umc.converters.document import DocumentConverter
    from umc.converters.document_images import DocumentToImagesConverter
    from umc.converters.gif import VideoToGifConverter
    from umc.converters.image import ImageConverter, ImagesToPdfConverter
    from umc.converters.model3d import ModelConverter
    from umc.converters.ocr import ImageOcrConverter, PdfOcrConverter
    from umc.converters.pdf_tools import PdfToolsConverter
    from umc.converters.slideshow import SlideshowConverter
    from umc.converters.split import DocumentSplitConverter
    from umc.converters.video import VideoConverter
    from umc.converters.video_frames import VideoToFramesConverter

    return (
        ImageConverter(),
        SlideshowConverter(),
        ImagesToPdfConverter(),
        ImageOcrConverter(),
        VideoConverter(),
        VideoToGifConverter(),
        VideoToFramesConverter(),
        AudioExtractConverter(),
        AudioConverter(),
        DataConverter(),
        DocumentConverter(),
        DocumentToImagesConverter(),
        DocumentSplitConverter(),
        PdfToolsConverter(),
        PdfOcrConverter(),
        ModelConverter(),
        ArchiveConverter(),
        PackConverter(),
    )


class MixedInputError(ValueError):
    pass


def targets_for(files: list[DetectedFile]) -> list[Target]:
    """All targets available for ``files`` (files of mixed kinds can only be packed)."""
    if not files:
        return []
    families = {f.family for f in files}
    family = families.pop() if len(families) == 1 else None  # None = mixed: only "pack" targets
    result: list[Target] = []
    seen: set[str] = set()
    for conv in converters():
        if not conv.any_family and conv.family != family:
            continue
        for t in conv.targets(files):
            if t.id not in seen:
                seen.add(t.id)
                result.append(t)
    return result
