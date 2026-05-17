"""Recent-directories persistence helpers.

The on-disk shape evolved from `list[str]` to `list[dict]` carrying
per-entry metadata (last_opened, file_count). `migrate()` accepts either
form and returns the new form. `touch()` records an open event.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _normalize(entry: Any) -> dict | None:
    if isinstance(entry, str):
        return {"path": entry, "last_opened": None, "file_count": None}
    if isinstance(entry, dict) and "path" in entry:
        return {
            "path": entry["path"],
            "last_opened": entry.get("last_opened"),
            "file_count": entry.get("file_count"),
        }
    return None


def migrate(items: list[Any]) -> list[dict]:
    """Coerce legacy str entries to the new dict form. Drops malformed entries."""
    out: list[dict] = []
    for it in items:
        n = _normalize(it)
        if n is not None:
            out.append(n)
    return out


def touch(
    items: list[Any],
    path: str,
    *,
    file_count: int | None = None,
    now: datetime | None = None,
    max_items: int = 20,
) -> list[dict]:
    """Move `path` to the front of the list, updating last_opened/file_count.

    Inserts at front if not already present. Returns a new list (input is not
    mutated). Truncates to `max_items` entries.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts = now.isoformat()

    normalized = migrate(items)
    others = [e for e in normalized if e["path"] != path]
    new_entry = {
        "path": path,
        "last_opened": ts,
        "file_count": file_count,
    }
    return [new_entry, *others][:max_items]
