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
                is_deleted=False,
                deleted_by=None,
                deleted_at=None,
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


class TestStratUnitNameAndLocation:
    """strat_unit_name (independent of strat_unit_uid) and location (mo:location_info
    widened to :Sample) -- both added as normal patchable fields alongside the
    existing scalar ones, see ta2-table-understanding issue #19."""

    def test_patches_strat_unit_name_independently_of_uid(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-STRAT-1",
                    "strat_unit_uid": "SU-001",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            sample_public_id("SM-STRAT-1"),
            {"strat_unit_name": "Fort Payne Formation"},
            USER,
        )
        assert patched.strat_unit_name == "Fort Payne Formation"
        assert patched.strat_unit_uid == "SU-001"
        assert (
            "https://geochemistry.isi.edu/ontology/strat_unit_name"
            in patched.edit_history[-1].changed_properties
        )

    def test_patches_location(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-LOC-1",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            sample_public_id("SM-LOC-1"),
            {"location": {"coordinates": "POINT(-84.6 35.6)"}},
            USER,
        )
        assert patched.location is not None
        assert patched.location.coordinates == "POINT(-84.6 35.6)"
        # the real predicate is mo:location_info, not a guessed gco:location --
        # see _FIELD_TO_PROPERTY_URI's docstring for the bug this guards against
        assert (
            "https://minmod.isi.edu/ontology/location_info"
            in patched.edit_history[-1].changed_properties
        )
        assert (
            "https://geochemistry.isi.edu/ontology/location"
            not in patched.edit_history[-1].changed_properties
        )


class TestSoftDelete:
    """is_deleted on Sample/Analysis/Element: a normal patchable field, not a new
    verb/endpoint. deleted_by/deleted_at are server-stamped when is_deleted actually
    changes, and cleared on undelete -- see SampleService._stamp_deletion."""

    def test_deleting_sample_stamps_deleted_by_and_at(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-DEL-1",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            sample_public_id("SM-DEL-1"), {"is_deleted": True}, USER
        )
        assert patched.is_deleted is True
        assert patched.deleted_by == USER
        assert patched.deleted_at is not None
        assert (
            "https://geochemistry.isi.edu/ontology/is_deleted"
            in patched.edit_history[-1].changed_properties
        )

    def test_undeleting_sample_clears_deleted_by_and_at(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-DEL-2",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        service.patch(sample_public_id("SM-DEL-2"), {"is_deleted": True}, USER)
        patched = service.patch(
            sample_public_id("SM-DEL-2"), {"is_deleted": False}, USER
        )
        assert patched.is_deleted is False
        assert patched.deleted_by is None
        assert patched.deleted_at is None

    def test_deleting_element_stamps_only_the_element(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-DEL-3",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [{"label": "Au", "grade": 1.0}],
                        }
                    ],
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            sample_public_id("SM-DEL-3"),
            {"analyses": [{"analysis_id": "A-1", "elements": [{"label": "Au", "is_deleted": True}]}]},
            USER,
        )
        assert patched.is_deleted is False
        assert patched.deleted_by is None
        [analysis] = patched.analyses
        assert analysis.is_deleted is False
        assert analysis.deleted_by is None
        [element] = analysis.elements
        assert element.is_deleted is True
        assert element.deleted_by == USER
        assert element.deleted_at is not None

    def test_deleting_analysis_stamps_only_the_analysis(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-DEL-4",
                    "analyses": [{"analysis_id": "A-1"}],
                    "modified_at": 0,
                }
            ),
            USER,
        )
        patched = service.patch(
            sample_public_id("SM-DEL-4"),
            {"analyses": [{"analysis_id": "A-1", "is_deleted": True}]},
            USER,
        )
        [analysis] = patched.analyses
        assert analysis.is_deleted is True
        assert analysis.deleted_by == USER
        assert analysis.deleted_at is not None
        assert patched.is_deleted is False

    def test_deleting_via_publish_and_undeleting(self, service: SampleService):
        service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-DEL-5",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        result = service.publish(
            publish_payload({"sample_id": "SM-DEL-5", "is_deleted": True}), USER
        )
        assert result["errors"] == []
        deleted = service.find_by_id(sample_public_id("SM-DEL-5"))
        assert deleted.is_deleted is True
        assert deleted.deleted_by == USER
        assert deleted.deleted_at is not None

        result = service.publish(
            publish_payload({"sample_id": "SM-DEL-5", "is_deleted": False}), USER
        )
        assert result["errors"] == []
        undeleted = service.find_by_id(sample_public_id("SM-DEL-5"))
        assert undeleted.is_deleted is False
        assert undeleted.deleted_by is None
        assert undeleted.deleted_at is None

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


