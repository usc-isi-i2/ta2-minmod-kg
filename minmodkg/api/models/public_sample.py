from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from minmodkg.misc.utils import format_nanoseconds, makedict
from minmodkg.models.kg.reference import Reference
from minmodkg.models.kg.sample import Analysis, EditEvent
from minmodkg.models.kgrel.custom_types import Location
from minmodkg.models.kgrel.sample import Sample as RelSample
from minmodkg.typing import InternalID
from pydantic import BaseModel, Field


class OutputPublicSample(BaseModel):
    id: InternalID  # the computed public_id (URI slug), not the raw :sample_id
    mineral_site_id: InternalID
    sample_id: Optional[str] = None
    sample_name: Optional[str] = None
    sample_local_id: Optional[str] = None
    sample_type: Optional[str] = None
    collection_date: Optional[str] = None
    description: Optional[str] = None
    mineral: Optional[str] = None
    sampling_method: Optional[str] = None
    sample_preparation: Optional[str] = None
    material_class: Optional[str] = None
    material_class_comment: Optional[str] = None
    analysed_material: Optional[str] = None
    sample_deposit_relation: Optional[str] = None
    geological_province: Optional[str] = None
    strat_unit_uid: Optional[str] = None
    strat_unit_name: Optional[str] = None
    strat_grouping: Optional[str] = None
    earth_material_group: Optional[str] = None
    earth_material_qualifier: Optional[str] = None
    mode_occurrence: Optional[str] = None
    metamorphic_grade: Optional[str] = None
    alteration: Optional[str] = None
    paragenetic_stage: Optional[str] = None
    texture: Optional[str] = None
    color: Optional[str] = None
    associated_minerals: Optional[str] = None
    feature_type: Optional[str] = None
    feature_name: Optional[str] = None
    feature_local_uid: Optional[str] = None
    top_depth_m: Optional[float] = None
    bottom_depth_m: Optional[float] = None
    comments: Optional[str] = None
    location: Optional[Location] = None
    is_deleted: bool = False
    deleted_by: Optional[str] = None
    deleted_at: Optional[str] = None

    analyses: list[Analysis] = Field(default_factory=list)
    reference: list[Reference] = Field(default_factory=list)
    edit_history: list[EditEvent] = Field(default_factory=list)

    modified_at: str
    # optimistic-concurrency token for PUT /samples/{id}?snapshot_id=..., same
    # convention as OutputPublicMineralSite (the row's own modified_at, stringified)
    snapshot_id: str = ""

    model_config = {"arbitrary_types_allowed": True}

    @staticmethod
    def from_kgrel(sample: RelSample) -> OutputPublicSample:
        return OutputPublicSample(
            id=sample.public_id,
            mineral_site_id=sample.mineral_site_id,
            sample_id=sample.sample_id,
            sample_name=sample.sample_name,
            sample_local_id=sample.sample_local_id,
            sample_type=sample.sample_type,
            collection_date=sample.collection_date,
            description=sample.description,
            mineral=sample.mineral,
            sampling_method=sample.sampling_method,
            sample_preparation=sample.sample_preparation,
            material_class=sample.material_class,
            material_class_comment=sample.material_class_comment,
            analysed_material=sample.analysed_material,
            sample_deposit_relation=sample.sample_deposit_relation,
            geological_province=sample.geological_province,
            strat_unit_uid=sample.strat_unit_uid,
            strat_unit_name=sample.strat_unit_name,
            strat_grouping=sample.strat_grouping,
            earth_material_group=sample.earth_material_group,
            earth_material_qualifier=sample.earth_material_qualifier,
            mode_occurrence=sample.mode_occurrence,
            metamorphic_grade=sample.metamorphic_grade,
            alteration=sample.alteration,
            paragenetic_stage=sample.paragenetic_stage,
            texture=sample.texture,
            color=sample.color,
            associated_minerals=sample.associated_minerals,
            feature_type=sample.feature_type,
            feature_name=sample.feature_name,
            feature_local_uid=sample.feature_local_uid,
            top_depth_m=sample.top_depth_m,
            bottom_depth_m=sample.bottom_depth_m,
            comments=sample.comments,
            location=sample.location,
            is_deleted=sample.is_deleted,
            deleted_by=sample.deleted_by,
            deleted_at=sample.deleted_at,
            analyses=sample.analyses,
            reference=sample.reference,
            edit_history=sample.edit_history,
            modified_at=format_nanoseconds(sample.modified_at),
            snapshot_id=str(sample.modified_at),
        )

    def to_dict(self):
        return self.model_dump(exclude_none=True)


