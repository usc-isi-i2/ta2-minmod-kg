from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any, Optional

from minmodkg.libraries.rdf.rdf_model import P, RDFModel, Subject
from minmodkg.misc.deserializer import get_dataclass_deserializer
from minmodkg.misc.utils import (
    NotEmptyListDeser,
    extend_unique,
    format_datetime,
    format_nanoseconds,
    makedict,
)
from minmodkg.models.kg.base import NS_MO, NS_MR, NS_RDFS
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.geology_info import GeologyInfo
from minmodkg.models.kg.location_info import LocationInfo
from minmodkg.models.kg.mineral_inventory import MineralInventory
from minmodkg.models.kg.reference import Reference
from minmodkg.models.kgrel.user import get_username
from minmodkg.transformations import make_site_id
from minmodkg.typing import IRI, CleanedNotEmptyStr, InternalID
from rdflib import URIRef

if TYPE_CHECKING:
    from minmodkg.models.kgrel.mineral_site import MineralSite as RelMineralSite


@dataclass
class MineralSiteIdent(RDFModel):
    __subj__ = Subject(type=NS_MO.term("MineralSite"), key_ns=NS_MR, key="uri")

    source_id: Annotated[CleanedNotEmptyStr, P()]
    record_id: Annotated[CleanedNotEmptyStr, P()]
    created_by: Annotated[CleanedNotEmptyStr, P()]

    @cached_property
    def uri(self) -> URIRef:
        return NS_MR.uri(self.id)

    @cached_property
    def id(self) -> InternalID:
        return make_site_id(
            get_username(self.created_by), self.source_id, self.record_id
        )

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            source_id=d["source_id"],
            record_id=d["record_id"],
            created_by=d["created_by"],
        )


