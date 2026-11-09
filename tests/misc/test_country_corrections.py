from __future__ import annotations

import copy
import csv
import os
from pathlib import Path

import pytest
import serde.json
from minmodkg.etl.kgrel_entity import EntityDeserFn
from minmodkg.etl.mineral_site import MergeFn
from minmodkg.misc.country_corrections import SOURCE, CountryCorrections
from minmodkg.models.kg.mineral_site import MineralSiteIdent
from minmodkg.models.kgrel.dedup_mineral_site import DedupMineralSite
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory
from statickg.models.file_and_path import BaseType, InputFile, RelPath

RESOURCE_DIR = Path(__file__).parent.parent / "resources"
# a small copy of a data repository's data/: the list
FIXTURE_DATA_DIR = RESOURCE_DIR / "country_corrections"
LIST = FIXTURE_DATA_DIR / "corrections/country.csv"

ASBESTOS = "https://mrdata.usgs.gov/asbestos"
MRDS = "https://mrdata.usgs.gov/mrds"
MR = "https://minmod.isi.edu/resource/"
V1 = "UMN Matching System v1"
US = MR + "Q1235"


def cand(observed: str | None, qid: str) -> dict:
    c = {"source": V1, "confidence": 1.0, "normalized_uri": MR + qid}
    if observed is not None:
        c["observed_name"] = observed
    return c


def site(
    source_id: str,
    record_id: str,
    state: dict | None = None,
    country: dict | None = None,
    creator: str = "umn",
) -> dict:
    s: dict = {
        "source_id": source_id,
        "record_id": record_id,
        "name": f"site {record_id}",
        "created_by": f"https://minmod.isi.edu/users/s/{creator}",
        "modified_at": "2024-09-20T00:00:00Z",
    }
    if state is not None or country is not None:
        s["location_info"] = {}
        if country is not None:
            s["location_info"]["country"] = [country]
        if state is not None:
            s["location_info"]["state_or_province"] = [state]
        s["location_info"]["crs"] = cand("EPSG:4326", "Q701")
        s["location_info"]["location"] = "POINT (-119.1529 37.2956)"
    return s


@pytest.fixture(scope="module")
def corrections() -> CountryCorrections:
    return CountryCorrections.from_file(LIST)


def test_a_record_without_a_country_gets_its_sources(corrections):
    s = site(ASBESTOS, "1010", state=cand("CA", "Q6915"))
    row = corrections.apply(s)
    assert row is not None and row.normalized_uri == US
    assert s["location_info"]["country"] == [
        {"source": SOURCE, "confidence": 1.0, "normalized_uri": US}
    ]
    # first, and the rest kept as it was
    assert list(s["location_info"]) == [
        "country",
        "state_or_province",
        "crs",
        "location",
    ]
    assert s["location_info"]["state_or_province"] == [cand("CA", "Q6915")]


def test_a_record_without_location_info_gets_one(corrections):
    s = site(ASBESTOS, "20", creator="sri")
    assert corrections.apply(s) is not None
    assert s["location_info"] == {
        "country": [{"source": SOURCE, "confidence": 1.0, "normalized_uri": US}]
    }


@pytest.mark.parametrize(
    "s",
    [
        # already has a country, even another one
        site(ASBESTOS, "1", state=cand("CA", "Q6915"), country=cand("Canada", "Q1038")),
        # a source the list does not name
        site(MRDS, "10005884", state=cand("Potosi", "Q5254")),
    ],
)
def test_other_records_are_untouched(corrections, s):
    before = copy.deepcopy(s)
    assert corrections.apply(s) is None
    assert s == before


@pytest.mark.parametrize(
    "rows",
    [
        "source_id,normalized_uri\n",
        "source_id,normalized_uri,note\n,https://minmod.isi.edu/resource/Q1235,\n",
        "source_id,normalized_uri,note\nhttps://mrdata.usgs.gov/asbestos,,\n",
        "source_id,normalized_uri,note\nhttps://mrdata.usgs.gov/asbestos,Q1235,\n",
        "source_id,normalized_uri,note\n"
        "https://mrdata.usgs.gov/asbestos,https://minmod.isi.edu/resource/Q1235,\n"
        "https://mrdata.usgs.gov/asbestos,https://minmod.isi.edu/resource/Q1038,\n",
    ],
)
def test_a_bad_list_is_refused(tmp_path: Path, rows: str):
    path = tmp_path / "country.csv"
    path.write_text(rows)
    with pytest.raises(ValueError):
        CountryCorrections.from_file(path)


def test_a_missing_or_empty_list_is_empty(tmp_path: Path):
    assert CountryCorrections.from_file(tmp_path / "missing.csv").rows == []
    path = tmp_path / "country.csv"
    path.write_text("source_id,normalized_uri,note\n")
    empty = CountryCorrections.from_file(path)
    s = site(ASBESTOS, "1010", state=cand("CA", "Q6915"))
    assert empty.apply(s) is None


# ------------------------------------------------- through the merge (MergeFn)

SITES = [
    # the postal codes the upstream matcher put in Uruguay and Zimbabwe
    site(ASBESTOS, "1010", state=cand("CA", "Q6915")),
    site(ASBESTOS, "499", state=cand("ME", "Q7079")),
    # already right
    site(ASBESTOS, "20", state=cand("OR", "Q6886")),
    # the same record from another extractor, with no location at all
    site(ASBESTOS, "1010", creator="sri"),
]