@dataclass
class InputPublicSample:
    """The client-supplied payload for POST/PUT /samples.

    Deliberately has NO edit_history field -- unlike created_by on MineralSite, we
    don't even accept a client-supplied value to override; SampleService builds the
    real EditEvent entirely server-side (identity from the authenticated session,
    changed_properties from an actual diff against the DB), see to_kgrel() below.
    """

    mineral_site_id: InternalID
    sample_id: Optional[str] = None
    sample_name: Optional[str] = None
    sample_local_id: Optional[str] = None
    sample_type: Optional[str] = None
    collection_date: Optional[str] = None
    description: Optional[str] = None
    mineral: Optional[str] = None
    sampling_method: Optional[str] = None
    sample_preparation: Optional[str] = None
    material_class: Optional[str] = None
    material_class_comment: Optional[str] = None
    analysed_material: Optional[str] = None
    sample_deposit_relation: Optional[str] = None
    geological_province: Optional[str] = None
    strat_unit_uid: Optional[str] = None
    strat_unit_name: Optional[str] = None
    strat_grouping: Optional[str] = None
    earth_material_group: Optional[str] = None
    earth_material_qualifier: Optional[str] = None
    mode_occurrence: Optional[str] = None
    metamorphic_grade: Optional[str] = None
    alteration: Optional[str] = None
    paragenetic_stage: Optional[str] = None
    texture: Optional[str] = None
    color: Optional[str] = None
    associated_minerals: Optional[str] = None
    feature_type: Optional[str] = None
    feature_name: Optional[str] = None
    feature_local_uid: Optional[str] = None
    top_depth_m: Optional[float] = None
    bottom_depth_m: Optional[float] = None
    comments: Optional[str] = None
    location: Optional[Location] = None
    # deleted_by/deleted_at are never accepted from the client -- server-derived
    # by SampleService.create()/update(), same convention as edit_history above.
    is_deleted: bool = False

    analyses: list[Analysis] = field(default_factory=list)
    reference: list[Reference] = field(default_factory=list)

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("mineral_site_id", self.mineral_site_id),
                ("sample_id", self.sample_id),
                ("sample_name", self.sample_name),
                ("sample_local_id", self.sample_local_id),
                ("sample_type", self.sample_type),
                ("collection_date", self.collection_date),
                ("description", self.description),
                ("mineral", self.mineral),
                ("sampling_method", self.sampling_method),
                ("sample_preparation", self.sample_preparation),
                ("material_class", self.material_class),
                ("material_class_comment", self.material_class_comment),
                ("analysed_material", self.analysed_material),
                ("sample_deposit_relation", self.sample_deposit_relation),
                ("geological_province", self.geological_province),
                ("strat_unit_uid", self.strat_unit_uid),
                ("strat_unit_name", self.strat_unit_name),
                ("strat_grouping", self.strat_grouping),
                ("earth_material_group", self.earth_material_group),
                ("earth_material_qualifier", self.earth_material_qualifier),
                ("mode_occurrence", self.mode_occurrence),
                ("metamorphic_grade", self.metamorphic_grade),
                ("alteration", self.alteration),
                ("paragenetic_stage", self.paragenetic_stage),
                ("texture", self.texture),
                ("color", self.color),
                ("associated_minerals", self.associated_minerals),
                ("feature_type", self.feature_type),
                ("feature_name", self.feature_name),
                ("feature_local_uid", self.feature_local_uid),
                ("top_depth_m", self.top_depth_m),
                ("bottom_depth_m", self.bottom_depth_m),
                ("comments", self.comments),
                (
                    "location",
                    self.location.to_dict() if self.location is not None else None,
                ),
                ("is_deleted", self.is_deleted),
                ("analyses", [a.to_dict() for a in self.analyses]),
                ("reference", [r.to_dict() for r in self.reference]),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> InputPublicSample:
        return cls(
            mineral_site_id=d["mineral_site_id"],
            sample_id=d.get("sample_id"),
            sample_name=d.get("sample_name"),
            sample_local_id=d.get("sample_local_id"),
            sample_type=d.get("sample_type"),
            collection_date=d.get("collection_date"),
            description=d.get("description"),
            mineral=d.get("mineral"),
            sampling_method=d.get("sampling_method"),
            sample_preparation=d.get("sample_preparation"),
            material_class=d.get("material_class"),
            material_class_comment=d.get("material_class_comment"),
            analysed_material=d.get("analysed_material"),
            sample_deposit_relation=d.get("sample_deposit_relation"),
            geological_province=d.get("geological_province"),
            strat_unit_uid=d.get("strat_unit_uid"),
            strat_unit_name=d.get("strat_unit_name"),
            strat_grouping=d.get("strat_grouping"),
            earth_material_group=d.get("earth_material_group"),
            earth_material_qualifier=d.get("earth_material_qualifier"),
            mode_occurrence=d.get("mode_occurrence"),
            metamorphic_grade=d.get("metamorphic_grade"),
            alteration=d.get("alteration"),
            paragenetic_stage=d.get("paragenetic_stage"),
            texture=d.get("texture"),
            color=d.get("color"),
            associated_minerals=d.get("associated_minerals"),
            feature_type=d.get("feature_type"),
            feature_name=d.get("feature_name"),
            feature_local_uid=d.get("feature_local_uid"),
            top_depth_m=d.get("top_depth_m"),
            bottom_depth_m=d.get("bottom_depth_m"),
            comments=d.get("comments"),
            location=Location.from_dict(d["location"]) if d.get("location") else None,
            is_deleted=d.get("is_deleted", False),
            analyses=[Analysis.from_dict(a) for a in d.get("analyses", [])],
            reference=[Reference.from_dict(r) for r in d.get("reference", [])],
        )

    def to_kgrel(self) -> RelSample:
        """Builds a RelSample with public_id/edit_history/modified_at left as
        placeholders -- SampleService.create()/update() fill those in, since none of
        them can be correctly derived from the client payload alone (public_id needs
        the request to be validated first, edit_history's identity must come from the
        authenticated session, and changed_properties needs a diff against the DB)."""
        return RelSample(
            public_id="",
            mineral_site_id=self.mineral_site_id,
            sample_id=self.sample_id,
            sample_name=self.sample_name,
            sample_local_id=self.sample_local_id,
            sample_type=self.sample_type,
            collection_date=self.collection_date,
            description=self.description,
            mineral=self.mineral,
            sampling_method=self.sampling_method,
            sample_preparation=self.sample_preparation,
            material_class=self.material_class,
            material_class_comment=self.material_class_comment,
            analysed_material=self.analysed_material,
            sample_deposit_relation=self.sample_deposit_relation,
            geological_province=self.geological_province,
            strat_unit_uid=self.strat_unit_uid,
            strat_unit_name=self.strat_unit_name,
            strat_grouping=self.strat_grouping,
            earth_material_group=self.earth_material_group,
            earth_material_qualifier=self.earth_material_qualifier,
            mode_occurrence=self.mode_occurrence,
            metamorphic_grade=self.metamorphic_grade,
            alteration=self.alteration,
            paragenetic_stage=self.paragenetic_stage,
            texture=self.texture,
            color=self.color,
            associated_minerals=self.associated_minerals,
            feature_type=self.feature_type,
            feature_name=self.feature_name,
            feature_local_uid=self.feature_local_uid,
            top_depth_m=self.top_depth_m,
            bottom_depth_m=self.bottom_depth_m,
            comments=self.comments,
            location=self.location,
            # deleted_by/deleted_at are placeholders -- SampleService.create()/
            # update() stamp the real values from is_deleted (see _stamp_deletion).
            is_deleted=self.is_deleted,
            deleted_by=None,
            deleted_at=None,
            analyses=self.analyses,
            reference=self.reference,
            edit_history=[],
            modified_at=0,
        )
