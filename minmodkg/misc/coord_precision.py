"""How precise a source coordinate really is, and whether another one refines it.

Decimal places as written are not precision:

- MRDS pads every value to 16 places (`28.1180500000000002` is good to 5);
- converted values are printed as full floats (`868492.6492782086` in Oregon's
  EPSG:2994 feet), so their places track the size of the number;
- degree-minute-second conversions look like 7-13 places (`-38.9166666666667`)
  but are only good to a minute or a second of arc.

So a value counts by its last real decimal place, with trailing zeros and float
noise dropped, or by its DMS grid, in its CRS's own unit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Sequence

from pyproj import CRS

KM_PER_DEGREE = 111.32
_NUMBER = re.compile(r"[-+]?(\d*)(?:\.(\d*))?(?:[eE]([-+]?\d+))?")


def decimals(s: str) -> int:
    """Decimal places as written: '45.10' -> 2, '1.5e-3' -> 4."""
    m = _NUMBER.fullmatch(s)
    return max(len(m.group(2) or "") - int(m.group(3) or 0), 0) if m else 0


def real_decimals(s: str) -> int:
    """Decimal places once trailing zeros and float noise are dropped:
    '35.1000000000000000' -> 1, '28.1180500000000002' -> 5."""
    return min(decimals(s), decimals(repr(float(s))))


def xy_numbers(wkt: str) -> list[tuple[str, str]]:
    """The x and y of every coordinate tuple in a WKT, as written (Z/M dropped)."""
    wkt = re.sub(r"^\s*SRID=\d+;", "", wkt)
    out = []
    for part in re.split(r"[(),]", wkt):
        toks = part.split()
        if len(toks) >= 2 and all(
            _NUMBER.fullmatch(t) and any(c.isdigit() for c in t) for t in toks
        ):
            out.append((toks[0], toks[1]))
    return out


def _on_grid(s: str, per_degree: int) -> bool:
    """A whole number of arcminutes (60) or arcseconds (3600), within what the
    value's own digits can carry."""
    x = abs(float(s)) * per_degree
    return abs(x - round(x)) <= per_degree * 0.5 * 10 ** -real_decimals(s) + 1e-9


def dms_grid(values: Sequence[str]) -> Optional[int]:
    """60 or 3600 if the values look converted from degrees-minutes-seconds:
    each is a long decimal (>= 6 real places) on the arcsecond grid or a short
    one (<= 4) that is a whole arcminute, and at least one is long. At 6 places
    a random value lands on the grid with p = 0.0036 per axis."""
    kinds = [
        (
            "long"
            if real_decimals(s) >= 6 and _on_grid(s, 3600)
            else "short" if real_decimals(s) <= 4 and _on_grid(s, 60) else None
        )
        for s in values
    ]
    if "long" not in kinds or not all(kinds):
        return None
    return 60 if all(_on_grid(s, 60) for s in values) else 3600


@lru_cache
def _crs(name: str) -> CRS:
    return CRS.from_user_input(name)


def _km_per_unit(crs: str) -> float:
    c = _crs(crs)
    if c.is_geographic:
        return KM_PER_DEGREE
    return c.axis_info[0].unit_conversion_factor / 1000


@dataclass(frozen=True)
class SourceCoordinate:
    """A coordinate as the source wrote it (WKT in its own CRS, "EPSG:<code>"),
    with its WGS84 location view; `id` is the record it belongs to."""

    id: str
    wkt: str
    crs: str
    lat: float
    lon: float

    def _grid(self) -> Optional[int]:
        if not _crs(self.crs).is_geographic:
            return None
        return dms_grid([v for xy in xy_numbers(self.wkt) for v in xy])

    def resolution_km(self) -> Optional[float]:
        values = [v for xy in xy_numbers(self.wkt) for v in xy]
        if not values:
            return None
        grid = self._grid()
        if grid is not None:
            return KM_PER_DEGREE / grid
        return 10 ** -min(map(real_decimals, values)) * _km_per_unit(self.crs)

    def contains(self, other: SourceCoordinate) -> bool:
        """`other` lies inside this coordinate's rounding box: half a unit of
        its last real digit either side, each axis its own (DMS: half its grid).
        Same CRS and both POINTs: the raw numbers are compared; otherwise the
        WGS84 views, with the box converted to degrees."""
        grid = self._grid()
        if grid is not None:
            hx = hy = 0.5 / grid
        else:
            xy = xy_numbers(self.wkt)
            hx, hy = (0.5 * 10 ** -min(real_decimals(p[i]) for p in xy) for i in (0, 1))
        hx, hy = hx + 1e-9, hy + 1e-9
        if (
            self.crs == other.crs
            and _geometry_type(self.wkt) == _geometry_type(other.wkt) == "POINT"
        ):
            (px, py), (ox, oy) = (
                tuple(map(float, xy_numbers(c.wkt)[0])) for c in (self, other)
            )
            return abs(px - ox) <= hx and abs(py - oy) <= hy
        if not _crs(self.crs).is_geographic:
            k = _km_per_unit(self.crs) / KM_PER_DEGREE
            hy *= k
            hx *= k / max(math.cos(math.radians(self.lat)), 1e-6)
        return abs(self.lat - other.lat) <= hy and abs(self.lon - other.lon) <= hx


def _geometry_type(wkt: str) -> str:
    m = re.match(r"\s*(?:SRID=\d+;)?\s*([A-Za-z]+)", wkt)
    return m.group(1).upper() if m else ""


def finest_refinement(
    picked: SourceCoordinate, candidates: Sequence[SourceCoordinate]
) -> Optional[SourceCoordinate]:
    """The candidate that is strictly more precise than `picked` and lies inside
    its rounding box (the same spot, written with more real digits); the finest
    if several, the first of equals. None if there is none."""
    resolution = picked.resolution_km()
    if resolution is None:
        return None
    finer = [
        (r, c)
        for c in candidates
        if (r := c.resolution_km()) is not None
        and r < resolution
        and picked.contains(c)
    ]
    return min(finer, key=lambda rc: rc[0])[1] if finer else None
