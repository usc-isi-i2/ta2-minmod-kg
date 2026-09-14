from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from minmodkg.misc.utils import format_datetime
from minmodkg.models.kg.base import NS_GCO
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.sample import Analysis, Element
from minmodkg.models.kg.sample import EditEvent
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.sample import Sample
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.transformations import make_sample_id
from minmodkg.typing import InternalID
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# fields that are either internal bookkeeping or not real ontology properties on
# :Sample -- never treated as a "changed property" in an EditEvent
_NON_PROPERTY_FIELDS = {"id", "public_id", "mineral_site_id", "edit_history", "modified_at"}

# Sample fields PATCH /samples/{id} is allowed to touch. Deliberately excludes
# sample_id/mineral_site_id (those define the record's identity/URI -- "renaming"
# a sample isn't an edit, it's a different operation not supported here) and the
# system-managed edit_history/modified_at.
_PATCHABLE_SAMPLE_FIELDS = {
    "sample_name",
    "sample_local_id",
    "sample_type",
    "collection_date",
    "description",
    "mineral",
    "sampling_method",
    "sample_preparation",
    "material_class",
    "material_class_comment",
    "analysed_material",
    "sample_deposit_relation",
    "geological_province",
    "strat_unit_uid",
    "strat_grouping",
    "earth_material_group",
    "earth_material_qualifier",
    "mode_occurrence",
    "metamorphic_grade",
    "alteration",
    "paragenetic_stage",
    "texture",
    "color",
    "associated_minerals",
    "feature_type",
    "feature_name",
    "feature_local_uid",
    "top_depth_m",
    "bottom_depth_m",
    "comments",
}
# analysis_id is the natural key used to target an existing analysis, not a
# patchable field itself
_PATCHABLE_ANALYSIS_FIELDS = {
    "analytical_method",
    "instrument_type",
    "laboratory_location",
    "operating_conditions",
    "standards_used",
    "aggregation_method",
    "data_quality",
    "analysis_date",
}
# label is the natural key used to target an existing element, not a patchable
# field itself -- isotopes aren't patchable at all (an Isotope has only `label`,
# so "editing" one is really renaming its identity, same problem as sample_id above)
_PATCHABLE_ELEMENT_FIELDS = {"grade", "grade_unit", "detection_limit", "detection_limit_unit"}


class ArgumentError(Exception):
    pass


class SampleNotFoundError(Exception):
    pass


class ExpiredSnapshotIdError(Exception):
    pass


