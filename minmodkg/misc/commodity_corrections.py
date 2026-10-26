"""Known wrong commodity matches in the site JSON, and how to correct them.

The list is a CSV in the data repository (``data/corrections/commodity.csv``)
with the columns ``source_id, observed_name, context, action, normalized_uri,
note``. A row matches an inventory when

- ``source_id`` equals the record's ``source_id``,
- ``observed_name`` equals the inventory's ``commodity.observed_name``, and
- ``context`` is empty, or equals the record's own commodity text: every
  inventory's ``observed_name`` in file order, joined by ``" | "`` (Oregon's
  ``stone | crushed | SC``).

All comparisons are exact. A row with a matching context wins over one with an
empty context. ``drop`` removes the inventory; ``remap`` sets its
``normalized_uri`` to the row's and its ``confidence`` to 1.0, and keeps
``observed_name`` and ``source``.

Only matches are corrected: a candidate without a ``normalized_uri`` is left as
it is (another extractor of the same source may carry the same string
unmatched), and so is one that already has the remap's target.

The ETL merge applies the list to each raw site before the site is normalized,
so the stored inventories, the kgrel views and the KG export all see the
corrected value. Sites created or edited through the API are not corrected.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

COLUMNS = ["source_id", "observed_name", "context", "action", "normalized_uri", "note"]
CONTEXT_SEP = " | "


@dataclass(frozen=True)
class CommodityCorrection:
    source_id: str
    observed_name: str
    context: str
    action: Literal["drop", "remap"]
    normalized_uri: Optional[str]
    note: str


def commodity_context(inventories: list[dict]) -> str:
    """The record's own commodity text, e.g. ``stone | crushed | SC``."""
    return CONTEXT_SEP.join(
        (inv.get("commodity") or {}).get("observed_name") or "" for inv in inventories
    )


class CommodityCorrections:
    def __init__(self, rows: list[CommodityCorrection]):
        self.rows = rows
        self.index: dict[tuple[str, str], dict[str, CommodityCorrection]] = {}
        for row in rows:
            by_context = self.index.setdefault((row.source_id, row.observed_name), {})
            if row.context in by_context:
                raise ValueError(f"Duplicated commodity correction: {row}")
            by_context[row.context] = row

    @staticmethod
    def from_file(path: Optional[Path]) -> CommodityCorrections:
        """Read the list; a missing file is an empty list."""
        if path is None or not path.exists():
            return CommodityCorrections([])
        return _read(str(path), path.stat().st_mtime_ns)

    def apply(self, site: dict) -> list[tuple[CommodityCorrection, dict]]:
        """Correct the site's inventories in place. Returns each applied row
        with the inventory as it was before. A site that no row matches is
        left untouched."""
        invs = site.get("mineral_inventory")
        if len(self.index) == 0 or not invs:
            return []

        context = None
        applied = []
        keep = []
        for inv in invs:
            commodity = inv.get("commodity") or {}
            by_context = self.index.get(
                (site.get("source_id"), commodity.get("observed_name"))
            )
            uri = commodity.get("normalized_uri")
            row = None
            if by_context is not None and uri is not None:
                if context is None:
                    context = commodity_context(invs)
                row = by_context.get(context) or by_context.get("")
            if row is None or row.normalized_uri == uri:
                keep.append(inv)
                continue

            applied.append((row, {**inv, "commodity": dict(commodity)}))
            if row.action == "remap":
                commodity["normalized_uri"] = row.normalized_uri
                commodity["confidence"] = 1.0
                keep.append(inv)

        if len(applied) > 0:
            site["mineral_inventory"] = keep
        return applied


@lru_cache(maxsize=4)
def _read(path: str, mtime_ns: int) -> CommodityCorrections:
    # once per process and file version
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: expected the columns {COLUMNS}")
        rows = []
        for i, raw in enumerate(reader, start=2):
            r = {k: (v or "").strip() for k, v in raw.items()}
            if r["action"] not in ("drop", "remap"):
                raise ValueError(f"{path}:{i}: action must be drop or remap")
            if (r["action"] == "remap") != (r["normalized_uri"] != ""):
                raise ValueError(
                    f"{path}:{i}: remap needs a normalized_uri, drop must not have one"
                )
            if r["source_id"] == "" or r["observed_name"] == "":
                raise ValueError(
                    f"{path}:{i}: source_id and observed_name are required"
                )
            rows.append(
                CommodityCorrection(
                    source_id=r["source_id"],
                    observed_name=r["observed_name"],
                    context=r["context"],
                    action=r["action"],  # type: ignore
                    normalized_uri=r["normalized_uri"] or None,
                    note=r["note"],
                )
            )
    return CommodityCorrections(rows)
