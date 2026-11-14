"""Light/dark palettes, the application stylesheet and the user's theme choice.

The choice ("system", "light" or "dark") is kept in QSettings ``appearance/theme``. "system"
follows the OS colour scheme. Everything that paints with the palette listens to
``notifier().changed``, which fires whenever the effective light / dark look changes, whether
the user picked another mode or the OS switched while following it.
"""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QGuiApplication

CHOICES = ("system", "light", "dark")
LABELS = {"system": "Same as the computer", "light": "Light", "dark": "Dark"}
ICONS = {"system": "sun-moon", "light": "sun", "dark": "moon"}
SETTING = "appearance/theme"

LIGHT = {
    "bg": "#f3f4f7",
    "surface": "#ffffff",
    "surface_alt": "#eff0f4",
    "border": "#e3e5ea",
    "border_strong": "#cfd3db",
    "text": "#121419",
    "muted": "#6a7080",
    "accent": "#5b5ef0",
    "accent_hover": "#4a4ddc",
    "accent_text": "#ffffff",
    "accent_soft": "#eeeefe",
    "accent_border": "#c4c6fa",
    "danger": "#d43c45",
    "danger_soft": "#fdeced",
    "success": "#16955b",
    "success_soft": "#e6f6ee",
    "clip_video": "#5b5ef0",
    "clip_picture": "#1f9d9d",
    "clip_audio": "#1f9d61",
    "clip_text": "#d98a1f",
    "playhead": "#d43c45",
    # code editor (same values as --code-* in web/src/styles.css)
    "code_keyword": "#8839ef",
    "code_string": "#2f8f2f",
    "code_comment": "#8c8fa1",
    "code_number": "#d4570b",
    "code_function": "#1e66f5",
    "code_type": "#b7791f",
    "code_tag": "#d20f39",
    "code_attribute": "#b7791f",
    "code_operator": "#5c6070",
    "code_builtin": "#0e8a8a",
    "code_gutter": "#9aa0ad",
    "code_line": "#f4f5fa",
    "code_match": "#fff2a8",
    "code_bracket": "#dfe1fb",
}

DARK = {
    "bg": "#0f1014",
    "surface": "#17191e",
    "surface_alt": "#20232a",
    "border": "#272a31",
    "border_strong": "#363a43",
    "text": "#eceef2",
    "muted": "#8e94a2",
    "accent": "#8385ff",
    "accent_hover": "#9b9dff",
    "accent_text": "#0c0c1c",
    "accent_soft": "#22234a",
    "accent_border": "#3e4185",
    "danger": "#ff6b70",
    "danger_soft": "#3a1d21",
    "success": "#3ccf8e",
    "success_soft": "#16301f",
    "clip_video": "#8385ff",
    "clip_picture": "#2bb0b0",
    "clip_audio": "#3ccf8e",
    "clip_text": "#e8a040",
    "playhead": "#ff6b70",
    "code_keyword": "#c792ea",
    "code_string": "#a6d189",
    "code_comment": "#7f8796",
    "code_number": "#f5a97f",
    "code_function": "#8aadf4",
    "code_type": "#eed49f",
    "code_tag": "#ed8796",
    "code_attribute": "#eed49f",
    "code_operator": "#a5adcb",
    "code_builtin": "#8bd5ca",
    "code_gutter": "#5b6070",
    "code_line": "#1f2128",
    "code_match": "#5a4b16",
    "code_bracket": "#2c2e52",
}


_choice = "system"


class _Notifier(QObject):
    changed = Signal()  # the look switched between light and dark
    choice_changed = Signal(str)  # the user's System / Light / Dark choice


_notifier: _Notifier | None = None
_last_dark: bool | None = None


def notifier() -> _Notifier:
    """``changed`` fires when the effective look switches between light and dark."""
    global _notifier, _last_dark
    if _notifier is None:
        _notifier = _Notifier()
        _last_dark = is_dark()
        QGuiApplication.styleHints().colorSchemeChanged.connect(_scheme_changed)
    return _notifier


def _scheme_changed(_scheme=None) -> None:
    _notify()


def _notify() -> None:
    global _last_dark
    dark = is_dark()
    if _notifier is not None and dark != _last_dark:
        _last_dark = dark
        from umc.gui import icons

        # icons / colours that open pages and windows baked in when they were built
        icons.retint(swap_colors(to_dark=dark))
        _notifier.changed.emit()


def swap_colors(to_dark: bool) -> dict[str, str]:
    """Each colour of the palette being left → the same token's colour in the new one."""
    old, new = (LIGHT, DARK) if to_dark else (DARK, LIGHT)
    swap: dict[str, str] = {}
    for key, value in old.items():
        if value != new[key]:
            swap.setdefault(value.lower(), new[key])
    return swap


def _hint_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def choice() -> str:
    return _choice


def next_choice(current: str | None = None) -> str:
    """System → Light → Dark → System."""
    current = _choice if current is None else current
    i = CHOICES.index(current) if current in CHOICES else -1
    return CHOICES[(i + 1) % len(CHOICES)]


def saved_choice() -> str:
    value = QSettings().value(SETTING, "system", str)
    return value if value in CHOICES else "system"


def set_choice(value: str, save: bool = True) -> None:
    """Switch to ``value`` (and remember it). Qt is told too, so native parts (title bars,
    Fusion's palette) follow where the platform allows it."""
    global _choice
    if value not in CHOICES:
        value = "system"
    _choice = value
    hints = QGuiApplication.styleHints()
    try:
        if value == "system":
            hints.unsetColorScheme()
        else:
            hints.setColorScheme(Qt.ColorScheme.Dark if value == "dark" else Qt.ColorScheme.Light)
    except AttributeError:  # Qt older than 6.8: our own palette still follows the choice
        pass
    if save:
        settings = QSettings()
        settings.setValue(SETTING, value)
        settings.sync()
    _notify()
    if _notifier is not None:
        _notifier.choice_changed.emit(value)


def load_choice() -> None:
    """At startup, before the first window: apply the remembered choice."""
    set_choice(saved_choice(), save=False)


def is_dark() -> bool:
    if _choice == "dark":
        return True
    if _choice == "light":
        return False
    return _hint_dark()


def palette() -> dict[str, str]:
    return DARK if is_dark() else LIGHT


def stylesheet() -> str:
    template = resources.files("umc.gui").joinpath("style.qss").read_text(encoding="utf-8")
    for key, value in sorted(palette().items(), key=lambda kv: -len(kv[0])):
        template = template.replace(f"@{key}", value)
    return template
