"""Light/dark palettes and the application stylesheet."""

from __future__ import annotations

from importlib import resources

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

LIGHT = {
    "bg": "#f6f7f9",
    "surface": "#ffffff",
    "surface_alt": "#eef0f4",
    "border": "#dfe2e8",
    "text": "#16181d",
    "muted": "#6b7280",
    "accent": "#5b5bf0",
    "accent_hover": "#4848e0",
    "accent_text": "#ffffff",
    "accent_soft": "#e9e9fe",
    "danger": "#d43c3c",
    "success": "#1f9d61",
}

DARK = {
    "bg": "#121317",
    "surface": "#1b1d23",
    "surface_alt": "#24262e",
    "border": "#2f323b",
    "text": "#eceef3",
    "muted": "#9097a5",
    "accent": "#7c7cff",
    "accent_hover": "#9494ff",
    "accent_text": "#0d0d1a",
    "accent_soft": "#2a2a52",
    "danger": "#ff6b6b",
    "success": "#3ccf8e",
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
