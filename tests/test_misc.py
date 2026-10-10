from __future__ import annotations

import pytest
import shapely.wkt
from minmodkg.misc import LongestPrefixIndex, reproject_wkt
from minmodkg.misc.geo import reproject_geometry
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kgrel.custom_types import Location, LocationView
from shapely.geometry import Point


def test_longest_prefix_index():
    index = LongestPrefixIndex.create(
        [
            "article::",
            "article::http://example.com/",
            "article::http://example.com/1",
            "databases::http://usgs.gov/",
        ]
    )
    assert (
        index.get("article::http://example.com/10") == "article::http://example.com/1"
    )

    assert index.get("article::http://example.com/2") == "article::http://example.com/"
    assert index.get("article::http://abc.com") == "article::"
    assert index.get("databases::http://usgs.gov/1") == "databases::http://usgs.gov/"
    assert index.get("databases::http://mrdata") is None
    assert index.get("mining-report::") is None


# Musick mine, Bohemia district, Oregon (DOGAMI record ID_17360), as stored in
# NAD83(CORS96) / Oregon Lambert (ft). Oregon spans lon -124.71..-116.45 and
# lat 41.98..46.30.
OREGON_LAMBERT_FT = "POINT (741725.31 674269.18)"


def test_reproject_to_wgs84_returns_lon_lat():
    # WKT is (x=lon, y=lat) on both sides; EPSG:4326's own axis order is
    # (lat, lon), which used to come back swapped.
    out = shapely.wkt.loads(reproject_wkt(OREGON_LAMBERT_FT, "EPSG:2994", "EPSG:4326"))
    assert -124.71 <= out.x <= -116.45 and 41.98 <= out.y <= 46.30

    out = reproject_geometry(
        shapely.wkt.loads(OREGON_LAMBERT_FT), "EPSG:2994", "EPSG:4326"
    )
    assert -124.71 <= out.x <= -116.45 and 41.98 <= out.y <= 46.30


def test_reproject_same_crs_returns_input_unchanged():
    wkt = "POINT (-122.6539 43.5792)"
    assert reproject_wkt(wkt, "EPSG:4326", "EPSG:4326") == wkt

    point = Point(-122.6539, 43.5792)
    assert reproject_geometry(point, "EPSG:4326", "EPSG:4326") is point


MR = "https://minmod.isi.edu/resource/"
UTM_12N = MR + "Q704"  # EPSG:26912


def location_view(wkt: str, crs: str | None = None) -> LocationView:
    def ent(uri):
        return CandidateEntity(source="test", confidence=1.0, normalized_uri=uri)

    location = Location(
        country=[ent(MR + "Q1140")],  # Mexico
        state_or_province=[ent(MR + "Q4555")],  # Sonora
        crs=ent(crs) if crs else None,
        coordinates=wkt,
    )
    return LocationView.from_location(location, {UTM_12N: "EPSG:26912"})


@pytest.mark.parametrize(
    "wkt, crs",
    [
        ("POINT(0 0)", None),
        ("POINT (0.0 0.0)", None),
        ("POINT(-0.0 0.0)", None),
        ("MULTIPOINT((0 0), (0 0))", None),
        # (0, 0) in the source's own CRS, before reprojection
        ("POINT (0 0)", UTM_12N),
    ],
)
def test_null_island_is_no_coordinate(wkt, crs):
    view = location_view(wkt, crs)
    assert (view.lat, view.lon) == (None, None)
    assert view.country == ["Q1140"] and view.state_or_province == ["Q4555"]


def test_null_island_is_dropped_from_a_multipoint():
    view = location_view("MULTIPOINT((0 0), (10 20))")
    assert (view.lat, view.lon) == (20, 10)


@pytest.mark.parametrize(
    "wkt, lat, lon",
    [
        ("POINT(0 10)", 10, 0),
        ("POINT(10 0)", 0, 10),
        ("POINT(-109.103889 29.774722)", 29.774722, -109.103889),
    ],
)
def test_real_coordinates_are_untouched(wkt, lat, lon):
    view = location_view(wkt)
    assert (view.lat, view.lon) == (lat, lon)
    assert view.country == ["Q1140"] and view.state_or_province == ["Q4555"]


def test_real_non_wgs84_point_still_reprojects():
    # OFR-757 record in UTM zone 12N. Utah spans lon -114.05..-109.04 and
    # lat 37.00..42.00.
    view = location_view("POINT (632887 4199554)", UTM_12N)
    assert -114.05 <= view.lon <= -109.04 and 37.00 <= view.lat <= 42.00
