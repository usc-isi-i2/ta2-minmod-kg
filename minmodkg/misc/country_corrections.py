"""Countries for sources whose records carry none.

The list is a CSV in the data repository with the columns ``source_id,
normalized_uri, note``. Each row says every record of ``source_id`` is in the
country ``normalized_uri``. A record of a listed source that has no country
candidate gets one, with ``confidence`` 1.0 and ``source`` set to ``SOURCE``.
A record that already has a country is left as it is, whatever the list says.

The ETL merge applies the list to each raw site before the site is normalized,
so the merge-time state repair sees the country: a state matched in another
country (asbestos ``CA`` -> Canelones, Uruguay) is re-resolved inside it. Sites
created or edited through the API are not corrected.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from minmodkg.models.kg.base import NS_MR

COLUMNS = ["source_id", "normalized_uri", "note"]
SOURCE = "MinMod country corrections"


@dataclass(frozen=True)
class CountryCorrection:
    source_id: str
    normalized_uri: str
    note: str


class CountryCorrections:
    def __init__(self, rows: list[CountryCorrection]):
        self.rows = rows
        self.index: dict[str, CountryCorrection] = {}
        for row in rows:
            if row.source_id in self.index:
                raise ValueError(f"Duplicated country correction: {row}")
            self.index[row.source_id] = row

    @staticmethod
    def from_file(path: Optional[Path]) -> CountryCorrections:
        """Read the list; a missing file is an empty list."""
        if path is None or not path.exists():
            return CountryCorrections([])
        return read_list(str(path), path.stat().st_mtime_ns)

    def apply(self, site: dict) -> Optional[CountryCorrection]:
        """Give the site its source's country, in place, when it has none.
        Returns the applied row, or None when the site is left untouched."""
        row = self.index.get(site.get("source_id") or "")
        if row is None:
            return None
        location_info = site.get("location_info") or {}
        if len(location_info.get("country") or []) > 0:
            return None
        # first, as the sources that do carry a country write it
        site["location_info"] = {
            "country": [
                {
                    "source": SOURCE,
                    "confidence": 1.0,
                    "normalized_uri": row.normalized_uri,
                }
            ],
            **{k: v for k, v in location_info.items() if k != "country"},
        }
        return row


@lru_cache(maxsize=4)
def read_list(path: str, mtime_ns: int) -> CountryCorrections:
    # once per process and file version
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"{path}: expected the columns {COLUMNS}")
        rows = []
        for i, raw in enumerate(reader, start=2):
            r = {k: (v or "").strip() for k, v in raw.items()}
            if r["source_id"] == "":
                raise ValueError(f"{path}:{i}: source_id is required")
            if not r["normalized_uri"].startswith(NS_MR.namespace):
                raise ValueError(
                    f"{path}:{i}: normalized_uri must be a MinMod resource"
                )
            rows.append(
                CountryCorrection(
                    source_id=r["source_id"],
                    normalized_uri=r["normalized_uri"],
                    note=r["note"],
                )
            )
    return CountryCorrections(rows)
