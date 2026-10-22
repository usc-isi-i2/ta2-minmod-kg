from __future__ import annotations

import pytest
from minmodkg.misc.coord_precision import (
    KM_PER_DEGREE,
    SourceCoordinate,
    finest_refinement,
    real_decimals,
)


def coord(wkt: str, crs: str = "EPSG:4326", lat: float = 0.0, lon: float = 0.0):
    return SourceCoordinate("site", wkt, crs, lat, lon)


@pytest.mark.parametrize(
    "value, places",
    [
        ("40", 0),
        ("40.12345", 5),
        ("35.1000000000000000", 1),  # padded, as MRDS writes it
        ("28.1180500000000002", 5),  # padded with float noise
    ],
)
def test_real_decimals(value, places):
    assert real_decimals(value) == places


def test_dms_value_counts_at_its_grid_not_its_digits():
    # 38d55' S of the equator, 6d55' -- 13 places as written, good to an arcminute
    c = coord("POINT (-38.9166666666667 -6.91666666666667)")
    assert c.resolution_km() == pytest.approx(KM_PER_DEGREE / 60)
    # a plain 4-place value of the same spot is finer than the arcminute grid
    assert coord("POINT (-38.9167 -6.9167)").resolution_km() < c.resolution_km()


def test_float_noise_in_a_projected_crs_adds_no_precision():
    # Oregon Lambert (ft): digits past what a double holds are not precision
    plain = coord("POINT (741725.31 674269.18)", "EPSG:2994")
    noisy = coord("POINT (741725.31000000001 674269.18000000001)", "EPSG:2994")
    assert noisy.resolution_km() == plain.resolution_km()
    assert finest_refinement(plain, [noisy]) is None


def test_the_rounding_box_is_per_axis():
    # latitude 0 places, longitude 5: the box is +-0.5 deg tall, +-0.000005 wide
    picked = coord("POINT(-83.08333 48)")
    assert picked.contains(coord("POINT(-83.083331 47.75)"))
    assert not picked.contains(coord("POINT(-83.16667 47.75)"))


def test_finest_refinement_only_inside_the_box():
    picked = coord("POINT(-110.9 30.0)")
    inside = coord("POINT(-110.9149 30.0142)")
    outside = coord("POINT(-110.96 30.0)")  # finer, but 0.06 deg away
    assert finest_refinement(picked, [outside, inside]) is inside
    assert finest_refinement(picked, [outside]) is None
