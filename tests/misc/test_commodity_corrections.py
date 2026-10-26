from __future__ import annotations

import copy
import csv
import os
from pathlib import Path

import pytest
import serde.json
from minmodkg.etl.kgrel_entity import EntityDeserFn
from minmodkg.etl.mineral_site import MergeFn
from minmodkg.misc.commodity_corrections import (
    CommodityCorrection,
    CommodityCorrections,
)
from minmodkg.models.kg.mineral_site import MineralSiteIdent
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory
from statickg.models.file_and_path import BaseType, InputFile, RelPath

RESOURCE_DIR = Path(__file__).parent.parent / "resources"
# a small copy of a data repository's data/: the list, a few commodities and
# the sulfur mine
FIXTURE_DATA_DIR = RESOURCE_DIR / "commodity_corrections"
LIST = FIXTURE_DATA_DIR / "corrections/commodity.csv"

MRDS = "https://mrdata.usgs.gov/mrds"
OREGON = "http://www.oregongeology.org"
MR = "https://minmod.isi.edu/resource/"
V1 = "UMN Matching System v1"
V2 = "UMN Matching System-ProcMinev2"


def cand(observed: str, qid: str | None, confidence=1.0, source=V1) -> dict:
    c = {"source": source, "confidence": confidence, "observed_name": observed}
    if qid is not None:
        c["normalized_uri"] = MR + qid
    return c


def site(source_id: str, record_id: str, *commodities: dict) -> dict:
    return {
        "source_id": source_id,
        "record_id": record_id,
        "created_by": "https://minmod.isi.edu/users/s/umn",
        "modified_at": "2025-07-03T20:08:30Z",
        "mineral_inventory": [
            {"reference": {"document": {"uri": source_id}}, "commodity": c}
            for c in commodities
        ],
    }


def observed(s: dict) -> list[tuple]:
    return [
        (
            inv["commodity"]["observed_name"],
            inv["commodity"].get("normalized_uri", MR)[len(MR) :] or None,
            inv["commodity"]["confidence"],
            inv["commodity"]["source"],
        )
        for inv in s["mineral_inventory"]
    ]


@pytest.fixture(scope="module")
def corrections() -> CommodityCorrections:
    return CommodityCorrections.from_file(LIST)


def test_drop_removes_the_inventory(corrections):
    s = site(
        MRDS,
        "10004382",
        cand("Sand And Gravel", "Q656", 0.9999, V2),
        cand("Construction", "Q609", 0.5453, V2),
    )
    applied = corrections.apply(s)
    assert [(r.action, inv["commodity"]["observed_name"]) for r, inv in applied] == [
        ("drop", "Construction")
    ]
    assert observed(s) == [("Sand And Gravel", "Q656", 0.9999, V2)]


def test_remap_keeps_the_observed_name_and_the_matcher(corrections):
    s = site(MRDS, "10008252", cand("Titanium", "Q614", 0.9999, V2))
    s["mineral_inventory"].append(
        {
            "reference": {"document": {"uri": MRDS}},
            "commodity": cand("Metal", "Q581", 0.7599, V2),
            "grade": {"value": 15.0},
        }
    )
    applied = corrections.apply(s)
    assert [(r.action, inv["commodity"]["normalized_uri"]) for r, inv in applied] == [
        ("remap", MR + "Q581")
    ]
    assert observed(s) == [
        ("Titanium", "Q614", 0.9999, V2),
        ("Metal", "Q628", 1.0, V2),
    ]
    # only the candidate changes
    assert s["mineral_inventory"][1]["grade"] == {"value": 15.0}


def test_remap_with_a_context(corrections):
    s = site(OREGON, "ID_05048", cand("stone", "Q663"), cand("crushed", None, 0.001))
    s["mineral_inventory"].append(
        {"reference": {"document": {"uri": OREGON}}, "commodity": cand("SC", "Q607")}
    )
    assert len(corrections.apply(s)) == 1
    assert observed(s) == [
        ("stone", "Q663", 1.0, V1),
        ("crushed", None, 0.001, V1),
        ("SC", "Q664", 1.0, V1),
    ]


def test_drop_with_a_context(corrections):
    s = site(
        OREGON,
        "ID_06010",
        cand("sand and gravel", "Q656"),
        cand("S", "Q633"),
        cand("G", None, 0.001),
    )
    assert len(corrections.apply(s)) == 1
    assert observed(s) == [
        ("sand and gravel", "Q656", 1.0, V1),
        ("G", None, 0.001, V1),
    ]