@dataclass
class MineralSite(MineralSiteIdent, RDFModel):
    __subj__ = Subject(type=NS_MO.term("MineralSite"), key_ns=NS_MR, key="uri")

    name: Annotated[Optional[CleanedNotEmptyStr], P(pred=NS_RDFS.term("label"))] = None
    aliases: Annotated[list[CleanedNotEmptyStr], P(is_list=True)] = field(
        default_factory=list
    )
    site_rank: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    site_type: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    mineral_form: Annotated[list[CleanedNotEmptyStr], P(is_list=True)] = field(
        default_factory=list
    )
    geology_info: Annotated[Optional[GeologyInfo], P()] = None
    location_info: Annotated[Optional[LocationInfo], P()] = None
    deposit_type_candidate: Annotated[list[CandidateEntity], P()] = field(
        default_factory=list
    )
    mineral_inventory: Annotated[list[MineralInventory], P()] = field(
        default_factory=list
    )
    reference: Annotated[list[Reference], P()] = field(default_factory=list)
    discovered_year: Annotated[Optional[int], P()] = None
    # Soft-delete, same convention as :Sample/:Analysis/:Element -- see
    # schema/geochem_v1.2.1.ttl. deleted_by/deleted_at are server-derived from
    # the session, never client-supplied.
    is_deleted: Annotated[bool, P()] = False
    deleted_by: Annotated[Optional[IRI], P()] = None
    deleted_at: Annotated[Optional[CleanedNotEmptyStr], P()] = None

    modified_at: Annotated[
        Annotated[str, "Datetime with %Y-%m-%dT%H:%M:%S.%fZ format"], P()
    ] = field(default_factory=lambda: format_datetime(datetime.now(timezone.utc)))

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("source_id", self.source_id),
                ("record_id", self.record_id),
                ("name", self.name),
                ("aliases", self.aliases),
                ("site_rank", self.site_rank),
                ("site_type", self.site_type),
                ("mineral_form", self.mineral_form),
                (
                    "geology_info",
                    (
                        self.geology_info.to_dict()
                        if self.geology_info is not None
                        else None
                    ),
                ),
                (
                    "location_info",
                    (
                        self.location_info.to_dict()
                        if self.location_info is not None
                        else None
                    ),
                ),
                (
                    "deposit_type_candidate",
                    [x.to_dict() for x in self.deposit_type_candidate],
                ),
                ("mineral_inventory", [x.to_dict() for x in self.mineral_inventory]),
                ("reference", [x.to_dict() for x in self.reference]),
                ("discovered_year", self.discovered_year),
                ("is_deleted", self.is_deleted),
                ("deleted_by", self.deleted_by),
                ("deleted_at", self.deleted_at),
                ("created_by", self.created_by),
                ("modified_at", self.modified_at),
            )
        )

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            source_id=d["source_id"],
            record_id=d["record_id"],
            name=d.get("name"),
            aliases=d.get("aliases", []),
            site_rank=d.get("site_rank"),
            site_type=d.get("site_type"),
            mineral_form=d.get("mineral_form", []),
            geology_info=(
                GeologyInfo.from_dict(d["geology_info"])
                if d.get("geology_info")
                else None
            ),
            location_info=(
                LocationInfo.from_dict(d["location_info"])
                if d.get("location_info")
                else None
            ),
            deposit_type_candidate=[
                CandidateEntity.from_dict(x)
                for x in d.get("deposit_type_candidate", [])
            ],
            mineral_inventory=[
                MineralInventory.from_dict(x) for x in d.get("mineral_inventory", [])
            ],
            reference=[Reference.from_dict(x) for x in d.get("reference", [])],
            discovered_year=d.get("discovered_year"),
            is_deleted=d.get("is_deleted", False),
            deleted_by=d.get("deleted_by"),
            deleted_at=d.get("deleted_at"),
            created_by=d["created_by"],
            modified_at=d["modified_at"],
        )

    def merge_mut(self, other: MineralSite):
        """Merge another site into this site almost as how knowledge graph does. This behaves similar to convert these sites into triples and merge the triples
        except that field that is expected to be single value will not be overwritten.
        """
        if self.name is None:
            self.name = other.name
        self.aliases = extend_unique(self.aliases, other.aliases)
        if self.site_rank is None:
            self.site_rank = other.site_rank
        if self.site_type is None:
            self.site_type = other.site_type
        if self.location_info is None:
            self.location_info = other.location_info
        elif other.location_info is not None:
            self.location_info.merge_mut(other.location_info)

        self.deposit_type_candidate = extend_unique(
            self.deposit_type_candidate,
            other.deposit_type_candidate,
            key_fn=CandidateEntity.to_tuple,
        )

        self.mineral_inventory.extend(other.mineral_inventory)
        self.reference = Reference.dedup(self.reference + other.reference)
        assert self.created_by == other.created_by
        self.modified_at = max(self.modified_at, other.modified_at)

    @classmethod
    def from_kgrel(cls, site: RelMineralSite):
        return cls(
            source_id=site.source_id,
            record_id=site.record_id,
            name=site.name,
            aliases=site.aliases,
            site_rank=site.rank,
            site_type=site.type,
            location_info=(
                LocationInfo(
                    country=site.location.country,
                    state_or_province=site.location.state_or_province,
                    crs=site.location.crs,
                    location=site.location.coordinates,
                )
                if site.location is not None
                else None
            ),
            deposit_type_candidate=site.deposit_type_candidates,
            mineral_inventory=site.inventories,
            reference=site.reference,
            is_deleted=site.is_deleted,
            deleted_by=site.deleted_by,
            deleted_at=site.deleted_at,
            created_by=site.created_by,
            modified_at=format_nanoseconds(site.modified_at),
        )


class MineralSiteValidator:
    structure_validator = get_dataclass_deserializer(MineralSite)
    # structure_validator = get_dataclass_deserializer(InputPublicMineralSite)

    @classmethod
    def validate(cls, mineral_site: Any):
        return cls.structure_validator(mineral_site)
