"""Shared types for the document editor (no Qt)."""

from __future__ import annotations

from dataclasses import dataclass, field

# Picture bytes by the name the HTML refers to them with ("img/1.png").
DocImages = dict[str, bytes]

EDITABLE_DOCUMENT_EXTS: frozenset[str] = frozenset(
    {"docx", "odt", "rtf", "doc", "md", "txt", "html", "htm"}
)
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_CHARS = 2_000_000
TOO_LARGE = "This document is too large to edit here."

# Things a document can contain that the editor can't keep.
W_HEADERS = "headers and footers"
W_TEXTBOX = "text boxes and shapes"
W_COLUMNS = "multiple columns"
W_TRACKED = "tracked changes"
W_COMMENTS = "comments"
W_NOTES = "footnotes and endnotes"
W_FIELDS = "fields"
W_OBJECTS = "embedded objects"
W_PICTURES = "pictures in a format the editor can't show"


@dataclass
class OpenedDocument:
    html: str
    images: DocImages
    source_format: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class SaveContent:
    html: str
    images: DocImages
    markdown: str | None = None
    plain_text: str | None = None


def warning_text(warnings: list[str]) -> str:
    """One plain-language sentence for the "parts that will be lost" notice."""
    if not warnings:
        return ""
    return (
        f"This document has parts the editor can't keep: {', '.join(warnings)}. "
        "They will be lost if you save over the original — use Save as to keep a copy."
    )


def next_image_name(images: DocImages, ext: str) -> str:
    return f"img/{len(images) + 1}.{ext}"