@pytest.fixture(scope="module")
def merge_inputs(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("merge")
    entity_dir = root / "entities"
    entity_dir.mkdir()
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
                "site_id": MineralSiteIdent.from_dict(s).id,
                "dedup_id": "d_" + s["record_id"],
            }
            for s in SITES
        ],
        sameas,
    )
    empty = root / "empty.csv"
    empty.write_text("source_id,normalized_uri,note\n")
    listfile = root / "country.csv"
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
        "state_or_province_file": infile(entity_dir / "state_or_province.json"),
        "empty": infile(empty),
        "list": infile(listfile),
    }


def merge(inputs: dict, name: str, country_corrections_files: list[InputFile]):
    workdir = inputs["root"] / f"work-{name}"
    workdir.mkdir()
    out = MergeFn(workdir, inputs["entity_dir"]).invoke(
        infiles=inputs["infiles"],
        sameas_file=inputs["sameas_file"],
        outfile=inputs["root"] / f"out-{name}.json",
        corrections_files=[],
        country_corrections_files=country_corrections_files,
        state_or_province_file=inputs["state_or_province_file"],
    )
    return out


def test_an_empty_list_changes_nothing_byte_for_byte(merge_inputs):
    none = merge(merge_inputs, "none", []).read_bytes()
    empty = merge(merge_inputs, "empty", [merge_inputs["empty"]]).read_bytes()
    assert none == empty


def test_the_merge_repairs_the_state_inside_the_country(merge_inputs):
    out = serde.json.deser(merge(merge_inputs, "list", [merge_inputs["list"]]))
    msis = [
        MineralSiteAndInventory.from_dict(x) for x in out["MineralSiteAndInventory"]
    ]
    by_key = {(m.ms.record_id, m.ms.created_by[-3:]): m.ms for m in msis}
    for record_id, state in (("1010", "Q6845"), ("499", "Q6865"), ("20", "Q6886")):
        ms = by_key[(record_id, "umn")]
        assert ms.location_view.country == ["Q1235"]
        assert ms.location_view.state_or_province == [state]
        # the stored candidate, which the KG export writes out, keeps the
        # source's state and gains the country
        kg = ms.to_kg().location_info
        assert kg is not None
        assert [c.normalized_uri for c in kg.country] == [US]
    assert by_key[("1010", "sri")].location_view.country == ["Q1235"]

    dedups = {
        d.id: d
        for d in (DedupMineralSite.from_dict(x) for x in out["DedupMineralSite"])
    }
    twin_lakes = dedups["d_1010"]
    assert twin_lakes.country.value == ["Q1235"]
    assert twin_lakes.state_or_province.value == ["Q6845"]
    assert dedups["d_499"].state_or_province.value == ["Q6865"]


def test_without_the_list_the_state_stays_in_another_country(merge_inputs):
    out = serde.json.deser(merge(merge_inputs, "none-2", []))
    dedups = {
        d.id: d
        for d in (DedupMineralSite.from_dict(x) for x in out["DedupMineralSite"])
    }
    assert dedups["d_1010"].country.value == []
    assert dedups["d_1010"].state_or_province.value == ["Q6915"]


# ------------------------------------------------- data checks
# Run against a ta2-minmod-data checkout with MINMOD_DATA_DIR=<checkout>/data;
# without it, against the small fixture and the test entity tables.
DATA_DIR = Path(os.environ.get("MINMOD_DATA_DIR", FIXTURE_DATA_DIR))
COUNTRY_TABLE = (
    DATA_DIR / "entities/country.csv"
    if "MINMOD_DATA_DIR" in os.environ
    else RESOURCE_DIR / "kgdata/entities/country.csv"
)


def test_every_country_is_a_country():
    with open(COUNTRY_TABLE, encoding="utf-8-sig") as f:
        qids = {r["minmod_id"] for r in csv.DictReader(f)}
    rows = CountryCorrections.from_file(DATA_DIR / "corrections/country.csv").rows
    assert len(rows) > 0
    for row in rows:
        assert row.normalized_uri[len(MR) :] in qids, row


def test_every_listed_source_has_only_the_listed_states():
    """A listed source's records name states of the listed country only, so
    the country added for them is not a guess."""
    if "MINMOD_DATA_DIR" not in os.environ:
        pytest.skip("needs a ta2-minmod-data checkout")
    with open(DATA_DIR / "entities/state_or_province.csv", encoding="utf-8-sig") as f:
        codes: dict[str, set[str]] = {}
        for r in csv.DictReader(f):
            # a source may write the code or the name
            names = codes.setdefault(r["country_code"], set())
            names.update((r["state_code"], r["name"]))
    with open(DATA_DIR / "entities/country.csv", encoding="utf-8-sig") as f:
        iso2 = {r["minmod_id"]: r["iso2"] for r in csv.DictReader(f)}

    rows = CountryCorrections.from_file(DATA_DIR / "corrections/country.csv").rows
    for row in rows:
        allowed = codes[iso2[row.normalized_uri[len(MR) :]]]
        n = 0
        for f in sorted((DATA_DIR / "mineral-sites").glob("*/*/*.json")):
            for s in serde.json.deser(f):
                if s["source_id"] != row.source_id:
                    continue
                for st in (s.get("location_info") or {}).get("state_or_province", []):
                    n += 1
                    assert st.get("observed_name") in allowed, (f, s["record_id"])
        assert n > 0, row