class SampleService:

    def __init__(self, _engine: Optional[Engine] = None):
        self.engine = _engine or engine

    def get_sample_db_id(self, public_id: InternalID) -> Optional[int]:
        q = select(Sample.id).where(Sample.public_id == public_id)
        with Session(self.engine) as session:
            return session.execute(q).scalar_one_or_none()

    def find_by_id(self, public_id: InternalID) -> Optional[Sample]:
        q = select(Sample).where(Sample.public_id == public_id)
        with Session(self.engine, expire_on_commit=False) as session:
            return session.execute(q).scalar_one_or_none()

    def create(self, sample: Sample, user_uri: str) -> Sample:
        """Create a new sample. sample.public_id/edit_history/modified_at are
        computed here -- not trusted from the caller (see InputPublicSample.to_kgrel,
        which deliberately leaves them as placeholders)."""
        if not sample.sample_id:
            raise ArgumentError(
                "sample_id is required to compute this Sample's identifier/URI "
                "(the ontology's :sample_id is technically optional, but this API "
                "requires it in practice, same as MineralSite requires source_id/record_id)"
            )

        sample.public_id = make_sample_id(sample.mineral_site_id, sample.sample_id)
        now_ns = time.time_ns()
        sample.modified_at = now_ns
        sample.edit_history = [
            EditEvent(
                updated_by=user_uri,
                updated_at=format_datetime(datetime.now(timezone.utc)),
                changed_properties=self._changed_properties({}, sample.to_dict()),
            )
        ]

        with Session(self.engine, expire_on_commit=False) as session:
            session.add(sample)
            session.add(EventLog.from_sample_add(sample))
            session.commit()
            session.refresh(sample)
        return sample

    def update(
        self,
        sample: Sample,
        user_uri: str,
        snapshot_id: Optional[int] = None,
    ) -> Sample:
        """Update an existing sample (sample.id must already be set -- see
        set_id() -- and sample.public_id must match the existing row's, checked by
        the router before calling this). Appends one EditEvent to the existing
        edit_history; never overwrites it (see EditEvent's docstring)."""
        with Session(self.engine, expire_on_commit=False) as session:
            existing = session.execute(
                select(Sample).where(Sample.id == sample.id)
            ).scalar_one_or_none()
            if existing is None:
                raise SampleNotFoundError(f"Sample {sample.id} does not exist")

            if snapshot_id is not None and existing.modified_at != snapshot_id:
                raise ExpiredSnapshotIdError(
                    f"The new snapshot of the sample is {existing.modified_at}"
                )

            changed = self._changed_properties(existing.to_dict(), sample.to_dict())

            sample.edit_history = existing.edit_history + [
                EditEvent(
                    updated_by=user_uri,
                    updated_at=format_datetime(datetime.now(timezone.utc)),
                    changed_properties=changed,
                )
            ]
            sample.modified_at = time.time_ns()

            session.execute(sample.get_update_query())
            session.add(EventLog.from_sample_update(sample))
            session.commit()
        return sample

    def patch(
        self,
        public_id: InternalID,
        patch: dict,
        user_uri: str,
        snapshot_id: Optional[int] = None,
    ) -> Sample:
        """Apply a sparse, keyed upsert to an existing sample. Only fields present
        in `patch` are changed; everything else -- including on any analysis/
        element not mentioned -- is left untouched. Nested analyses/elements are
        matched to existing records by analysis_id/label, not array index/position
        (so a patch survives the sample being re-fetched with analyses in a
        different order). An analysis_id or label that doesn't match an existing
        record creates a new analysis/element instead of erroring (see
        ta2-table-understanding issue #18 -- HMI is no longer edit-only as of
        2026-09-11). The sample itself must already exist -- creating a whole new
        sample is SampleService.create()'s job, called from publish() below.
        """
        unknown_fields = set(patch.keys()) - _PATCHABLE_SAMPLE_FIELDS - {"analyses"}
        if unknown_fields:
            raise ArgumentError(
                f"Unknown or non-editable field(s) in patch: {sorted(unknown_fields)}"
            )

        with Session(self.engine, expire_on_commit=False) as session:
            existing = session.execute(
                select(Sample).where(Sample.public_id == public_id)
            ).scalar_one_or_none()
            if existing is None:
                raise SampleNotFoundError(f"Sample {public_id} does not exist")

            if snapshot_id is not None and existing.modified_at != snapshot_id:
                raise ExpiredSnapshotIdError(
                    f"The new snapshot of the sample is {existing.modified_at}"
                )

            changed: set[str] = set()

            for field in _PATCHABLE_SAMPLE_FIELDS & patch.keys():
                old_value = getattr(existing, field)
                new_value = patch[field]
                if old_value != new_value:
                    setattr(existing, field, new_value)
                    changed.add(field)

            if "analyses" in patch:
                analyses_by_id = {
                    a.analysis_id: a for a in existing.analyses if a.analysis_id
                }
                for a_patch in patch["analyses"]:
                    a_id = a_patch.get("analysis_id")
                    if not a_id:
                        raise ArgumentError(
                            "Each analyses[] patch item requires analysis_id"
                        )
                    if a_id not in analyses_by_id:
                        target_analysis = Analysis(analysis_id=a_id)
                        existing.analyses.append(target_analysis)
                        analyses_by_id[a_id] = target_analysis
                    else:
                        target_analysis = analyses_by_id[a_id]

                    unknown_a_fields = (
                        set(a_patch.keys())
                        - _PATCHABLE_ANALYSIS_FIELDS
                        - {"analysis_id", "elements"}
                    )
                    if unknown_a_fields:
                        raise ArgumentError(
                            f"Unknown or non-editable analysis field(s): {sorted(unknown_a_fields)}"
                        )

                    for field in _PATCHABLE_ANALYSIS_FIELDS & a_patch.keys():
                        old_value = getattr(target_analysis, field)
                        new_value = a_patch[field]
                        if old_value != new_value:
                            setattr(target_analysis, field, new_value)
                            changed.add(field)

                    if "elements" in a_patch:
                        elements_by_label = {
                            e.label: e for e in target_analysis.elements
                        }
                        for e_patch in a_patch["elements"]:
                            label = e_patch.get("label")
                            if not label:
                                raise ArgumentError(
                                    "Each elements[] patch item requires label"
                                )
                            if label not in elements_by_label:
                                target_element = Element(label=label)
                                target_analysis.elements.append(target_element)
                                elements_by_label[label] = target_element
                            else:
                                target_element = elements_by_label[label]

                            unknown_e_fields = (
                                set(e_patch.keys())
                                - _PATCHABLE_ELEMENT_FIELDS
                                - {"label"}
                            )
                            if unknown_e_fields:
                                raise ArgumentError(
                                    f"Unknown or non-editable element field(s): {sorted(unknown_e_fields)}"
                                )

                            for field in _PATCHABLE_ELEMENT_FIELDS & e_patch.keys():
                                old_value = getattr(target_element, field)
                                new_raw = e_patch[field]
                                if field in ("grade_unit", "detection_limit_unit"):
                                    new_value = (
                                        CandidateEntity.from_dict(new_raw)
                                        if new_raw is not None
                                        else None
                                    )
                                else:
                                    new_value = new_raw
                                if old_value != new_value:
                                    setattr(target_element, field, new_value)
                                    changed.add(field)

            if not changed:
                # every provided value already matched the existing one -- a valid
                # no-op, but don't append a vacuous EditEvent or bump modified_at
                return existing

            existing.edit_history = existing.edit_history + [
                EditEvent(
                    updated_by=user_uri,
                    updated_at=format_datetime(datetime.now(timezone.utc)),
                    changed_properties=[NS_GCO.uristr(f) for f in sorted(changed)],
                )
            ]
            existing.modified_at = time.time_ns()

            session.execute(existing.get_update_query())
            session.add(EventLog.from_sample_update(existing))
            session.commit()
        return existing

    def publish(self, payload: dict, user_uri: str) -> dict:
        """Batch upsert entrypoint for POST /papers/publish (ta2-table-understanding
        issue #18): apply one paper's worth of sparse sample/analysis/element
        edits-or-creates in a single call -- one curator Publish click, one call,
        potentially touching several samples across several deposits.

        Each sample is its own transaction, via create()/patch() -- one bad sample
        doesn't block the rest of the batch from saving; failures are collected into
        `errors` instead of raised. `paper_id` in the payload is not resolved to
        anything here -- there is no MinMod-side record of the paper itself, see
        issue #18 -- every write is addressed via mineral_site_id -> sample_id ->
        analysis_id -> element label.

        Element identity arrives as `symbol` (HMI's own field name, e.g. "Au"); ours
        is `label` -- translated in place by _rename_element_symbol_to_label before
        anything else runs.

        Creating a brand-new MineralSite is deliberately NOT this method's job -- an
        unresolvable mineral_site_id is reported as an error (via the same
        IntegrityError create() already raises for an unknown FK), not created. New
        sites go through HMI's existing deposit-publish flow instead.
        """
        deposits = payload.get("deposits")
        if not isinstance(deposits, list):
            raise ArgumentError("`deposits` must be a list")

        created: list[dict] = []
        updated: list[dict] = []
        errors: list[dict] = []

        for d_i, deposit in enumerate(deposits):
            mineral_site_id = deposit.get("mineral_site_id")
            if not mineral_site_id:
                errors.append(
                    {
                        "path": f"deposits[{d_i}]",
                        "detail": "mineral_site_id is required",
                    }
                )
                continue

            samples = deposit.get("samples")
            if not isinstance(samples, list):
                errors.append(
                    {"path": f"deposits[{d_i}]", "detail": "`samples` must be a list"}
                )
                continue

            for s_i, sample_patch in enumerate(samples):
                path = f"deposits[{d_i}].samples[{s_i}]"
                sample_id = sample_patch.get("sample_id")
                if not sample_id:
                    errors.append({"path": path, "detail": "sample_id is required"})
                    continue

                self._rename_element_symbol_to_label(sample_patch)

                try:
                    self._validate_units(sample_patch)
                    self._validate_identity_keys(sample_patch)
                    public_id = make_sample_id(mineral_site_id, sample_id)
                    pre_existing = self.find_by_id(public_id)

                    if pre_existing is None:
                        new_sample = Sample.from_dict(
                            {
                                **sample_patch,
                                "public_id": "",
                                "mineral_site_id": mineral_site_id,
                                "modified_at": 0,
                            }
                        )
                        result = self.create(new_sample, user_uri)
                        created.append(
                            {
                                "sample_id": result.public_id,
                                "changed_properties": result.edit_history[
                                    -1
                                ].changed_properties,
                            }
                        )
                    else:
                        patch_only = {
                            k: v for k, v in sample_patch.items() if k != "sample_id"
                        }
                        result = self.patch(public_id, patch_only, user_uri)
                        if result.modified_at != pre_existing.modified_at:
                            updated.append(
                                {
                                    "sample_id": result.public_id,
                                    "changed_properties": result.edit_history[
                                        -1
                                    ].changed_properties,
                                }
                            )
                except IntegrityError:
                    errors.append(
                        {
                            "path": path,
                            "sample_id": sample_id,
                            "detail": (
                                f"mineral_site_id {mineral_site_id!r} does not exist -- "
                                "publish the deposit first"
                            ),
                        }
                    )
                except (ArgumentError, ExpiredSnapshotIdError, KeyError) as e:
                    errors.append(
                        {"path": path, "sample_id": sample_id, "detail": str(e)}
                    )

        return {"created": created, "updated": updated, "errors": errors}

    @staticmethod
    def _validate_units(sample_patch: dict) -> None:
        """Same check as the single-sample PATCH route's _validate_patch_units,
        reimplemented here (raising ArgumentError instead of HTTPException) so
        publish() can catch it per-sample rather than rejecting the whole batch.
        publish() bypasses that router helper entirely -- this path was silently
        unvalidated until this was added."""
        units = EntityService.get_instance().get_unit_uris()
        for a_i, a_patch in enumerate(sample_patch.get("analyses", [])):
            for e_i, e_patch in enumerate(a_patch.get("elements", [])):
                for unit_field in ("grade_unit", "detection_limit_unit"):
                    unit = e_patch.get(unit_field)
                    if unit and unit.get("normalized_uri") is not None:
                        if unit["normalized_uri"] not in units:
                            raise ArgumentError(
                                f"analyses[{a_i}].elements[{e_i}].{unit_field} has URI "
                                f"'{unit['normalized_uri']}' which is not in the allowed set"
                            )

    @staticmethod
    def _validate_identity_keys(sample_patch: dict) -> None:
        """patch() validates analysis_id/label presence inline when merging into an
        EXISTING sample -- but publish()'s create branch never calls patch() at all,
        it goes straight through Sample.from_dict()'s lenient parsing (Analysis/
        Element.from_dict() use dict.get(), so a missing identity key silently becomes
        None instead of erroring). Catches that gap for both branches by checking
        up front, before create()/patch() ever runs."""
        for a_i, a_patch in enumerate(sample_patch.get("analyses", [])):
            if not a_patch.get("analysis_id"):
                raise ArgumentError(f"analyses[{a_i}] requires analysis_id")
            for e_i, e_patch in enumerate(a_patch.get("elements", [])):
                if not e_patch.get("label"):
                    raise ArgumentError(
                        f"analyses[{a_i}].elements[{e_i}] requires label (or symbol)"
                    )

    @staticmethod
    def _rename_element_symbol_to_label(sample_patch: dict) -> None:
        """Mutates `sample_patch` in place. HMI's element identity field is
        `symbol` (e.g. "Au"); ours is `label` -- the one field-name gap between
        HMI's own shape and ours for this contract (issue #18 §4)."""
        for analysis in sample_patch.get("analyses", []):
            for elem in analysis.get("elements", []):
                if "symbol" in elem:
                    elem["label"] = elem.pop("symbol")

    @staticmethod
    def _changed_properties(old: dict, new: dict) -> list[str]:
        keys = (set(old.keys()) | set(new.keys())) - _NON_PROPERTY_FIELDS
        changed = sorted(k for k in keys if old.get(k) != new.get(k))
        return [NS_GCO.uristr(k) for k in changed]
