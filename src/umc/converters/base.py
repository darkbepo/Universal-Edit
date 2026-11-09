"""Converter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from umc.core.detect import DetectedFile, MediaFamily

if TYPE_CHECKING:
    from umc.core.job import ConversionJob
    from umc.core.registry import Target


class Converter(ABC):
    """Turns a list of same-family input files into output files.

    Subclasses must not import Qt — they are driven purely through :class:`ConversionJob`.
    """

    #: Family of the inputs this converter accepts.
    family: ClassVar[MediaFamily]
    #: Options dataclass (see :mod:`umc.core.options`).
    options_cls: ClassVar[type]
    #: Offered for inputs of any family — also mixed ones (e.g. "pack into a ZIP").
    any_family: ClassVar[bool] = False

    @abstractmethod
    def targets(self, files: list[DetectedFile]) -> list[Target]:
        """Output targets this converter can produce for *all* of ``files``."""

    @abstractmethod
    def run(self, job: ConversionJob) -> list[Path]:
        """Perform the conversion and return the paths that were written."""

    def target(self, id: str, label: str, ext: str, group: str, hint: str = "") -> Target:  # noqa: A002
        from umc.core.registry import Target

        return Target(id=id, label=label, ext=ext, group=group, converter=self, hint=hint)
