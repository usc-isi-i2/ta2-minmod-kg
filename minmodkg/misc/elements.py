from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def element_index() -> dict[str, str]:
    """Lowercased symbol, name or alias -> symbol."""
    index = {}
    with open(Path(__file__).parent / "elements.csv", newline="") as f:
        for row in csv.DictReader(f):
            keys = [row["symbol"], row["name"], *row["aliases"].split("|")]
            for key in keys:
                if key:
                    index[key.lower()] = row["symbol"]
    return index


def element_symbol(text: str) -> Optional[str]:
    """The symbol for an element symbol or name, any case; None if unknown."""
    return element_index().get(text.strip().lower())
