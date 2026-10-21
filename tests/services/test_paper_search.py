"""Searching GeoChem papers by measured element. Docker-free, on in-memory
SQLite like test_sample; get_sites aggregates with Postgres-only jsonb_agg,
so GET /papers/{paper_id} is not covered here."""

from __future__ import annotations

import minmodkg.models.kgrel.dedup_mineral_site  # noqa: F401 -- see test_sample
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from minmodkg.api.dependencies import get_paper_service
from minmodkg.api.routers import paper as paper_router
from minmodkg.etl.geochem_jsonld import USER_URI
from minmodkg.models.kgrel.base import Base
from minmodkg.models.kgrel.custom_types.location import LocationView
from minmodkg.models.kgrel.mineral_site import MineralSite
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.sample_element import (
    SampleElement,
    replace_sample_elements,
)
from minmodkg.models.kgrel.views.mineral_inventory_view import MineralInventoryView
from minmodkg.services.paper import PaperService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# paper -> site -> samples, each sample a list of (label, grade)
PAPERS = {
    "2004_Ono_etal": {
        "ono_a": [[("Ga", 12.0), ("Se", -0.4)], [("Ga", None)]],
        "ono_b": [[("In", 3.0)]],
    },
    "2011_Ye_etal": {
        "ye_a": [[("Ga", -0.2)], [("In", 0.0)]],
    },
}


def make_site(paper_id: str, record_id: str) -> MineralSite:
    return MineralSite(
        site_id=f"site__{record_id}",
        dedup_site_id="unused",
        source_id=f"https://doi.org/{paper_id}",
        source_score=None,
        record_id=record_id,
        name=record_id,
        aliases=[],
        rank=None,
        type=None,
        location=None,
        location_view=LocationView(),
        deposit_type_candidates=[],
        inventories=[],
        reference=[],
        mineral_form=[],
        geology_info=None,
        discovered_year=None,
        is_deleted=False,
        deleted_by=None,
        deleted_at=None,
        created_by=USER_URI,
        modified_at=0,
    )


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        eng,
        tables=[
            MineralSite.__table__,
            MineralInventoryView.__table__,
            Sample.__table__,
            SampleElement.__table__,
            Paper.__table__,
        ],
    )
    with Session(eng) as session:
        for paper_id, sites in PAPERS.items():
            session.add(
                Paper(
                    paper_id=paper_id,
                    source_id=f"https://doi.org/{paper_id}",
                    doi=paper_id,
                    title=None,
                    authors=None,
                    journal=None,
                    year=None,
                    url=None,
                    file=f"{paper_id}.jsonld",
                    modified_at=0,
                )
            )
            for record_id, samples in sites.items():
                site = make_site(paper_id, record_id)
                session.add(site)
                session.flush()
                if record_id == "ono_b":
                    session.add(
                        MineralInventoryView(
                            commodity="Q1",
                            contained_metal=None,
                            tonnage=None,
                            grade=None,
                            date=None,
                            site_id=site.id,
                        )
                    )
                rows = []
                for i, elements in enumerate(samples):
                    sample = Sample.from_raw_sample(
                        {
                            "sample_id": f"{record_id}-{i}",
                            "mineral_site_id": site.site_id,
                            "analyses": [
                                {
                                    "elements": [
                                        {"label": label, "grade": grade}
                                        for label, grade in elements
                                    ]
                                }
                            ],
                        }
                    )
                    sample.modified_at = 0
                    rows.append(sample)
                session.add_all(rows)
                session.flush()
                replace_sample_elements(session, rows)
        session.commit()
    return eng


@pytest.fixture
def client(engine) -> TestClient:
    app = FastAPI()
    app.include_router(paper_router.router)
    app.dependency_overrides[get_paper_service] = lambda: PaperService(engine)
    return TestClient(app)


def found(client: TestClient, **params) -> dict[str, list[str]]:
    resp = client.get("/papers", params=params)
    assert resp.status_code == 200, resp.text
    return {p["paper_id"]: [s["record_id"] for s in p["sites"]] for p in resp.json()}


class TestSearch:
    def test_detected_only_by_default(self, client: TestClient):
        assert found(client, element="Ga") == {"2004_Ono_etal": ["ono_a"]}
        assert found(client, element="Se") == {}

    def test_include_bdl(self, client: TestClient):
        assert found(client, element="Ga", include_bdl=True) == {
            "2004_Ono_etal": ["ono_a"],
            "2011_Ye_etal": ["ye_a"],
        }
        assert found(client, element="Se", include_bdl=True) == {
            "2004_Ono_etal": ["ono_a"]
        }

    def test_zero_grade_never_matches(self, client: TestClient):
        assert found(client, element="In", include_bdl=True) == {
            "2004_Ono_etal": ["ono_b"]
        }

    def test_name_or_symbol_any_case(self, client: TestClient):
        expected = {"2004_Ono_etal": ["ono_a"]}
        for value in ("Ga", "ga", "Gallium", "GALLIUM"):
            assert found(client, element=value) == expected

    def test_ands_with_commodity_per_site(self, client: TestClient):
        assert found(client, element="In", commodity="Q1") == {
            "2004_Ono_etal": ["ono_b"]
        }
        # Ga is at ono_a, the commodity at ono_b: no single site has both
        assert found(client, element="Ga", commodity="Q1") == {}

    def test_count(self, client: TestClient):
        resp = client.get(
            "/papers",
            params={"element": "Ga", "include_bdl": True, "return_count": True},
        )
        assert resp.json()["total"] == 2

    def test_unknown_element(self, client: TestClient):
        resp = client.get("/papers", params={"element": "Unobtainium"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Element `Unobtainium` not found"

    def test_include_bdl_needs_element(self, client: TestClient):
        assert client.get("/papers", params={"include_bdl": True}).status_code == 400


class TestSamples:
    def sample_ids(self, client: TestClient, paper_id: str, **params) -> list[str]:
        resp = client.get(f"/papers/{paper_id}/samples", params=params)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == len(body["items"])
        return [s["sample_id"] for s in body["items"]]

    def test_filters_samples(self, client: TestClient):
        assert self.sample_ids(client, "2004_Ono_etal") == [
            "ono_a-0",
            "ono_a-1",
            "ono_b-0",
        ]
        assert self.sample_ids(client, "2004_Ono_etal", element="Ga") == ["ono_a-0"]
        assert self.sample_ids(client, "2011_Ye_etal", element="Ga") == []
        assert self.sample_ids(
            client, "2011_Ye_etal", element="Ga", include_bdl=True
        ) == ["ye_a-0"]

    def test_samples_are_whole(self, client: TestClient):
        resp = client.get("/papers/2004_Ono_etal/samples", params={"element": "Ga"})
        (sample,) = resp.json()["items"]
        labels = [e["label"] for a in sample["analyses"] for e in a["elements"]]
        assert labels == ["Ga", "Se"]

    def test_unknown_element(self, client: TestClient):
        resp = client.get("/papers/2004_Ono_etal/samples", params={"element": "Xx"})
        assert resp.status_code == 404
