from __future__ import annotations

import copy
from dataclasses import replace

from minmodkg.etl.geochem_jsonld import (
    USER_URI,
    EntityResolver,
    apply_sample,
    apply_site,
    deposit_record_ids,
    read_paper,
    sample_key,
)
from minmodkg.transformations import make_sample_id, make_site_id

RES = "https://geochemistry.isi.edu/resource/"
SITE = RES + "site__10-1000-x__"
SOURCE_ID = "https://doi.org/10.1000/x"
PPM = "https://minmod.isi.edu/resource/Q220"


def element(label: str, grade: str | None) -> dict:
    return {
        "@id": f"{SITE}suttsu__sample__66__pyrite__analysis__a1__element__{label}",
        "@type": "geochem:Element",
        "rdfs:label": label,
        "grade": grade,
        "grade_unit": {
            "@id": RES + "unit__ppm",
            "@type": "mo:UnitCandidate",
            "rdfs:label": "ppm",
        },
    }


def make_paper() -> dict:
    sample = {
        "@id": SITE + "suttsu__sample__66__pyrite",
        "sample_id": "66",
        "sample_name": {"value": "pyrite 66", "evidence_quote": "py-66"},
        "latitude": "42.5",
        "longitude": "140.1",
        "analyses": [
            {
                "@id": SITE + "suttsu__sample__66__pyrite__analysis__a1",
                "analysis_id": "a1",
                "instrument_type_model": "EPMA",
                "element": [element("Bi", "0.04"), element("Ag", None)],
            }
        ],
    }
    split = {
        "@id": SITE + "suttsu__sample__66__pyrite",
        "sample_id": "66",
        "analyses": [
            {
                "@id": SITE + "suttsu__sample__66__pyrite__analysis__a2",
                "analysis_id": "a2",
                "element": [],
            }
        ],
    }
    return {
        "paper_id": "2004_X",
        "paper_doi": "10.1000/x",
        "paper_title": "T",
        "deposits": [
            {
                "@id": SITE + "suttsu",
                "name": "Suttsu",
                "country": {
                    "observed_name": "JPN",
                    "confidence": "0.95",
                    "source": "x",
                },
                "commodity": [
                    {"@id": SITE + "suttsu__commodity__ag", "observed_name": "Ag"}
                ],
                "samples": [sample, split],
            },
            {"@id": SITE + "suttsu", "name": "Suttsu again", "samples": []},
        ],
    }


def resolver() -> EntityResolver:
    return EntityResolver(
        countries={"jpn": "https://minmod.isi.edu/resource/Q1109"}, units={"ppm": PPM}
    )


def test_deposit_record_ids_follow_hmi():
    deposits = [
        {"@id": SITE + "suttsu"},
        {"@id": SITE + "suttsu"},
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
    assert sample_key({"@id": SITE + "a__sample__66__pyrite"}) == "66__pyrite"
    assert sample_key({"sample_id": "S-1"}) == "S-1"


def test_read_paper():
    content = read_paper(make_paper(), resolver())

    site = content.sites[0]
    assert site.id == make_site_id("geochem-hmi", SOURCE_ID, "suttsu")
    assert content.sites[1].record_id == "suttsu-2"
    assert site.created_by == USER_URI
    assert site.location_info is not None
    assert site.location_info.country[0].normalized_uri.endswith("Q1109")

    # the split node's analyses are merged into one sample
    assert len(content.samples) == 1 and len(content.merged_samples) == 1
    s = content.samples[0]
    assert s.id == make_sample_id(site.id, "66__pyrite")
    assert s.sample_name == "pyrite 66"
    assert s.location_info is not None
    assert s.location_info.location == "POINT (140.1 42.5)"
    assert [a.analysis_id for a in s.analyses] == ["a1", "a2"]
    assert s.analyses[0].instrument_type == "EPMA"
    bi, ag = s.analyses[0].elements
    assert bi.grade == 0.04 and bi.grade_unit is not None
    assert bi.grade_unit.normalized_uri == PPM
    assert ag.grade is None


def test_write_back_unchanged_is_identity():
    paper = make_paper()
    out = copy.deepcopy(paper)
    content = read_paper(paper, resolver())
    for site in content.sites:
        apply_site(out, site)
    for sample in content.samples:
        apply_sample(out, sample)
    assert out["deposits"][0]["samples"] == paper["deposits"][0]["samples"]


def test_sample_edits_survive_reload():
    paper = make_paper()
    s = read_paper(paper, resolver()).samples[0]
    s.sample_name = "edited"
    s.analyses[0].elements[0].grade = 1.5
    s.analyses[0].elements[1].is_deleted = True
    s.analyses[0].elements[1].deleted_by = "https://minmod.isi.edu/users/u/x"
    new = copy.deepcopy(s.analyses[0])
    new.analysis_id = "a3"
    s.analyses.append(new)
    apply_sample(paper, s)

    reloaded = read_paper(paper, resolver()).samples[0]
    assert reloaded.to_dict() == s.to_dict()
    # untouched keys and ids stay as they were
    first, last = paper["deposits"][0]["samples"]
    assert first["analyses"][0]["element"][0]["grade_unit"]["@id"] == RES + "unit__ppm"
    assert last["analyses"][-1]["@id"].endswith("__sample__66__pyrite__analysis__a3")


def test_new_sample_and_site_round_trip():
    paper = make_paper()
    content = read_paper(paper, resolver())
    site = replace(
        content.sites[0], record_id="new-deposit", name="New", is_deleted=True
    )
    apply_site(paper, site)
    sample = replace(content.samples[0], mineral_site_id=site.id, sample_id="fresh")
    apply_sample(paper, sample)

    reloaded = read_paper(paper, resolver())
    new_site = next(s for s in reloaded.sites if s.id == site.id)
    assert (new_site.name, new_site.is_deleted) == ("New", True)
    fresh = next(s for s in reloaded.samples if s.mineral_site_id == site.id)
    assert fresh.id == make_sample_id(site.id, "fresh")
    assert [a.analysis_id for a in fresh.analyses] == ["a1", "a2"]
