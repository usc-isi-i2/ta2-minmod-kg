"""Tests for SampleService's patch/publish upsert semantics (ta2-table-understanding
issue #18): unmatched analysis_id/element label creates instead of rejecting, and
publish() batches that across a whole paper's worth of samples with per-sample error
isolation.

Deliberately Docker-free: an in-memory SQLite engine, not the Docker-Postgres fixtures
in tests/conftest.py. Only mineral_site/sample/event_log/unit are created -- nothing
here is Postgres-specific (no ARRAY/JSONB columns on those four tables), so this runs
identically locally and in CI with no external services. dedup_mineral_site is
imported (so MineralSite's FK to it resolves at DDL-compile time) but never created --
its dedup columns use a Postgres-only ARRAY type that's irrelevant here, and SQLite
doesn't enforce FK constraints by default, so the missing table is never touched.

The one thing genuinely untestable under SQLite is real FK enforcement (verified
instead against live Postgres, see issue #18) -- the "unresolvable mineral_site_id"
case below simulates that failure by mocking create() to raise IntegrityError, which
is what publish() actually catches; it doesn't re-derive Postgres's own FK behavior.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import minmodkg.models.kgrel.dedup_mineral_site  # noqa: F401 -- see module docstring
from minmodkg.models.kgrel.base import Base
from minmodkg.models.kgrel.custom_types.location import LocationView
from minmodkg.models.kgrel.entities.unit import Unit
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSite
from minmodkg.models.kgrel.sample import Sample as RelSample
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.services.sample import ArgumentError, SampleService
from minmodkg.transformations import make_sample_id
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

USER = "https://minmod.isi.edu/users/u/tester"
SITE_ID = "site__test__1__tester"
GOOD_UNIT = "https://minmod.isi.edu/resource/Q1"
BAD_UNIT = "https://minmod.isi.edu/resource/NOT-A-REAL-UNIT"


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        eng,
        tables=[
            MineralSite.__table__,
            RelSample.__table__,
            EventLog.__table__,
            Unit.__table__,
        ],
    )
    with Session(eng) as session:
        session.add(
            MineralSite(
                site_id=SITE_ID,
                dedup_site_id="unused",
                source_id="test::source",
                source_score=None,
                record_id="1",
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
                created_by=USER,
                modified_at=0,
            )
        )
        session.add(Unit(id="Q1", name="grams per tonne", aliases=["g/t"]))
        session.commit()
    return eng


@pytest.fixture
def service(engine):
    # EntityService is a process-wide singleton keyed off the global default engine;
    # rebind it to this test's engine so unit-uri lookups see the Unit seeded above
    # instead of a stale instance left over from another test.
    EntityService.instance = EntityService(engine)
    return SampleService(engine)


def sample_public_id(sample_id: str) -> str:
    return make_sample_id(SITE_ID, sample_id)


def publish_payload(*samples: dict, mineral_site_id: str = SITE_ID) -> dict:
    return {
        "paper_id": "test-paper",
        "deposits": [{"mineral_site_id": mineral_site_id, "samples": list(samples)}],
    }


class TestPatchUpsert:
    """SampleService.patch()'s create-on-unmatched behavior for nested analyses/elements."""

    def test_creates_unmatched_analysis(self, service: SampleService):
        created = service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-1",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            created.public_id,
            {"analyses": [{"analysis_id": "A-1", "analytical_method": "ICP-MS"}]},
            USER,
        )
        assert [a.analysis_id for a in patched.analyses] == ["A-1"]
        assert patched.analyses[0].analytical_method == "ICP-MS"
        assert (
            "https://geochemistry.isi.edu/ontology/analytical_method"
            in patched.edit_history[-1].changed_properties
        )

    def test_creates_unmatched_element_on_existing_analysis(self, service: SampleService):
        created = service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-2",
                    "analyses": [{"analysis_id": "A-1"}],
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            created.public_id,
            {"analyses": [{"analysis_id": "A-1", "elements": [{"label": "Au", "grade": 2.5}]}]},
            USER,
        )
        [analysis] = patched.analyses
        assert [e.label for e in analysis.elements] == ["Au"]
        assert analysis.elements[0].grade == 2.5

    def test_rejects_unknown_top_level_field(self, service: SampleService):
        created = service.create(
            RelSample.from_dict(
                {"public_id": "", "mineral_site_id": SITE_ID, "sample_id": "SM-3", "modified_at": 0}
            ),
            USER,
        )
        with pytest.raises(ArgumentError):
            service.patch(created.public_id, {"sample_id": "renamed"}, USER)

    def test_noop_patch_does_not_append_edit_history(self, service: SampleService):
        created = service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-4",
                    "description": "same",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        before = len(created.edit_history)
        result = service.patch(created.public_id, {"description": "same"}, USER)
        assert len(result.edit_history) == before
        assert result.modified_at == created.modified_at


