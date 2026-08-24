"""What a file actually is, as opposed to what it says it is.

§10 rates malicious upload as a High threat and answers it with "type allowlist
by magic bytes rather than extension". This module is that answer, and it is
deliberately strict in three ways:

  * **An allowlist.** A customer proving a failed transfer sends a screenshot or
    a PDF statement. Every format outside that is a format we would be accepting
    in order to be accommodating, and each one carries its own parser bugs into
    an agent's browser.
  * **Content, never the filename.** `statement.pdf` is a claim made by whoever
    uploaded it.
  * **Polyglots are rejected, not merely mis-served.** A file that is a valid GIF
    *and* a valid HTML document is a file built to be one thing to our sniffer
    and another to a browser. Serving it as an octet-stream defeats it; refusing
    it means we never have to be right about the serving.
"""

from __future__ import annotations

import dataclasses
import io
import zipfile

MAX_BYTES = 10 * 1024 * 1024  # §10: 10 MB cap

# The whole allowlist. Adding to it is a security decision, not a convenience one.
SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)

# Markers that make a file dangerous to a browser regardless of its header.
# Checked across the whole file, because a polyglot hides the second format
# after the first one's valid prefix.
ACTIVE_CONTENT_MARKERS: tuple[bytes, ...] = (
    b"<script",
    b"<html",
    b"<!doctype html",
    b"<iframe",
    b"<svg",
    b"javascript:",
    b"<?php",
    b"<%",
    b"onerror=",
    b"onload=",
)

# A PDF may legitimately contain JavaScript. It is still a PDF we do not want:
# an agent opening it in a browser's built-in viewer is executing it.
PDF_ACTIVE_MARKERS: tuple[bytes, ...] = (b"/JavaScript", b"/JS", b"/Launch", b"/OpenAction")


class RejectedUpload(ValueError):
    """Refused before it was ever stored."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class Inspection:
    content_type: str
    extension: str
    size_bytes: int


def inspect(content: bytes, *, declared_name: str = "") -> Inspection:
    """Decide what this is, or refuse it. Never trusts `declared_name`."""
    if not content:
        raise RejectedUpload("empty", "The file is empty.")
    if len(content) > MAX_BYTES:
        raise RejectedUpload(
            "too_large", f"{len(content)} bytes exceeds the {MAX_BYTES} byte limit."
        )

    match = next(
        ((mime, ext) for signature, mime, ext in SIGNATURES if content.startswith(signature)),
        None,
    )
    if match is None:
        raise RejectedUpload(
            "unsupported_type",
            "That file type is not accepted. Send a PDF or an image (PNG, JPEG or GIF).",
        )

    content_type, extension = match
    _reject_polyglots(content, content_type)
    _reject_archive_bombs(content)
    return Inspection(content_type=content_type, extension=extension, size_bytes=len(content))


def _reject_polyglots(content: bytes, content_type: str) -> None:
    lowered = content.lower()

    if content_type == "application/pdf":
        for marker in PDF_ACTIVE_MARKERS:
            if marker.lower() in lowered:
                raise RejectedUpload(
                    "active_content",
                    "That PDF contains embedded scripting or an automatic action.",
                )
        return

    for marker in ACTIVE_CONTENT_MARKERS:
        if marker in lowered:
            raise RejectedUpload(
                "polyglot",
                "That file is a valid image and also a valid web page. A file built to "
                "be two things is a file built to be misread by one of them.",
            )


def _reject_archive_bombs(content: bytes) -> None:
    """A zip whose declared expansion is absurd relative to its size.

    Only reachable if an archive format is ever added to the allowlist — which it
    is not today. The check exists anyway, because the day somebody adds `.docx`
    to `SIGNATURES` is the day this becomes load-bearing, and a check written
    then is a check written under deadline.
    """
    if not content.startswith(b"PK\x03\x04"):
        return

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            declared = sum(entry.file_size for entry in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise RejectedUpload("corrupt_archive", "That archive could not be read.") from exc

    if declared > MAX_BYTES * 20 or (content and declared / len(content) > 100):
        raise RejectedUpload(
            "archive_bomb",
            f"That archive expands to {declared} bytes from {len(content)} — "
            "a compression ratio that only occurs on purpose.",
        )
