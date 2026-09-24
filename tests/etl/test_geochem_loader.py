from __future__ import annotations

from minmodkg.etl.geochem_loader import (
    USER_URI,
    EntityResolver,
    build_paper,
    deposit_record_ids,
    sample_key,
)
from minmodkg.transformations import make_sample_id, make_site_id

BASE = "https://geochemistry.isi.edu/resource/site__10-1000-x__"


def paper(deposits: list[dict]) -> dict:
    return {"paper_doi": "10.1000/x", "paper_title": "T", "deposits": deposits}


def test_deposit_record_ids_follow_hmi():
    deposits = [
        {"@id": BASE + "suttsu", "name": "Suttsu"},
        {"@id": BASE + "suttsu", "name": "Suttsu again"},
        {"name": "Named only"},
        {},
    ]
    assert deposit_record_ids(deposits) == [
        "suttsu",
        "suttsu-2",
        "Named only",
        "deposit-4",
    ]


def test_sample_key_is_id_tail():
    assert sample_key({"@id": BASE + "a__sample__66__pyrite"}) == "66__pyrite"
    assert sample_key({"sample_id": "S-1"}) == "S-1"


def test_build_paper():
    resolver = EntityResolver(
        countries={"jpn": "https://minmod.isi.edu/resource/Q1109"},
        units={"ppm": "https://minmod.isi.edu/resource/Q220"},
    )
    sample = {
        "@id": BASE + "suttsu__sample__66__pyrite",
        "sample_id": "66",
        "latitude": "42.5",
        "longitude": "140.1",
        "analyses": [
            {
                "analysis_id": "a1",
                "instrument_type_model": "EPMA",
                "element": [
                    {
                        "rdfs:label": "Bi",
                        "grade": "0.04",
                        "grade_unit": {"rdfs:label": "ppm"},
                    },
                    {"rdfs:label": "Ag", "grade": None, "grade_unit": {}},
                ],
            }
        ],
    }
    deposit = {
        "@id": BASE + "suttsu",
        "name": "Suttsu",
        "country": {"observed_name": "JPN", "confidence": "0.95", "source": "x"},
        "samples": [sample, {**sample, "analyses": [{"analysis_id": "a2"}]}],
    }
    load = build_paper(paper([deposit]), resolver, {})

    site = load.sites[0]
    assert site.id == make_site_id("geochem-hmi", "https://doi.org/10.1000/x", "suttsu")
    assert site.created_by == USER_URI
    assert site.location_info is not None
    assert site.location_info.country[0].normalized_uri.endswith("Q1109")

    assert len(load.samples) == 1 and len(load.merged_samples) == 1
    s = load.samples[0]
    assert s.id == make_sample_id(site.id, "66__pyrite")
    assert s.location_info is not None
    assert s.location_info.location == "POINT (140.1 42.5)"
    assert [a.analysis_id for a in s.analyses] == ["a1", "a2"]
    elements = s.analyses[0].elements
    assert s.analyses[0].instrument_type == "EPMA"
    assert elements[0].grade == 0.04
    assert elements[0].grade_unit is not None
    assert elements[0].grade_unit.normalized_uri.endswith("Q220")
    assert elements[1].grade is None and elements[1].grade_unit is None


def test_edits_override_jsonld():
    resolver = EntityResolver()
    deposit = {
        "@id": BASE + "suttsu",
        "samples": [{"@id": BASE + "suttsu__sample__s1", "analyses": []}],
    }
    site_id = build_paper(paper([deposit]), resolver, {}).sites[0].id
    public_id = make_sample_id(site_id, "s1")
    edits = {
        site_id: {
            public_id: {
                "id": public_id,
                "sample_id": "s1",
                "mineral_site_id": site_id,
                "sample_name": "edited",
            }
        }
    }
    load = build_paper(paper([deposit]), resolver, edits)
    assert [s.sample_name for s in load.samples] == ["edited"]