class TestSHACLValidation:
    """SampleService.publish()'s SHACL gate (ta2-table-understanding issue #18):
    validate_sample_shacl() is called on the fully-formed post-edit sample before
    either create() or patch() persists anything -- a failure is a per-sample
    errors[] entry, not a raised exception, and nothing is written to the DB.

    Mocks minmodkg.services.sample.validate_sample_shacl directly rather than
    exercising the real pyshacl/rdflib pipeline -- that pipeline is verified
    separately (see tests/test_validators.py's real-shape-file checks); this
    isolates publish()'s own wiring: does a failure block persistence, does a
    pass go through unaffected, does the message reach errors[]."""

    def test_shacl_failure_blocks_new_sample_creation(self, service: SampleService):
        with patch(
            "minmodkg.services.sample.validate_sample_shacl",
            return_value=["missing required field X"],
        ):
            result = service.publish(
                publish_payload({"sample_id": "SM-SHACL-1", "sample_name": "bad"}),
                USER,
            )
        assert result["created"] == []
        assert len(result["errors"]) == 1
        assert "missing required field X" in result["errors"][0]["detail"]
        assert service.find_by_id(sample_public_id("SM-SHACL-1")) is None

    def test_shacl_failure_blocks_existing_sample_patch(self, service: SampleService):
        created = service.create(
            RelSample.from_dict(
                {
                    "public_id": "",
                    "mineral_site_id": SITE_ID,
                    "sample_id": "SM-SHACL-2",
                    "description": "original",
                    "modified_at": 0,
                }
            ),
            USER,
        )
        with patch(
            "minmodkg.services.sample.validate_sample_shacl",
            return_value=["bad shape"],
        ):
            result = service.publish(
                publish_payload({"sample_id": "SM-SHACL-2", "description": "SHOULD NOT PERSIST"}),
                USER,
            )
        assert result["updated"] == []
        assert len(result["errors"]) == 1
        unchanged = service.find_by_id(sample_public_id("SM-SHACL-2"))
        assert unchanged.description == "original"
        assert unchanged.modified_at == created.modified_at

    def test_shacl_pass_does_not_block_publish(self, service: SampleService):
        with patch(
            "minmodkg.services.sample.validate_sample_shacl", return_value=[]
        ):
            result = service.publish(
                publish_payload({"sample_id": "SM-SHACL-3", "sample_name": "fine"}),
                USER,
            )
        assert result["errors"] == []
        assert len(result["created"]) == 1
        assert service.find_by_id(sample_public_id("SM-SHACL-3")) is not None


class TestBareUnitLabels:
    """grade_unit/detection_limit_unit as a bare label/URI string (issue #18 §5)
    -- previously crashed publish() with an uncaught AttributeError ('str'
    object has no attribute 'get') in _validate_units, since it assumed every
    caller still sent the older wrapped CandidateEntity shape. Reported
    externally by Ryan (HMI) hitting exactly this on a real publish call."""

    def test_bare_label_resolves_and_does_not_crash(self, service: SampleService):
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-UNIT-1",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {"symbol": "Au", "grade": 2.5, "grade_unit": "g/t"}
                            ],
                        }
                    ],
                }
            ),
            USER,
        )
        assert result["errors"] == []
        sample = service.find_by_id(sample_public_id("SM-UNIT-1"))
        element = sample.analyses[0].elements[0]
        assert element.grade_unit.normalized_uri == GOOD_UNIT
        assert element.grade_unit.observed_name == "g/t"

    def test_bare_label_matches_case_insensitively_by_name_or_alias(
        self, service: SampleService
    ):
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-UNIT-2",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {
                                    "symbol": "Au",
                                    "grade": 2.5,
                                    "grade_unit": "Grams Per Tonne",
                                }
                            ],
                        }
                    ],
                }
            ),
            USER,
        )
        assert result["errors"] == []
        sample = service.find_by_id(sample_public_id("SM-UNIT-2"))
        assert sample.analyses[0].elements[0].grade_unit.normalized_uri == GOOD_UNIT

    def test_bare_uri_is_accepted_directly(self, service: SampleService):
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-UNIT-3",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {"symbol": "Au", "grade": 2.5, "grade_unit": GOOD_UNIT}
                            ],
                        }
                    ],
                }
            ),
            USER,
        )
        assert result["errors"] == []
        sample = service.find_by_id(sample_public_id("SM-UNIT-3"))
        assert sample.analyses[0].elements[0].grade_unit.normalized_uri == GOOD_UNIT

    def test_unresolvable_bare_label_is_a_per_sample_error_not_a_crash(
        self, service: SampleService
    ):
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-UNIT-4",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {
                                    "symbol": "Au",
                                    "grade": 2.5,
                                    "grade_unit": "not-a-real-unit-label",
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
        assert "not-a-real-unit-label" in result["errors"][0]["detail"]
        assert service.find_by_id(sample_public_id("SM-UNIT-4")) is None

    def test_old_wrapped_shape_still_works(self, service: SampleService):
        """Backward compat: a caller that still sends the older wrapped
        CandidateEntity shape directly (not a bare string) is left untouched by
        _resolve_element_units and validated the same way as before."""
        result = service.publish(
            publish_payload(
                {
                    "sample_id": "SM-UNIT-5",
                    "analyses": [
                        {
                            "analysis_id": "A-1",
                            "elements": [
                                {
                                    "symbol": "Au",
                                    "grade": 2.5,
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
        sample = service.find_by_id(sample_public_id("SM-UNIT-5"))
        assert sample.analyses[0].elements[0].grade_unit.normalized_uri == GOOD_UNIT