@pytest.mark.parametrize(
    "s",
    [
        # the same string from another source, left unmatched there
        site(
            "https://doi.org/10.5066/P9HTERGK",
            "1",
            cand("Construction", None, 0.001),
        ),
        # an Oregon code from another source
        site(MRDS, "2", cand("SC", "Q607")),
        # exact only: no case folding
        site(MRDS, "3", cand("construction", "Q609", 0.5, V2)),
        # a listed string another extractor left unmatched
        site(MRDS, "6", cand("Construction", None, 1.0, "SRI database agent v0")),
        # already what the remap would make it
        site(MRDS, "7", cand("Metal", "Q628", 1.0)),
        site(MRDS, "4"),
        {"source_id": MRDS, "record_id": "5", "created_by": "x", "modified_at": "y"},
    ],
)
def test_non_matching_records_are_untouched(corrections, s):
    before = copy.deepcopy(s)
    assert corrections.apply(s) == []
    assert s == before


@pytest.mark.parametrize(
    "s",
    [
        # S and G at a crushed-stone record: undecoded, left as it is
        site(
            OREGON,
            "ID_1",
            cand("stone", "Q663"),
            cand("crushed", None, 0.001),
            cand("S", "Q633"),
            cand("G", None, 0.001),
        ),
        site(OREGON, "ID_2", cand("sand and gravel", "Q656"), cand("SC", "Q607")),
    ],
)
def test_the_context_must_match(corrections, s):
    before = copy.deepcopy(s)
    assert corrections.apply(s) == []
    assert s == before


def test_the_sulfur_mine_is_untouched(corrections):
    (mine,) = serde.json.deser(
        FIXTURE_DATA_DIR
        / "mineral-sites/umn/oregon_department_geology_mineral_industries/b043.json"
    )
    assert mine["name"] == "Last Chance Sulfur Mine"
    before = copy.deepcopy(mine)
    assert corrections.apply(mine) == []
    assert mine == before


def test_a_matching_context_wins_over_an_empty_one():
    row = dict(source_id=OREGON, observed_name="S", normalized_uri=None, note="")
    corrections = CommodityCorrections(
        [
            CommodityCorrection(context="", action="drop", **row),
            CommodityCorrection(
                **{**row, "normalized_uri": MR + "Q655"},
                context="sand and gravel | S",
                action="remap",
            ),
        ]
    )
    s = site(OREGON, "1", cand("sand and gravel", "Q656"), cand("S", "Q633"))
    assert [r.action for r, _ in corrections.apply(s)] == ["remap"]
    s = site(OREGON, "2", cand("S", "Q633"))
    assert [r.action for r, _ in corrections.apply(s)] == ["drop"]


@pytest.mark.parametrize(
    "rows",
    [
        "a,Construction,,drop,,\na,Construction,,drop,,\n",
        "a,Construction,,delete,,\n",
        "a,Metal,,remap,,\n",
        "a,Construction,,drop,https://minmod.isi.edu/resource/Q656,\n",
        ",Construction,,drop,,\n",
    ],
)
def test_a_bad_list_is_refused(tmp_path: Path, rows: str):
    path = tmp_path / "commodity.csv"
    path.write_text(
        "source_id,observed_name,context,action,normalized_uri,note\n" + rows
    )
    with pytest.raises(ValueError):
        CommodityCorrections.from_file(path)


def test_a_missing_or_empty_list_is_empty(tmp_path: Path):
    assert CommodityCorrections.from_file(tmp_path / "missing.csv").rows == []
    assert CommodityCorrections.from_file(None).rows == []
    path = tmp_path / "commodity.csv"
    path.write_text("source_id,observed_name,context,action,normalized_uri,note\n")
    assert CommodityCorrections.from_file(path).rows == []


# ------------------------------------------------- through the merge (MergeFn)

SITES = [
    site(
        MRDS,
        "10115528",
        cand("Sand And Gravel", "Q656", 0.9999, V2),
        cand("Construction", "Q609", 0.5453, V2),
        cand("Strontium", "Q609", 0.9999, V2),
    ),
    site(OREGON, "ID_05048", cand("stone", "Q663"), cand("crushed", None, 0.001)),
    site(OREGON, "ID_04514", cand("sulfur and pyrite", None, 0.001), cand("S", "Q633")),
]
SITES[1]["mineral_inventory"].append(
    {"reference": {"document": {"uri": OREGON}}, "commodity": cand("SC", "Q607")}
)


