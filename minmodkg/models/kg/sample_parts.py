"""GeoChem sub-structures embedded within Sample: Element, Isotope, Analysis, EditEvent.

Deliberately kept in a separate module from sample.py (which defines the top-level
Sample entity). Sample needs minmodkg.transformations (for make_sample_id), which
transitively imports minmodkg.models.kgrel.base -- and kgrel.base needs to import
Analysis/EditEvent here for its type_annotation_map (they're embedded as JSON columns
on the Sample table, same as Reference/CandidateEntity/MineralInventory are for
MineralSite). Keeping them here, with no dependency on transformations.py, avoids that
circular import -- mirrors how Reference/CandidateEntity/MineralInventory each live in
their own file, separate from mineral_site.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Optional

from minmodkg.libraries.rdf.rdf_model import P, RDFModel, Subject
from minmodkg.misc.utils import makedict
from minmodkg.models.kg.base import NS_GCO, NS_GCR, NS_RDFS
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.typing import IRI, CleanedNotEmptyStr


@dataclass
class Element(RDFModel):
    __subj__ = Subject(type=NS_GCO.term("Element"), key_ns=NS_GCR)

    label: Annotated[CleanedNotEmptyStr, P(pred=NS_RDFS.term("label"))]
    grade: Annotated[Optional[float], P()] = None
    grade_unit: Annotated[Optional[CandidateEntity], P()] = None
    detection_limit: Annotated[Optional[float], P()] = None
    detection_limit_unit: Annotated[Optional[CandidateEntity], P()] = None

    def to_dict(self):
        return makedict.without_none(
            (
                ("label", self.label),
                ("grade", self.grade),
                (
                    "grade_unit",
                    self.grade_unit.to_dict() if self.grade_unit is not None else None,
                ),
                ("detection_limit", self.detection_limit),
                (
                    "detection_limit_unit",
                    (
                        self.detection_limit_unit.to_dict()
                        if self.detection_limit_unit is not None
                        else None
                    ),
                ),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> Element:
        return cls(
            label=d["label"],
            grade=d.get("grade"),
            grade_unit=(
                CandidateEntity.from_dict(d["grade_unit"])
                if d.get("grade_unit")
                else None
            ),
            detection_limit=d.get("detection_limit"),
            detection_limit_unit=(
                CandidateEntity.from_dict(d["detection_limit_unit"])
                if d.get("detection_limit_unit")
                else None
            ),
        )


@dataclass
class Isotope(RDFModel):
    __subj__ = Subject(type=NS_GCO.term("Isotope"), key_ns=NS_GCR)

    label: Annotated[CleanedNotEmptyStr, P(pred=NS_RDFS.term("label"))]

    def to_dict(self):
        return {"label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> Isotope:
        return cls(label=d["label"])


@dataclass
class Analysis(RDFModel):
    __subj__ = Subject(type=NS_GCO.term("Analysis"), key_ns=NS_GCR)

    analysis_id: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    analytical_method: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    instrument_type: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    laboratory_location: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    operating_conditions: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    standards_used: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    aggregation_method: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    data_quality: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    # ISO datetime string (xsd:dateTime)
    analysis_date: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    elements: Annotated[list[Element], P(pred=NS_GCO.term("element"))] = field(
        default_factory=list
    )
    isotopes: Annotated[list[Isotope], P(pred=NS_GCO.term("isotope"))] = field(
        default_factory=list
    )

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("analysis_id", self.analysis_id),
                ("analytical_method", self.analytical_method),
                ("instrument_type", self.instrument_type),
                ("laboratory_location", self.laboratory_location),
                ("operating_conditions", self.operating_conditions),
                ("standards_used", self.standards_used),
                ("aggregation_method", self.aggregation_method),
                ("data_quality", self.data_quality),
                ("analysis_date", self.analysis_date),
                ("elements", [e.to_dict() for e in self.elements]),
                ("isotopes", [i.to_dict() for i in self.isotopes]),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> Analysis:
        return cls(
            analysis_id=d.get("analysis_id"),
            analytical_method=d.get("analytical_method"),
            instrument_type=d.get("instrument_type"),
            laboratory_location=d.get("laboratory_location"),
            operating_conditions=d.get("operating_conditions"),
            standards_used=d.get("standards_used"),
            aggregation_method=d.get("aggregation_method"),
            data_quality=d.get("data_quality"),
            analysis_date=d.get("analysis_date"),
            elements=[Element.from_dict(e) for e in d.get("elements", [])],
            isotopes=[Isotope.from_dict(i) for i in d.get("isotopes", [])],
        )


@dataclass
class EditEvent(RDFModel):
    """One HMI save. Additive-only: a Sample accumulates one of these per save via
    its edit_history list -- never overwritten, so the full edit history survives
    (unlike MineralSite.created_by, which gets overwritten on every write)."""

    __subj__ = Subject(type=NS_GCO.term("EditEvent"), key_ns=NS_GCR)

    # MinMod user URI, e.g. https://minmod.isi.edu/users/u/{username} -- always
    # server-derived from the authenticated session, never client-supplied
    updated_by: Annotated[IRI, P()]
    # ISO datetime string (xsd:dateTime)
    updated_at: Annotated[CleanedNotEmptyStr, P()]
    # property URIs touched by this save, e.g. https://geochemistry.isi.edu/ontology/grade
    changed_properties: Annotated[list[IRI], P()] = field(default_factory=list)

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("updated_by", self.updated_by),
                ("updated_at", self.updated_at),
                ("changed_properties", self.changed_properties),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> EditEvent:
        return cls(
            updated_by=d["updated_by"],
            updated_at=d["updated_at"],
            changed_properties=d.get("changed_properties", []),
        )
