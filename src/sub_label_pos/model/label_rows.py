"""Group labels by exact (start_time, end_time) equality for the labels sidebar.

Distinct from ``model/groups.py`` (``compute_label_groups``), which merges any
labels whose intervals overlap into a single group. The labels sidebar wants a
stricter notion: only labels whose start AND end match exactly belong in the
same row.

Spec: docs/superpowers/specs/2026-05-18-labels-sidebar-design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from sub_label_pos.model.ass_file import LabelDialogue


@dataclass(frozen=True)
class LabelGroupRow:
    """One row in the labels sidebar.

    A row represents one or more labels that share identical
    ``(start_time, end_time)``. The first label's text is the row's
    primary line; secondary labels stack underneath.
    """
    start_time: float
    end_time: float
    labels: tuple["LabelDialogue", ...]


def group_labels_by_exact_timing(
    labels: Iterable["LabelDialogue"],
) -> list[LabelGroupRow]:
    """Group labels with identical (start_time, end_time) into rows.

    Rows are sorted by start_time ascending, then end_time ascending.
    Within a row, labels keep their input-list order.
    """
    by_key: dict[tuple[float, float], list] = {}
    order: list[tuple[float, float]] = []
    for lb in labels:
        key = (lb.start_time, lb.end_time)
        if key not in by_key:
            by_key[key] = []
            order.append(key)
        by_key[key].append(lb)

    rows = [
        LabelGroupRow(
            start_time=start, end_time=end, labels=tuple(by_key[(start, end)]),
        )
        for (start, end) in order
    ]
    rows.sort(key=lambda r: (r.start_time, r.end_time))
    return rows
