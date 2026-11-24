from __future__ import annotations

import json
from pathlib import Path

import pytest
from minmodkg.etl import geochem_drop
from minmodkg.etl.geochem_drop import cited_supplements, scan


def write_paper(root: Path, paper_id: str, doi: str | None, **extra) -> Path:
    path = root / f"geochem_{paper_id}.canonical.jsonld"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "paper_id": paper_id,
        "paper_doi": doi,
        "paper_title": f"Title of {paper_id}",
    }
    path.write_text(json.dumps(doc | extra))
    return path


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture(autouse=True)
def fake_pdf_text(monkeypatch):
    # a "PDF" here is a text file holding what its first pages would say
    monkeypatch.setattr(geochem_drop, "pdf_text", lambda pdf: pdf.read_text())


def test_shared_doi_and_paper_id_exclude_every_copy(tmp_path):
    write_paper(tmp_path, "2022_Benites_etal", "10.1/x")
    write_paper(tmp_path, "2022_Culqui_etal", "10.1/x")
    write_paper(tmp_path / "a", "2020_Yu_etal", "10.1/y")
    write_paper(tmp_path / "b", "2020_Yu_etal", "10.1/z")
    write_paper(tmp_path, "2004_Ono_etal", "10.1/ono")
    write_paper(tmp_path, "2025_Woje", None)

    drop = scan(tmp_path, frozenset({"2004_Ono_etal"}))

    assert [b.paper_id for b in drop.bundles] == ["2025_Woje"]
    assert set(drop.excluded) == {
        "2022_Benites_etal",
        "2022_Culqui_etal",
        "2020_Yu_etal",
        "2004_Ono_etal",
    }
    assert "10.1/x" in drop.excluded["2022_Benites_etal"]
    assert drop.without_doi == ["2025_Woje"]


def test_pdf_found_by_paper_id_or_doi_and_checked(tmp_path):
    write_paper(tmp_path / "jsonld", "2004_Ono_etal", "10.1111/j.1751")
    write_paper(tmp_path / "jsonld", "2011_Ye_etal", "10.1016/j.ye")
    write_paper(tmp_path / "jsonld", "2016_Bonnet_etal", "10.1016/j.bonnet")
    write_paper(tmp_path / "jsonld", "2018_Yuan_etal", "10.1016/j.yuan")
    touch(tmp_path / "pdfs/2004_Ono_etal.pdf", "doi: 10.1111/j.1751-blah")
    touch(tmp_path / "lib/10.1016_j.ye/paper.pdf", "TITLE OF 2011_YE_ETAL")
    touch(tmp_path / "pdfs/2016_Bonnet_etal.pdf", "some other paper")
    touch(tmp_path / "pdfs/2018_Yuan_etal.pdf", "")

    found = {b.paper_id: b for b in scan(tmp_path).bundles}

    assert found["2004_Ono_etal"].pdf_status == "doi"
    assert found["2011_Ye_etal"].pdf == tmp_path / "lib/10.1016_j.ye/paper.pdf"
    assert found["2011_Ye_etal"].pdf_status == "title"
    assert found["2016_Bonnet_etal"].pdf is None
    assert found["2016_Bonnet_etal"].pdf_status.startswith("mismatch")
    assert found["2018_Yuan_etal"].pdf_status == "no-text"


def test_supplements_and_workbook(tmp_path):
    doc = {
        "deposits": [
            {"samples": [{"reference": {"source": "supp:table1.xlsx"}}]},
            {"artifact_path": "supp:dir/table2.csv", "note": "supp:figure.png"},
        ]
    }
    write_paper(tmp_path / "jsonld", "2016_Frenzel_etal", "10.1/f", **doc)
    touch(tmp_path / "other/table1.xlsx")
    touch(tmp_path / "by_paper/2016_Frenzel_etal/table1.xlsx")
    touch(tmp_path / "extraction_2016_Frenzel_etal.xlsx")

    (b,) = scan(tmp_path).bundles

    assert b.supplements == [tmp_path / "by_paper/2016_Frenzel_etal/table1.xlsx"]
    assert b.missing_supplements == ["table2.csv"]
    assert b.workbook == tmp_path / "extraction_2016_Frenzel_etal.xlsx"
    assert b.pdf is None and b.pdf_status == "missing"


def test_cited_supplements_keeps_first_seen_order():
    doc = [
        {"source": "supp:b.xlsx"},
        {"source": "supp:a.xls"},
        {"source": "supp:b.xlsx"},
    ]
    assert cited_supplements(doc) == ["b.xlsx", "a.xls"]


def test_pdf_under_another_name_found_by_doi(tmp_path):
    write_paper(tmp_path, "2023_Soster_etal", "10.1/soster")
    write_paper(tmp_path, "2016_George_etal", "10.1/george")
    touch(tmp_path / "pdfs/2023_Soster_etal1.pdf", "https://doi.org/10.1/soster")
    touch(tmp_path / "pdfs/2016_George_etal.pdf", "10.1/george")

    found = {b.paper_id: b for b in scan(tmp_path).bundles}

    assert found["2023_Soster_etal"].pdf == tmp_path / "pdfs/2023_Soster_etal1.pdf"
    assert found["2023_Soster_etal"].pdf_status == "doi, other name"
    assert found["2016_George_etal"].pdf_status == "doi"