class TestPublish:
    """SampleService.publish() -- the batch/per-paper POST /papers/publish entrypoint."""

    def test_creates_new_sample(self, service: SampleService):
        result = service.publish(
            publish_payload({"sample_id": "SM-NEW", "sample_name": "brand new"}), USER
        )
        assert result["errors"] == []
        assert result["updated"] == []
        [created] = result["created"]
        assert created["sample_id"] == sample_public_id("SM-NEW")
        assert service.find_by_id(sample_public_id("SM-NEW")) is not None

    def test_updates_existing_sample(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {"public_id": "", "mineral_site_id": SITE_ID, "sample_id": "SM-5", "modified_at": 0}
            ),
            USER,
        )
        result = service.publish(
            publish_payload({"sample_id": "SM-5", "description": "edited via publish"}), USER
        )
        assert result["errors"] == []
        assert result["created"] == []
        [updated] = result["updated"]
        assert updated["sample_id"] == sample_public_id("SM-5")
        assert (
            service.find_by_id(sample_public_id("SM-5")).description == "edited via publish"
        )

    def test_translates_symbol_to_label(self, service: SampleService):
        service.publish(
            publish_payload(
                {
                    "sample_id": "SM-6",
                    "analyses": [
                        {"analysis_id": "A-1", "elements": [{"symbol": "Cu", "grade": 1.2}]}
                    ],
                }
            ),
            USER,
        )
        sample = service.find_by_id(sample_public_id("SM-6"))
        assert sample.analyses[0].elements[0].label == "Cu"

    def test_creating_new_sample_does_not_require_snapshot_or_extra_fields(
        self, service: SampleService
    ):
        result = service.publish(publish_payload({"sample_id": "SM-BARE"}), USER)
        assert result["errors"] == []
        assert len(result["created"]) == 1

    def test_isolates_per_sample_errors(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {"public_id": "", "mineral_site_id": SITE_ID, "sample_id": "SM-7", "modified_at": 0}
            ),
            USER,
        )
        payload = publish_payload(
            {"sample_id": "SM-7", "description": "should still save"},
            {"description": "missing sample_id, should error"},
        )
        result = service.publish(payload, USER)
        assert len(result["updated"]) == 1
        assert result["updated"][0]["sample_id"] == sample_public_id("SM-7")
        assert len(result["errors"]) == 1
        assert result["errors"][0]["detail"] == "sample_id is required"
        assert service.find_by_id(sample_public_id("SM-7")).description == "should still save"

    def test_rejects_malformed_deposits_list(self, service: SampleService):
        with pytest.raises(ArgumentError):
            service.publish({"paper_id": "x"}, USER)

    def test_reports_bad_analysis_field_as_per_sample_error_not_a_crash(
        self, service: SampleService
    ):
        result = service.publish(
            publish_payload(
                {"sample_id": "SM-8", "analyses": [{"not_analysis_id": "A-1"}]}
            ),
            USER,
        )
        assert result["created"] == []
        assert len(result["errors"]) == 1
        assert "analysis_id" in result["errors"][0]["detail"]

    def test_rejects_invalid_unit_on_newly_created_element(self, service: SampleService):
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-9",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {
                                    "symbol": "Pb",
                                    "grade": 1.0,
                                    "grade_unit": {
                                        "observed_name": "bogus",
                                        "confidence": 1.0,
                                        "source": "test",
                                        "normalized_uri": BAD_UNIT,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ),
            USER,
        )
        assert result["created"] == []
        assert len(result["errors"]) == 1
        assert BAD_UNIT in result["errors"][0]["detail"]
        # confirms the bug caught during manual verification stays fixed: nothing
        # should have been persisted for a sample rejected on unit validation
        assert service.find_by_id(sample_public_id("SM-9")) is None

    def test_accepts_valid_unit(self, service: SampleService):
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-10",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {
                                    "symbol": "Zn",
                                    "grade": 1.0,
                                    "grade_unit": {
                                        "observed_name": "g/t",
                                        "confidence": 1.0,
                                        "source": "test",
                                        "normalized_uri": GOOD_UNIT,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ),
            USER,
        )
        assert result["errors"] == []
        assert len(result["created"]) == 1

    def test_reports_unresolvable_mineral_site_id_as_error(self, service: SampleService):
        # Real FK enforcement is verified against live Postgres (issue #18), not
        # reproducible under SQLite -- see module docstring. This isolates publish()'s
        # own catch/report logic for whatever create() raises on a bad FK.
        with patch.object(service, "create", side_effect=IntegrityError("stmt", {}, Exception())):
            result = service.publish(
                publish_payload(
                    {"sample_id": "SM-11"}, mineral_site_id="site__does-not-exist"
                ),
                USER,
            )
        assert result["created"] == []
        assert result["updated"] == []
        assert len(result["errors"]) == 1
        assert "site__does-not-exist" in result["errors"][0]["detail"]
