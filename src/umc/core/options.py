"""Option schemas.

Every converter declares its settings as a dataclass whose fields are created with
:func:`opt`. The GUI reads the attached :class:`OptionSpec` metadata and renders a
matching widget automatically, so adding a setting never requires GUI changes.

Example::

    @dataclass
    class MyOptions:
        quality: int = opt(90, "Quality", kind="slider", min=1, max=100)
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# Widget kinds understood by the settings panel.
KINDS = {
    "slider",
    "int",
    "float",
    "choice",
    "bool",
    "text",
    "textarea",
    "password",
    "color",
    "file",
}


@dataclass(frozen=True)
class OptionSpec:
    name: str
    label: str
    kind: str
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    decimals: int = 0
    unit: str = ""
    help: str = ""
    # list of (value, label) for kind="choice"
    choices: tuple[tuple[Any, str], ...] = ()
    # Only show for these target ids (None = all targets of the converter).
    targets: tuple[str, ...] | None = None
    # Show only when this returns True for the dict of current option values.
    show_if: Callable[[dict[str, Any]], bool] | None = None
    # File filter for kind="file", e.g. "Audio (*.mp3 *.wav)"
    file_filter: str = ""
    group: str = ""

    def visible(self, values: dict[str, Any], target_id: str) -> bool:
        if self.targets is not None and target_id not in self.targets:
            return False
        return self.show_if is None or bool(self.show_if(values))


def opt(
    default: Any,
    label: str,
    *,
    kind: str | None = None,
    min: float | None = None,  # noqa: A002 - mirrors widget terminology
    max: float | None = None,  # noqa: A002
    step: float | None = None,
    decimals: int = 0,
    unit: str = "",
    help: str = "",  # noqa: A002
    choices: Sequence[Any] = (),
    targets: Sequence[str] | None = None,
    show_if: Callable[[dict[str, Any]], bool] | None = None,
    file_filter: str = "",
    group: str = "",
) -> Any:
    """Declare a dataclass field together with how it should be presented."""
    if kind is None:
        if choices:
            kind = "choice"
        elif isinstance(default, bool):
            kind = "bool"
        elif isinstance(default, int):
            kind = "int"
        elif isinstance(default, float):
            kind = "float"
        else:
            kind = "text"
    if kind not in KINDS:
        raise ValueError(f"unknown option kind {kind!r}")
    norm_choices = tuple(c if isinstance(c, tuple) else (c, str(c)) for c in choices)
    spec = dict(
        label=label,
        kind=kind,
        default=default,
        min=min,
        max=max,
        step=step,
        decimals=decimals,
        unit=unit,
        help=help,
        choices=norm_choices,
        targets=tuple(targets) if targets is not None else None,
        show_if=show_if,
        file_filter=file_filter,
        group=group,
    )
    return field(default=default, metadata={"umc_spec": spec})


def option_specs(options_cls: type) -> list[OptionSpec]:
    """Return the presentation specs of every field of an options dataclass, in order."""
    specs = []
    for f in dataclasses.fields(options_cls):
        meta = f.metadata.get("umc_spec")
        if meta is None:
            continue
        specs.append(OptionSpec(name=f.name, **meta))
    return specs


def defaults(options_cls: type) -> dict[str, Any]:
    return {s.name: s.default for s in option_specs(options_cls)}


def build(options_cls: type, values: dict[str, Any] | None = None) -> Any:
    """Instantiate an options dataclass, ignoring unknown keys."""
    names = {f.name for f in dataclasses.fields(options_cls)}
    return options_cls(**{k: v for k, v in (values or {}).items() if k in names})


def parse_time(text: str | float | int) -> float:
    """Parse ``"90"``, ``"1:30"`` or ``"00:01:30.5"`` into seconds. Empty → 0."""
    if isinstance(text, int | float):
        return float(text)
    text = text.strip()
    if not text:
        return 0.0
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError as exc:
        from umc.core.errors import ConversionError

        raise ConversionError(f"“{text}” isn't a valid time. Use seconds or mm:ss.") from exc
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds
