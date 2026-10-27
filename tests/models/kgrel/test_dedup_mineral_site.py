from __future__ import annotations

from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kgrel.custom_types import Location, LocationView
from minmodkg.models.kgrel.dedup_mineral_site import DedupMineralSite
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory

MR = "https://minmod.isi.edu/resource/"
CRS_NAMES = {MR + "Q701": "EPSG:4326"}
SYSTEM = "https://minmod.isi.edu/users/s/test"
EXPERT = "https://minmod.isi.edu/users/u/curator"


def site(record_id, wkt, score=0.5, created_by=SYSTEM, modified_at=1):
    """A record of one source; on a score tie main keeps the highest record_id."""
    crs = CandidateEntity(source="test", confidence=1.0, normalized_uri=MR + "Q701")
    location = Location(crs=crs, coordinates=wkt)
    return MineralSiteAndInventory.from_dict(
        {
            "ms": {
                "site_id": f"site__{record_id}",
                "dedup_site_id": "dedup_site__test",
                "source_id": "https://example.org/source",
                "source_score": score,
                "record_id": record_id,
                "location": location.to_dict(),
                "location_view": LocationView.from_location(
                    location, CRS_NAMES
                ).to_dict(),
                "created_by": created_by,
                "modified_at": modified_at,
            }
        }
    )


def picked(*sites):
    return DedupMineralSite.from_sites(list(sites), crs_names=CRS_NAMES).dms.coordinates


def test_tie_switches_to_the_same_spot_with_more_digits():
    c = picked(site("b", "POINT(-110.9 30.0)"), site("a", "POINT(-110.9149 30.0142)"))
    assert c.refid == "site__a" and (c.value.lat, c.value.lon) == (30.0142, -110.9149)


def test_tie_keeps_the_pick_when_the_finer_point_is_elsewhere():
    # finer, but 0.06 deg west: outside the +-0.05 box of a 1-place value
    c = picked(site("b", "POINT(-110.9 30.0)"), site("a", "POINT(-110.96 30.0)"))
    assert c.refid == "site__b"


def test_a_higher_score_is_never_overridden():
    c = picked(
        site("b", "POINT(-110.9 30.0)", score=0.8),
        site("a", "POINT(-110.9149 30.0142)", score=0.5),
    )
    assert c.refid == "site__b"


def test_an_expert_or_curator_record_is_never_overridden():
    # both at 1.0; the newest expert edit wins as before, however coarse
    c = picked(
        site("b", "POINT(-110.9 30.0)", created_by=EXPERT, modified_at=2),
        site("a", "POINT(-110.9149 30.0142)", created_by=EXPERT, modified_at=1),
    )
    assert c.refid == "site__b"


def test_equal_precision_keeps_the_current_order():
    # 30.010 is two real places, like 30.01: trailing zeros are not precision
    c = picked(site("b", "POINT(-110.91 30.01)"), site("a", "POINT(-110.910 30.010)"))
    assert c.refid == "site__b"


def test_null_island_is_never_a_candidate():
    # POINT(0 0) has no lat/lon, so the pick is the next record, then refined
    c = picked(
        site("c", "POINT(0 0)"),
        site("b", "POINT(-110.9 30.0)"),
        site("a", "POINT(-110.9149 30.0142)"),
    )
    assert c.refid == "site__a"


def test_merging_partials_switches_to_the_same_spot_with_more_digits():
    # each record in its own partial, so only the merge across partials can switch
    coarse = site("b", "POINT(-110.9 30.0)")
    fine = site("a", "POINT(-110.9149 30.0142)")
    partials = [
        DedupMineralSite.from_sites([s], crs_names=CRS_NAMES).dms
        for s in (coarse, fine)
    ]
    assert [p.coordinates.refid for p in partials] == ["site__b", "site__a"]
    c = DedupMineralSite.from_dedup_sites(
        partials, [coarse, fine], is_site_ranked=True, crs_names=CRS_NAMES
    ).dms.coordinates
    assert c.refid == "site__a"
