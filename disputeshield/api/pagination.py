"""Cursor pagination.

Offset pagination over a queue sorted by urgency is wrong in a way that is easy
to miss: cases move between pages while an agent is reading them, so page two
skips whatever page one pushed down. On a queue whose whole purpose is that
nothing is missed, that is a defect, not a nuisance.
"""

from __future__ import annotations

from rest_framework.pagination import CursorPagination


class DisputeCursorPagination(CursorPagination):
    page_size = 50
    max_page_size = 200
    page_size_query_param = "limit"
    # Ties broken by id: without a unique tiebreaker two cases sharing a deadline
    # can appear on both pages or on neither.
    ordering = ("-breach_resolution", "-breach_ack", "resolution_deadline", "id")
