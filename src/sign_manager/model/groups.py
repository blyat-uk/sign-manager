"""DerivedGroupModel — groups labels whose time intervals overlap.

Mirrors the legacy interval-overlap merge in
``sign_manager.ui.gallery_widget.compute_label_groups`` exactly:

* Sort labels by ``start_time``.
* Walk in order, extending the current group as long as the next label's
  ``start_time`` is ``<= max_end`` of the group so far. ``max_end`` tracks the
  largest ``end_time`` seen.
* Each emitted group's ``start`` is the minimum of its labels' ``start_time``
  (= the first sorted label's ``start_time``) and its ``end`` is the maximum
  of its labels' ``end_time`` (= the final ``max_end``).

``representative_time`` is the "best-fit" time picked by
``_best_representative_time`` (also mirrored from legacy): among the candidate
times {midpoint of each label}, pick the one at which the most labels are
simultaneously visible. Ties go to the earlier candidate (strict ``>``).

Subscribes to LabelStore signals and recomputes groups when labels are
added/removed/mutated. Currently does a full rebuild on every signal — the
algorithm is O(N log N) and label counts are typically small, so this is
acceptable for v1. Incremental updates can be added later if profiling shows
a need.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from sign_manager.model.ass_file import LabelDialogue
from sign_manager.model.label_store import LabelStore
from sign_manager.model.types import LabelId


@dataclass(frozen=True)
class LabelGroup:
    group_id: str
    label_ids: tuple[LabelId, ...]
    start: float
    end: float
    representative_time: float


class DerivedGroupModel(QObject):
    """Maintains a list of LabelGroups derived from LabelStore state.

    Signals:
      groups_changed(set[str]): emitted whenever the group list changes,
      carrying the set of affected group_ids (rebuild emits the full set).
    """

    groups_changed = pyqtSignal(set)

    def __init__(self, store: LabelStore) -> None:
        super().__init__()
        self._store = store
        self._groups: list[LabelGroup] = []
        # Per-label cache of the label's own (start_time, end_time) at the
        # time the current group structure was computed. Maintained by
        # `_refresh_window_cache` and consulted by `_on_mutated` to skip the
        # O(N) recompute when no affected label's window has changed (e.g.
        # position / style / text / colour edits don't touch time fields).
        self._label_to_group_window: dict[LabelId, tuple[float, float]] = {}
        store.file_loaded.connect(self._rebuild)
        store.labels_added.connect(self._rebuild_signal_all)
        store.labels_removed.connect(self._rebuild_signal_all)
        store.labels_mutated.connect(self._on_mutated)
        self._rebuild()

    @property
    def groups(self) -> list[LabelGroup]:
        return list(self._groups)

    # --- Rebuild paths ---------------------------------------------

    def _rebuild(self, *_args) -> None:
        old_ids = {g.group_id for g in self._groups}
        self._groups = self._compute_groups()
        self._refresh_window_cache()
        new_ids = {g.group_id for g in self._groups}
        if old_ids != new_ids or old_ids:
            self.groups_changed.emit(old_ids | new_ids)

    def _rebuild_signal_all(self, _affected_ids) -> None:
        self._rebuild()

    def _on_mutated(self, affected_ids: set) -> None:
        """Rebuild only if an affected label's time window has changed.

        For position / style / text edits the label's (start_time, end_time)
        is unchanged, so the interval-merge result is guaranteed identical
        and we can skip the O(N) recompute entirely. We consult the
        per-label group-window cache populated by ``_compute_groups``.
        """
        state = self._store.state
        windows_changed = False
        for lid in affected_ids:
            label = state.labels.get(lid)
            if label is None:
                # Label vanished from state -- treat as a structural change.
                windows_changed = True
                break
            cached_window = self._label_to_group_window.get(lid)
            if cached_window is None:
                # Newly tracked id we don't know about; play it safe.
                windows_changed = True
                break
            if (label.start_time, label.end_time) != cached_window:
                windows_changed = True
                break
        if not windows_changed:
            return

        old_groups = self._groups
        new_groups = self._compute_groups()
        old_key = [(g.label_ids, g.start, g.end) for g in old_groups]
        new_key = [(g.label_ids, g.start, g.end) for g in new_groups]
        if old_key != new_key:
            old_ids = {g.group_id for g in old_groups}
            new_ids = {g.group_id for g in new_groups}
            self._groups = new_groups
            self._refresh_window_cache()
            self.groups_changed.emit(old_ids | new_ids)
        # else: identical, no emit (cache is still valid since structure is
        # the same).

    def _refresh_window_cache(self) -> None:
        """Rebuild ``_label_to_group_window`` from the live store state.

        Stores each label's OWN (start_time, end_time) -- not the group's
        merged span -- so that `_on_mutated` can detect when a mutation
        actually moved the label's time window. Two labels in the same
        group can have different individual windows; comparing the
        group's merged span would produce false positives.
        """
        state = self._store.state
        cache: dict[LabelId, tuple[float, float]] = {}
        for g in self._groups:
            for lid in g.label_ids:
                label = state.labels.get(lid)
                if label is not None:
                    cache[lid] = (label.start_time, label.end_time)
        self._label_to_group_window = cache

    # --- Computation -----------------------------------------------

    def _compute_groups(self) -> list[LabelGroup]:
        """Group labels whose time intervals overlap (interval-merge).

        Mirrors ``compute_label_groups`` in gallery_widget.py exactly.
        """
        state = self._store.state
        if not state.order:
            return []

        # Pair (label_id, dialogue) so we keep id alongside the sort.
        pairs: list[tuple[LabelId, LabelDialogue]] = [
            (lid, state.labels[lid]) for lid in state.order
        ]
        # Stable sort by start_time (matches legacy sorted(..., key=start_time)).
        pairs.sort(key=lambda p: p[1].start_time)

        merged: list[list[tuple[LabelId, LabelDialogue]]] = []
        cur: list[tuple[LabelId, LabelDialogue]] = [pairs[0]]
        max_end = pairs[0][1].end_time

        for pair in pairs[1:]:
            _, dlg = pair
            if dlg.start_time <= max_end:
                cur.append(pair)
                if dlg.end_time > max_end:
                    max_end = dlg.end_time
            else:
                merged.append(cur)
                cur = [pair]
                max_end = dlg.end_time
        merged.append(cur)

        result: list[LabelGroup] = []
        for group_pairs in merged:
            ids = tuple(lid for lid, _ in group_pairs)
            dialogues = [dlg for _, dlg in group_pairs]
            start = min(d.start_time for d in dialogues)
            end = max(d.end_time for d in dialogues)
            rep = _best_representative_time(dialogues)
            result.append(LabelGroup(
                group_id=_make_group_id(list(ids)),
                label_ids=ids,
                start=start,
                end=end,
                representative_time=rep,
            ))
        return result


def _best_representative_time(labels: list[LabelDialogue]) -> float:
    """Pick the per-label midpoint at which the most labels are simultaneously
    visible.

    Mirrors ``_best_representative_time`` in gallery_widget.py exactly:
      * Initial ``best_time`` is the midpoint of ``labels[0]``; initial
        ``best_count`` is 0 (so the first iteration always overrides
        ``best_time``, given any non-empty group).
      * For each label, compute its midpoint ``t`` and count how many labels
        have ``start_time <= t <= end_time``.
      * Update best on strict ``count > best_count`` — ties go to the earlier
        candidate (i.e., the label that appears first in the input list).
    """
    best_time = (labels[0].start_time + labels[0].end_time) / 2
    best_count = 0
    for lb in labels:
        t = (lb.start_time + lb.end_time) / 2
        count = sum(1 for other in labels if other.start_time <= t <= other.end_time)
        if count > best_count:
            best_count = count
            best_time = t
    return best_time


def _make_group_id(label_ids: list[LabelId]) -> str:
    """Stable group id derived from the first label id in the group.
    Stable across rebuilds for the same group composition."""
    return f"G-{label_ids[0]}"