@pytest.fixture(scope="module")
def merge_inputs(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("merge")
    entity_dir = root / "entities"
    entity_dir.mkdir()
    # state_or_province: the merge-time state repair reads it
    for name in ("commodity_form", "crs", "data_source", "state_or_province"):
        records = EntityDeserFn.read_file(RESOURCE_DIR / f"kgdata/entities/{name}.csv")
        serde.json.ser(
            {records[0].__class__.__name__: [r.to_dict() for r in records]},
            entity_dir / f"{name}.json",
        )

    relpath = "data/mineral-sites/umn/test/b000.json"
    (root / relpath).parent.mkdir(parents=True)
    serde.json.ser(SITES, root / relpath)
    sameas = root / "sameas.json"
    serde.json.ser(
        [
            {
                "site_id": (sid := MineralSiteIdent.from_dict(s).id),
                "dedup_id": "d_" + sid,
            }
            for s in SITES
        ],
        sameas,
    )
    empty = root / "empty.csv"
    empty.write_text("source_id,observed_name,context,action,normalized_uri,note\n")
    listfile = root / "commodity.csv"
    listfile.write_bytes(LIST.read_bytes())

    def infile(path: Path) -> InputFile:
        return InputFile.from_relpath(
            RelPath(BaseType.REPO, root, str(path.relative_to(root)))
        )

    return {
        "root": root,
        "entity_dir": entity_dir,
        "infiles": [infile(root / relpath)],
        "sameas_file": infile(sameas),
        "empty": infile(empty),
        "list": infile(listfile),
    }


def merge(inputs: dict, name: str, corrections_files: list[InputFile]) -> Path:
    workdir = inputs["root"] / f"work-{name}"
    workdir.mkdir()
    return MergeFn(workdir, inputs["entity_dir"]).invoke(
        infiles=inputs["infiles"],
        sameas_file=inputs["sameas_file"],
        outfile=inputs["root"] / f"out-{name}.json",
        corrections_files=corrections_files,
    )


def test_an_empty_list_changes_nothing_byte_for_byte(merge_inputs):
    none = merge(merge_inputs, "none", []).read_bytes()
    empty = merge(merge_inputs, "empty", [merge_inputs["empty"]]).read_bytes()
    assert none == empty


def test_the_merge_corrects_the_stored_candidate_and_the_views(merge_inputs):
    out = serde.json.deser(merge(merge_inputs, "list", [merge_inputs["list"]]))
    msis = [
        MineralSiteAndInventory.from_dict(x) for x in out["MineralSiteAndInventory"]
    ]
    by_record = {m.ms.record_id: m for m in msis}

    mrds = by_record["10115528"]
    # the stored inventories, which the KG export writes out, ...
    assert [i.commodity.observed_name for i in mrds.ms.inventories] == [
        "Sand And Gravel",
        "Strontium",
    ]
    assert [i.commodity.observed_name for i in mrds.ms.to_kg().mineral_inventory] == [
        "Sand And Gravel",
        "Strontium",
    ]
    # ... and the views; the record's own Strontium stays
    assert sorted(v.commodity for v in mrds.invs) == ["Q609", "Q656"]

    oregon = by_record["ID_05048"]
    assert oregon.ms.inventories[2].commodity.normalized_uri == MR + "Q664"
    assert oregon.ms.inventories[2].commodity.confidence == 1.0
    assert sorted(v.commodity for v in oregon.invs) == ["Q663", "Q664"]

    mine = by_record["ID_04514"]
    assert [v.commodity for v in mine.invs] == ["Q633"]


# ------------------------------------------------- data checks
# Run against a ta2-minmod-data checkout with MINMOD_DATA_DIR=<checkout>/data;
# without it, against the small fixture.
DATA_DIR = Path(os.environ.get("MINMOD_DATA_DIR", FIXTURE_DATA_DIR))


def test_every_remap_target_is_a_commodity():
    with open(DATA_DIR / "entities/commodity.csv", encoding="utf-8-sig") as f:
        qids = {r["minmod_id"] for r in csv.DictReader(f)}
    rows = CommodityCorrections.from_file(DATA_DIR / "corrections/commodity.csv").rows
    assert len(rows) > 0
    for row in rows:
        if row.action == "remap":
            assert row.normalized_uri is not None
            assert row.normalized_uri.startswith(MR), row
            assert row.normalized_uri[len(MR) :] in qids, row


def test_no_row_matches_the_last_chance_sulfur_mine():
    corrections = CommodityCorrections.from_file(DATA_DIR / "corrections/commodity.csv")
    mines = [
        s
        for f in sorted(
            (
                DATA_DIR
                / "mineral-sites/umn/oregon_department_geology_mineral_industries"
            ).glob("*.json")
        )
        for s in serde.json.deser(f)
        if s["source_id"] == OREGON and s["record_id"] == "ID_04514"
    ]
    assert [s["name"] for s in mines] == ["Last Chance Sulfur Mine"]
    before = copy.deepcopy(mines[0])
    assert corrections.apply(mines[0]) == []
    assert mines[0] == before
