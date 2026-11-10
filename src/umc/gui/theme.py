"""Light/dark palettes and the application stylesheet."""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

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
}


def is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    return hints.colorScheme() == Qt.ColorScheme.Dark


def palette() -> dict[str, str]:
    return DARK if is_dark() else LIGHT


def stylesheet() -> str:
    template = resources.files("umc.gui").joinpath("style.qss").read_text(encoding="utf-8")
    for key, value in sorted(palette().items(), key=lambda kv: -len(kv[0])):
        template = template.replace(f"@{key}", value)
    return template
