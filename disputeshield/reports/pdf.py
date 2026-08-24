"""The regulator-facing PDF (§6.5, §7.3's `format=pdf`).

The CSVs are what a supervisor's systems ingest. This is what a supervisor
*reads*: the attestation first, the summary second, the per-case history third.

**Byte-reproducible, like the CSVs**, and for the same reason — a supervisor who
asks for the same period twice and gets two different files has been handed a
reason to doubt everything else in the bundle. PDFs make that harder than CSVs
do, because the format is full of places for a timestamp to hide:

  * `invariant=1` pins reportlab's creation date, modification date and document
    ID, which are otherwise regenerated on every build.
  * Page streams are left uncompressed. zlib's output is deterministic for a
    given build of zlib but not guaranteed across versions, so compressing would
    make reproducibility depend on which machine rendered the file.
  * Only the built-in Helvetica faces are used. An embedded font is subsetted,
    and a subset carries a generated name that differs between builds.
  * Nothing rendered is derived from "now". The one genuinely variable fact —
    when the export ran — lives in the manifest, outside the digests.

The attestation page is the part that matters, and it states what it does *not*
prove as plainly as what it does. A regulator-ready document that overclaims is
worse than one that says nothing.
"""

from __future__ import annotations

import dataclasses

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Per-case history is included in full up to this many cases. Beyond it the
# document says so explicitly, with the count and where the rest lives — a
# regulator-ready document must never truncate silently, because a supervisor
# reading it has no way to tell that it did.
MAX_CASES_WITH_HISTORY = 200

INK = colors.HexColor("#16161A")
MUTED = colors.HexColor("#5C5F66")
LINE = colors.HexColor("#D8D8D6")
BREACH = colors.HexColor("#B42318")
OK = colors.HexColor("#15803D")


@dataclasses.dataclass(frozen=True)
class Section:
    title: str
    rows: list[list[str]]
    widths: list[float]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "dsTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=2 * mm,
        ),
        "heading": ParagraphStyle(
            "dsHeading",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=INK,
            spaceBefore=6 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "dsBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
            spaceAfter=2 * mm,
        ),
        "muted": ParagraphStyle(
            "dsMuted",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceAfter=2 * mm,
        ),
        "mono": ParagraphStyle(
            "dsMono",
            parent=base["BodyText"],
            fontName="Courier",
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
            spaceAfter=1 * mm,
        ),
    }


def render(*, export, tenant) -> bytes:
    """Build the document from an already-built `Export`.

    Takes the export rather than the database on purpose: the PDF and the CSVs
    must describe the same period from the same read, and rebuilding the query
    here would let them disagree in the gap between two queries.
    """
    from io import BytesIO

    styles = _styles()
    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"DisputeShield complaints record — {tenant.name}",
        author="DisputeShield",
        subject=f"{export.manifest['period_from']} to {export.manifest['period_to']}",
        creator="DisputeShield",
        # Pins the creation date, the modification date and the document ID to
        # fixed values. Without it each of the three is regenerated per build and
        # two identical reports differ.
        invariant=1,
        # Uncompressed streams, deliberately. zlib's output is deterministic for
        # a given zlib build but not guaranteed across versions — so compressing
        # would make byte-reproducibility depend on which machine rendered the
        # document, which is exactly the property this is trying to have. It also
        # leaves the document greppable, which for an artefact a supervisor may
        # need to examine is a feature rather than a cost.
        pageCompression=0,
    )

    story: list = []
    story += _cover(export, tenant, styles)
    story.append(PageBreak())
    story += _summary(export, styles)
    story += _case_table(export, styles)
    story += _histories(export, styles)

    document.build(story, onFirstPage=_furniture, onLaterPages=_furniture)
    return buffer.getvalue()


