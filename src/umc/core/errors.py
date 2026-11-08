"""Exceptions shared by converters and the GUI.

Messages on these exceptions are shown to the user as-is, so keep them plain-language.
"""

from __future__ import annotations


class ConversionError(Exception):
    """A conversion failed. ``str(exc)`` is a friendly, user-facing message."""

    def __init__(self, message: str, details: str = "") -> None:
        super().__init__(message)
        self.details = details  # technical info for the log, never shown in the UI


class ConversionCancelled(Exception):
    """The user pressed Cancel."""


class ToolMissingError(ConversionError):
    """An external program (LibreOffice, Pandoc, FFmpeg) is required but not installed."""

    def __init__(self, tool: str, download_url: str, purpose: str) -> None:
        super().__init__(f"{tool} is needed to {purpose}, but it isn't installed.")
        self.tool = tool
        self.download_url = download_url
        self.purpose = purpose
