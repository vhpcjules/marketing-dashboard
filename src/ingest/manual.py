"""Manual inputs: Google Business Profile (GMB) and Hotjar, by file.

Neither source has a connector. Someone exports the month's figures and
saves them as

    data/manual/<year>/<domain>_<YYYY-MM>.json      (preferred)
    data/manual/<year>/<domain>_<YYYY-MM>.csv       (a header row and one data
                                                     row, or two columns of
                                                     field,value)

and this module turns the file into an open snapshot exactly like a
connector pull, with the same _meta block, so the build treats it no
differently from NetSuite data.

The one deliberate difference: a MISSING file is not an error. It returns a
`MissingManualInput` marker and the build renders a "data pending" callout
that says which file to add. The alternative - failing the whole build
because a Hotjar export is a day late - would either block the refresh or
train people to skip the section. A pending callout is visible and cheap.

A PRESENT file that is wrong is still an error. Required fields missing,
a value that is not a number, a negative count, or a `period` that names a
different month all raise ManualInputError, because a wrong figure that
renders is worse than a missing one that says so.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from ..freeze import SnapshotStore
from .common import IngestError, MissingManualInput, dec, jsonable

__all__ = [
    "MANUAL_ROOT", "REQUIRED_FIELDS", "ManualInput", "ManualInputError", "MissingManualInput",
    "manual_path_candidates", "load_manual", "ingest_manual",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = REPO_ROOT / "data" / "manual"
SOURCE = "manual export via data/manual/"

# The contract per domain. Adding a field here is a methodology change and
# gets its own commit; a file with extra numeric fields is accepted and the
# extras are kept, a file missing a required one is refused.
REQUIRED_FIELDS: Mapping[str, tuple[str, ...]] = {
    # Google Business Profile performance: how people found and acted on the listing.
    "gmb": ("impressions", "website_clicks", "calls", "direction_requests"),
    # Hotjar: session recordings and feedback for the month.
    "hotjar": ("recordings", "rage_click_recordings", "feedback_responses"),
}

# String-valued keys that describe the file rather than measure anything.
_META_KEYS = frozenset({"period", "source", "exported_by", "exported_at", "note", "notes"})


class ManualInputError(IngestError):
    pass


@dataclass(frozen=True)
class ManualInput:
    domain: str
    month: str
    path: Path
    values: Mapping[str, Decimal]
    notes: Mapping[str, str] = field(default_factory=dict)

    def body(self) -> dict:
        return jsonable({**dict(self.values), "notes": dict(self.notes),
                         "file": str(self.path.relative_to(REPO_ROOT)) if self.path.is_relative_to(REPO_ROOT)
                         else self.path.name})


def manual_path_candidates(domain: str, month: str, root: Path = MANUAL_ROOT) -> tuple[Path, Path]:
    year = month.split("-")[0]
    stem = root / year / f"{domain}_{month}"
    return (stem.with_suffix(".json"), stem.with_suffix(".csv"))


def _read_csv(path: Path) -> dict[str, Any]:
    with path.open(newline="") as fh:
        rows = [r for r in csv.reader(fh) if any(c.strip() for c in r)]
    if not rows:
        raise ManualInputError(f"{path}: empty file")
    if all(len(r) == 2 for r in rows):
        # field,value pairs; tolerate a header row that says so
        pairs = rows[1:] if [c.strip().lower() for c in rows[0]] == ["field", "value"] else rows
        return {k.strip(): v.strip() for k, v in pairs}
    if len(rows) == 2 and len(rows[0]) == len(rows[1]):
        return {k.strip(): v.strip() for k, v in zip(rows[0], rows[1])}
    raise ManualInputError(
        f"{path}: expected a header row plus one data row, or two columns of field,value; "
        f"got {len(rows)} rows of {[len(r) for r in rows]} columns"
    )


def _read(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ManualInputError(f"{path}: top level must be an object of field: value")
        return raw
    return _read_csv(path)


def load_manual(domain: str, month: str, root: Path = MANUAL_ROOT) -> ManualInput | MissingManualInput:
    """The month's figures, or a marker saying they have not arrived."""
    if domain not in REQUIRED_FIELDS:
        raise ManualInputError(f"unknown manual domain {domain!r}; known: {sorted(REQUIRED_FIELDS)}")
    candidates = manual_path_candidates(domain, month, root)
    present = [p for p in candidates if p.exists()]
    if not present:
        return MissingManualInput(domain, month, candidates)
    if len(present) > 1:
        raise ManualInputError(f"both {present[0].name} and {present[1].name} exist; keep one")
    path = present[0]
    raw = _read(path)

    period = raw.get("period")
    if period is not None and str(period).strip() != month:
        raise ManualInputError(f"{path.name}: file says period {period!r} but is named for {month}")

    values: dict[str, Decimal] = {}
    notes: dict[str, str] = {}
    for key, val in raw.items():
        if key in _META_KEYS:
            if val is not None:
                notes[key] = str(val)
            continue
        if isinstance(val, float):
            # JSON floats are how a manual file arrives; the digits typed into
            # the file are what json gives back through repr, so this is exact.
            val = repr(val)
        try:
            d = dec(val, f"{path.name}:{key}")
        except (TypeError, ValueError) as e:
            raise ManualInputError(str(e)) from None
        if d < 0:
            raise ManualInputError(f"{path.name}: {key} is negative ({d}); these are counts")
        values[key] = d

    missing = [f for f in REQUIRED_FIELDS[domain] if f not in values]
    if missing:
        raise ManualInputError(
            f"{path.name}: missing required field(s) {missing}; {domain} needs {list(REQUIRED_FIELDS[domain])}"
        )
    return ManualInput(domain, month, path, values, notes)


def ingest_manual(store: SnapshotStore, domain: str, month: str, *, root: Path = MANUAL_ROOT,
                  pulled_at: datetime | None = None) -> Path | MissingManualInput:
    """Write the manual file as an open snapshot; pass the marker through."""
    loaded = load_manual(domain, month, root)
    if isinstance(loaded, MissingManualInput):
        return loaded
    from ..freeze import query_hash
    return store.write_open(
        month, domain, loaded.body(), query_id=f"manual:{domain}",
        query_hash_=query_hash(loaded.path.read_text()), row_count=1,
        pulled_at=pulled_at or datetime.now(timezone.utc).replace(microsecond=0),
        source=f"{SOURCE}{loaded.path.name}",
    )