def _furniture(canvas, document) -> None:
    """Page number and footer. Nothing derived from the current time."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 12 * mm, "DisputeShield — regulator-ready complaints record")
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {document.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
    canvas.restoreState()


def _history_rows(export) -> int:
    """Records covering this period, counted from the export's own history.csv.

    Derived from the bundle rather than from a live query on purpose: it is a
    fact about the period, so it is stable for a closed one, and it is the number
    a reader can check by opening the attached CSV.
    """
    return max(export.files["history.csv"].decode().count("\n") - 1, 0)


def _cover(export, tenant, styles) -> list:
    manifest = export.manifest
    integrity = manifest["integrity"]
    chain = integrity["chain"]
    anchor = integrity.get("anchor", {})

    story = [
        Paragraph("Complaints record", styles["title"]),
        Paragraph(
            f"{tenant.name} &nbsp;·&nbsp; "
            f"{manifest['period_from'][:10]} to {manifest['period_to'][:10]}",
            styles["muted"],
        ),
        Spacer(1, 6 * mm),
        Paragraph("Integrity attestation", styles["heading"]),
    ]

    verdict = "VERIFIED" if chain["verified"] else "FAILED VERIFICATION"
    story.append(
        Paragraph(
            f"<b>Audit chain: {verdict}</b> — {_history_rows(export)} records cover "
            f"the {manifest['case_count']} cases in this period.",
            ParagraphStyle(
                "verdict",
                parent=styles["body"],
                fontSize=11,
                leading=14,
                textColor=OK if chain["verified"] else BREACH,
            ),
        )
    )
    if not chain["verified"]:
        # An export from a database whose chain does not verify must say so on the
        # first page. Producing a clean-looking bundle from a tampered history is
        # the worst thing this feature could do.
        story.append(
            Paragraph(
                f"The chain first breaks at sequence {chain['first_break']}. Records "
                "before that point are internally consistent; records from that point "
                "onward cannot be relied upon. This document is being produced from a "
                "database whose integrity check has failed, and that fact is stated "
                "here rather than omitted.",
                ParagraphStyle("warn", parent=styles["body"], textColor=BREACH),
            )
        )

    story.append(
        _facts(
            [
                ("Cases in period", str(manifest["case_count"])),
                ("History records", str(_history_rows(export))),
                ("Imported from a prior system", str(manifest.get("imported_case_count", 0))),
                ("Anchoring authority", anchor.get("authority") or "none configured"),
            ],
            styles,
        )
    )

    story += [
        Spacer(1, 3 * mm),
        Paragraph(
            "<b>What this attestation does and does not establish.</b> The hash chain "
            "shows that no record was altered relative to its neighbours. The signature "
            "shows that DisputeShield computed that check and obtained this result. "
            "Neither establishes <i>when</i> the chain existed — only an external "
            "anchor from a third-party timestamp authority does that.",
            styles["muted"],
        ),
        Paragraph(
            "<b>The live figures are deliberately not in this document.</b> The chain "
            "head, the current checkpoint and its signature, the count of records held "
            "in total and whether the latest checkpoint has been externally anchored "
            "are all statements about this system <i>now</i> rather than about this "
            "period. Printing them here would mean a closed period produced a different "
            "document every time it was exported, and a supervisor who asks for the same "
            "period twice and receives two different files has been handed a reason to "
            "doubt everything in both. They are published in the accompanying "
            "<font face='Courier'>manifest.json</font> and at "
            "<font face='Courier'>GET /v1/audit/verify</font>, which is where a figure "
            "about the present belongs.",
            styles["muted"],
        ),
        Paragraph(
            "The figures in this document are derived from the accompanying "
            "<font face='Courier'>cases.csv</font> and "
            "<font face='Courier'>history.csv</font>, whose SHA-256 digests are listed "
            "below. Recomputing a digest and comparing it with the value here is "
            "sufficient to show the data has not been altered since the export ran.",
            styles["muted"],
        ),
        _facts([(name, digest) for name, digest in sorted(manifest["files"].items())], styles),
    ]

    if manifest.get("imported_case_count"):
        story.append(
            Paragraph(
                f"<b>{manifest['imported_case_count']} of {manifest['case_count']} cases "
                "were imported from a prior system.</b> " + manifest["integrity_note"],
                styles["muted"],
            )
        )

    return story


def _summary(export, styles) -> list:
    manifest = export.manifest
    return [
        Paragraph("Summary", styles["heading"]),
        _facts(
            [
                ("Complaints in period", str(manifest["case_count"])),
                ("Of which imported", str(manifest.get("imported_case_count", 0))),
                ("Breached a mandated window", str(manifest["breach_count"])),
                ("Period from", manifest["period_from"]),
                ("Period to", manifest["period_to"]),
            ],
            styles,
        ),
    ]


def _case_table(export, styles) -> list:
    rows = _csv_rows(export.files["cases.csv"])
    if not rows:
        return [
            Paragraph("Complaints", styles["heading"]),
            Paragraph("No complaints were filed in this period.", styles["body"]),
        ]

    columns = ("reference", "origin", "category", "status", "outcome", "submitted_at")
    header = ["Reference", "Origin", "Category", "Status", "Outcome", "Submitted", "Breach"]

    body = [header]
    for row in rows:
        breached = "yes" if row.get("breach_resolution") == "true" else ""
        body.append([*(_short(row.get(name, "")) for name in columns), breached])

    table = Table(
        body,
        repeatRows=1,
        hAlign="LEFT",
        colWidths=[32 * mm, 20 * mm, 30 * mm, 24 * mm, 20 * mm, 30 * mm, 16 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 7.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TEXTCOLOR", (6, 1), (6, -1), BREACH),
            ]
        )
    )
    return [Paragraph("Complaints", styles["heading"]), table]


def _histories(export, styles) -> list:
    """Per-case history, in full — or an explicit statement that it is not."""
    cases = _csv_rows(export.files["cases.csv"])
    history = _csv_rows(export.files["history.csv"])

    story: list = [PageBreak(), Paragraph("Per-case history", styles["heading"])]

    if len(cases) > MAX_CASES_WITH_HISTORY:
        story.append(
            Paragraph(
                f"This period contains {len(cases)} complaints, more than the "
                f"{MAX_CASES_WITH_HISTORY} for which per-case history is reproduced in "
                "this document. The complete history for every complaint — including "
                f"the {len(history)} entries omitted here — is in the accompanying "
                "<font face='Courier'>history.csv</font>, whose digest is listed on the "
                "first page. Nothing has been discarded; this document is abridged and "
                "says so.",
                ParagraphStyle("cap", parent=styles["body"], textColor=BREACH),
            )
        )
        return story

    by_reference: dict[str, list[dict]] = {}
    for entry in history:
        by_reference.setdefault(entry["reference"], []).append(entry)

    for case in cases:
        entries = by_reference.get(case["reference"], [])
        block = [
            Paragraph(
                f"<b>{case['reference']}</b> — {case['category']} "
                f"({'imported' if case.get('origin') == 'imported' else 'DisputeShield'})",
                styles["body"],
            )
        ]
        if not entries:
            block.append(Paragraph("No recorded history.", styles["muted"]))
        else:
            table = Table(
                [["Seq", "Occurred", "Event", "Actor", "Reason", "Record hash"]]
                + [
                    [
                        entry["sequence"],
                        _short(entry["occurred_at"]),
                        entry["event_type"],
                        f"{entry['actor_type']}:{_short(entry['actor_id'], 12)}",
                        _short(entry["reason"], 40),
                        _short(entry["record_hash"], 22),
                    ]
                    for entry in entries
                ],
                repeatRows=1,
                hAlign="LEFT",
                colWidths=[10 * mm, 28 * mm, 36 * mm, 26 * mm, 44 * mm, 30 * mm],
            )
            table.setStyle(
                TableStyle(
                    [
                        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 6.5),
                        ("FONT", (0, 1), (-1, -1), "Helvetica", 6.5),
                        ("FONT", (5, 1), (5, -1), "Courier", 6),
                        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
                        ("LINEBELOW", (0, 0), (-1, 0), 0.5, LINE),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                )
            )
            block.append(table)
        block.append(Spacer(1, 4 * mm))
        # Keeps a case's heading with at least the start of its history, so a page
        # break never separates a reference from the events under it.
        story.append(KeepTogether(block[:2]))
        story += block[2:]

    return story


def _facts(pairs, styles) -> Table:
    table = Table(
        [[Paragraph(k, styles["muted"]), Paragraph(_wrap(v), styles["mono"])] for k, v in pairs],
        colWidths=[52 * mm, 116 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, LINE),
            ]
        )
    )
    return table


def _csv_rows(blob: bytes) -> list[dict]:
    import csv
    import io

    return list(csv.DictReader(io.StringIO(blob.decode("utf-8"))))


def _short(value: str, limit: int = 26) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _wrap(value: str) -> str:
    """Break a long digest so it wraps instead of overflowing the column."""
    if len(value) <= 48:
        return value
    return "<br/>".join(value[i : i + 48] for i in range(0, len(value), 48))
