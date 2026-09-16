from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Optional

from minmodkg.libraries.rdf.rdf_model import P, RDFModel, Subject
from minmodkg.misc.utils import makedict
from minmodkg.models.kg.base import NS_GCO, NS_GCR, NS_MO
from minmodkg.models.kg.location_info import LocationInfo
from minmodkg.models.kg.reference import Reference
from minmodkg.models.kg.sample_parts import Analysis, EditEvent, Element, Isotope
from minmodkg.transformations import make_sample_id
from minmodkg.typing import IRI, CleanedNotEmptyStr, InternalID
from rdflib import URIRef

if TYPE_CHECKING:
    from minmodkg.models.kgrel.sample import Sample as RelSample

# re-exported for convenience so callers can `from minmodkg.models.kg.sample import
# Sample, Analysis, Element, Isotope, EditEvent` without knowing about the internal
# sample_parts.py split (which exists only to avoid a circular import, see its docstring)
__all__ = [
    "Sample",
    "SampleIdent",
    "Analysis",
    "Element",
    "Isotope",
    "EditEvent",
]


@dataclass
class SampleIdent(RDFModel):
    __subj__ = Subject(type=NS_GCO.term("Sample"), key_ns=NS_GCR, key="uri")

    # :sample_id is source-provided data (not guaranteed globally unique on its own),
    # so the actual id/uri is scoped by the parent mineral site -- see make_sample_id.
    sample_id: Annotated[CleanedNotEmptyStr, P()]

    # Parent mineral site's site_id. Deliberately NOT Annotated[..., P()] -- it is used
    # only to compute this Sample's id/uri below, not serialized as a triple on Sample
    # itself, since the :has_sample edge is asserted from the MineralSite side.
    mineral_site_id: InternalID

    @cached_property
    def uri(self) -> URIRef:
        return NS_GCR.uri(self.id)

    @cached_property
    def id(self) -> InternalID:
        return make_sample_id(self.mineral_site_id, self.sample_id)

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            sample_id=d["sample_id"],
            mineral_site_id=d["mineral_site_id"],
        )


@dataclass
class Sample(SampleIdent, RDFModel):
    __subj__ = Subject(type=NS_GCO.term("Sample"), key_ns=NS_GCR, key="uri")

    sample_name: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    sample_local_id: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    sample_type: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    collection_date: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    description: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    mineral: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    sampling_method: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    sample_preparation: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    material_class: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    material_class_comment: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    analysed_material: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    sample_deposit_relation: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    geological_province: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    strat_unit_uid: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    # Free text as reported -- distinct from strat_unit_uid (a normalized
    # identifier). Not a rename/replacement of it, see schema/geochem_v1.2.0.ttl.
    strat_unit_name: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    strat_grouping: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    earth_material_group: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    earth_material_qualifier: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    mode_occurrence: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    metamorphic_grade: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    alteration: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    paragenetic_stage: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    texture: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    color: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    associated_minerals: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    feature_type: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    feature_name: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    feature_local_uid: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    top_depth_m: Annotated[Optional[float], P()] = None
    bottom_depth_m: Annotated[Optional[float], P()] = None
    comments: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    # mo:location_info's domain widened to include :Sample (was mo:MineralSite
    # only) -- a sample can be reported at a more precise point than its parent
    # site. Reuses mo:LocationInfo as-is, same object MineralSite uses.
    location_info: Annotated[
        Optional[LocationInfo], P(pred=NS_MO.term("location_info"))
    ] = None
    # Soft-delete, same convention as Analysis/Element -- dedicated fields
    # rather than reading off edit_history, so who/when-deleted stays uniform
    # with MineralSite, which has no edit_history mechanism at all.
    is_deleted: Annotated[bool, P()] = False
    deleted_by: Annotated[Optional[IRI], P()] = None
    deleted_at: Annotated[Optional[CleanedNotEmptyStr], P()] = None

    analyses: Annotated[list[Analysis], P(pred=NS_GCO.term("has_analysis"))] = field(
        default_factory=list
    )
    # geochem's :reference sub-property (mo:reference widened to Sample/Analysis/Element)
    reference: Annotated[list[Reference], P(pred=NS_GCO.term("reference"))] = field(
        default_factory=list
    )
    edit_history: Annotated[
        list[EditEvent], P(pred=NS_GCO.term("edit_history"))
    ] = field(default_factory=list)

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("sample_id", self.sample_id),
                ("mineral_site_id", self.mineral_site_id),
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
                    "location_info",
                    self.location_info.to_dict() if self.location_info else None,
                ),
                ("is_deleted", self.is_deleted),
                ("deleted_by", self.deleted_by),
                ("deleted_at", self.deleted_at),
                ("analyses", [a.to_dict() for a in self.analyses]),
                ("reference", [r.to_dict() for r in self.reference]),
                ("edit_history", [e.to_dict() for e in self.edit_history]),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> Sample:
        return cls(
            sample_id=d["sample_id"],
            mineral_site_id=d["mineral_site_id"],
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
            location_info=(
                LocationInfo.from_dict(d["location_info"])
                if d.get("location_info")
                else None
            ),
            is_deleted=d.get("is_deleted", False),
            deleted_by=d.get("deleted_by"),
            deleted_at=d.get("deleted_at"),
            analyses=[Analysis.from_dict(a) for a in d.get("analyses", [])],
            reference=[Reference.from_dict(r) for r in d.get("reference", [])],
            edit_history=[EditEvent.from_dict(e) for e in d.get("edit_history", [])],
        )

    @classmethod
    def from_kgrel(cls, sample: RelSample) -> Sample:
        return cls(
            sample_id=sample.sample_id,
            mineral_site_id=sample.mineral_site_id,
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
            location_info=(
                sample.location.to_kg() if sample.location is not None else None
            ),
            is_deleted=sample.is_deleted,
            deleted_by=sample.deleted_by,
            deleted_at=sample.deleted_at,
            analyses=sample.analyses,
            reference=sample.reference,
            edit_history=sample.edit_history,
        )
