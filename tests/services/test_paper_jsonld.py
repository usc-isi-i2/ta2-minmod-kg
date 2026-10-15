"""GET /papers/{paper_id}/jsonld: the paper's file plus the edits not yet
written back to it. Docker-free, on in-memory SQLite like test_sample."""

from __future__ import annotations

import json
from pathlib import Path

import minmodkg.models.kgrel.dedup_mineral_site  # noqa: F401 -- see test_sample
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from minmodkg.api.dependencies import get_paper_service
from minmodkg.api.routers import paper as paper_router
from minmodkg.etl.geochem_jsonld import (
    USER_URI,
    build_paper,
    deposit_sites,
    paper_source_id,
    read_paper,
    write_paper_file,
)
from minmodkg.models.kgrel.base import Base
from minmodkg.models.kgrel.custom_types.location import LocationView
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSite
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.services.paper import PaperService
from minmodkg.services.sync.geochem_listener import GeoChemBackupListener
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.etl.test_geochem_jsonld import make_paper, resolver

FILE = "geochem_2004_X.canonical.jsonld"


@pytest.fixture
def engine(monkeypatch):
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        eng,
        tables=[
            MineralSite.__table__,
            Sample.__table__,
            EventLog.__table__,
            Paper.__table__,
        ],
    )
    # routing reads papers and sites through the default engine
    monkeypatch.setattr("minmodkg.services.sync.geochem_routing.engine", eng)
    return eng


@pytest.fixture
def jsonld_dir(tmp_path: Path, engine) -> Path:
    doc = make_paper()
    write_paper_file(tmp_path / FILE, doc)
    content = read_paper(doc, resolver())
    with Session(engine) as session:
        session.add(build_paper(doc, FILE, 0))
        # the paper's two deposit nodes share one site
        for site_id in dict(deposit_sites(doc)):
            session.add(
                MineralSite(
                    site_id=site_id,
                    dedup_site_id="unused",
                    source_id=paper_source_id(doc),
                    source_score=None,
                    record_id="suttsu",
                    name=None,
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
            )
        session.add_all(Sample.from_raw_sample(s) for s in content.samples)
        session.commit()
    return tmp_path


def edit_sample(engine, name: str) -> None:
    """Rename the paper's sample the way the publish API does: the row and
    an unsynced event in one transaction."""
    with Session(engine) as session:
        sample = session.execute(select(Sample)).scalars().first()
        sample.sample_name = name
        session.add(EventLog.from_sample_update(sample))
        session.commit()


def write_back(engine, jsonld_dir: Path, mark_synced: bool = True) -> None:
    with Session(engine) as session:
        events = (
            session.execute(select(EventLog).where(EventLog.geochem_synced.is_(False)))
            .scalars()
            .all()
        )
        GeoChemBackupListener(jsonld_dir).handle(events)
        if mark_synced:
            session.execute(update(EventLog).values(geochem_synced=True))
            session.commit()


def get_paper(engine) -> Paper:
    with Session(engine) as session:
        return session.get(Paper, "2004_X")


def sample_name(text: str) -> str:
    return json.loads(text)["deposits"][0]["samples"][0]["sample_name"]


class TestGetJsonld:
    def test_without_edits_is_the_file(self, engine, jsonld_dir: Path):
        out = PaperService(engine).get_jsonld(get_paper(engine), jsonld_dir)
        assert out == (jsonld_dir / FILE).read_text()

    def test_includes_edits_not_written_back(self, engine, jsonld_dir: Path):
        before = (jsonld_dir / FILE).read_text()
        edit_sample(engine, "pyrite 66 (edited)")

        out = PaperService(engine).get_jsonld(get_paper(engine), jsonld_dir)
        assert sample_name(out) == "pyrite 66 (edited)"
        assert (jsonld_dir / FILE).read_text() == before

    def test_matches_the_file_after_write_back(self, engine, jsonld_dir: Path):
        edit_sample(engine, "pyrite 66 (edited)")
        service = PaperService(engine)
        pending = service.get_jsonld(get_paper(engine), jsonld_dir)

        write_back(engine, jsonld_dir)
        assert (jsonld_dir / FILE).read_text() == pending
        assert service.get_jsonld(get_paper(engine), jsonld_dir) == pending

    def test_written_back_but_not_yet_marked(self, engine, jsonld_dir: Path):
        # the write-back saved the file but hasn't committed its flags
        edit_sample(engine, "pyrite 66 (edited)")
        write_back(engine, jsonld_dir, mark_synced=False)

        out = PaperService(engine).get_jsonld(get_paper(engine), jsonld_dir)
        assert out == (jsonld_dir / FILE).read_text()


class TestRoute:
    @pytest.fixture
    def client(self, engine, jsonld_dir: Path, monkeypatch) -> TestClient:
        monkeypatch.setattr(paper_router, "GEOCHEM_JSONLD_DIR", jsonld_dir)
        app = FastAPI()
        app.include_router(paper_router.router)
        app.dependency_overrides[get_paper_service] = lambda: PaperService(engine)
        return TestClient(app)

    def test_returns_jsonld(self, client: TestClient, jsonld_dir: Path):
        resp = client.get("/papers/2004_X/jsonld")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/ld+json"
        assert resp.text == (jsonld_dir / FILE).read_text()

    def test_etag(self, client: TestClient, engine):
        etag = client.get("/papers/2004_X/jsonld").headers["etag"]
        resp = client.get("/papers/2004_X/jsonld", headers={"If-None-Match": etag})
        assert resp.status_code == 304

        edit_sample(engine, "pyrite 66 (edited)")
        resp = client.get("/papers/2004_X/jsonld", headers={"If-None-Match": etag})
        assert resp.status_code == 200
        assert resp.headers["etag"] != etag

    def test_unknown_paper(self, client: TestClient):
        assert client.get("/papers/nope/jsonld").status_code == 404

    def test_missing_file(self, client: TestClient, jsonld_dir: Path):
        (jsonld_dir / FILE).unlink()
        assert client.get("/papers/2004_X/jsonld").status_code == 500
