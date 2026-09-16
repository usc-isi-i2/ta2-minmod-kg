from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Optional, Union

from fastapi import HTTPException, status
from minmodkg.misc.utils import (
    format_datetime,
    format_nanoseconds,
    is_valid_url,
    makedict,
)
from minmodkg.models.kg.base import MINMOD_NS
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.geology_info import GeologyInfo
from minmodkg.models.kg.location_info import LocationInfo
from minmodkg.models.kg.mineral_inventory import MineralInventory
from minmodkg.models.kg.mineral_site import MineralSite as InputMineralSite
from minmodkg.models.kg.reference import Reference
from minmodkg.models.kgrel.mineral_site import MineralSite, MineralSiteAndInventory
from minmodkg.models.kgrel.user import User
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.typing import IRI, InternalID
from pydantic import BaseModel, Field


class GradeTonnage(BaseModel):
    commodity: InternalID
    total_contained_metal: Optional[float] = None
    total_tonnage: Optional[float] = None
    total_grade: Optional[float] = None
    date: Optional[str] = None


class Coordinates(BaseModel):
    lat: Annotated[float, "Latitude"]
    lon: Annotated[float, "Longitude"]


class OutputPublicMineralSite(BaseModel):
    id: InternalID
    source_id: str
    record_id: Union[str, int]
    dedup_site_uri: Optional[IRI]
    name: Optional[str]
    created_by: IRI
    aliases: list[str]
    site_rank: Optional[str] = None
    site_type: Optional[str] = None
    location_info: Optional[LocationInfo] = None
    deposit_type_candidate: list[CandidateEntity] = Field(default_factory=list)
    mineral_form: list[str] = Field(default_factory=list)
    geology_info: Optional[GeologyInfo] = None
    mineral_inventory: list[MineralInventory] = Field(default_factory=list)
    discovered_year: Optional[int] = None
    reference: list[Reference] = Field(default_factory=list)
    is_deleted: bool = False
    deleted_by: Optional[IRI] = None
    deleted_at: Optional[str] = None
    modified_at: str = Field(
        default_factory=lambda: format_datetime(datetime.now(timezone.utc))
    )

    coordinates: Optional[Coordinates] = None
    grade_tonnage: list[GradeTonnage] = Field(default_factory=list)
    snapshot_id: str = ""

    @staticmethod
    def from_kgrel(site: MineralSiteAndInventory):
        ms = site.ms
        return OutputPublicMineralSite(
            id=ms.site_id,
            source_id=ms.source_id,
            record_id=ms.record_id,
            dedup_site_uri=MINMOD_NS.md.uristr(ms.dedup_site_id),
            name=ms.name,
            created_by=ms.created_by,
            aliases=ms.aliases,
            site_rank=ms.rank,
            site_type=ms.type,
            location_info=(
                LocationInfo(
                    country=ms.location.country,
                    state_or_province=ms.location.state_or_province,
                    crs=ms.location.crs,
                    location=ms.location.coordinates,
                )
                if ms.location is not None
                else None
            ),
            deposit_type_candidate=ms.deposit_type_candidates,
            geology_info=ms.geology_info,
            mineral_form=ms.mineral_form,
            mineral_inventory=ms.inventories,
            discovered_year=ms.discovered_year,
            reference=ms.reference,
            is_deleted=ms.is_deleted,
            deleted_by=ms.deleted_by,
            deleted_at=ms.deleted_at,
            modified_at=format_nanoseconds(ms.modified_at),
            coordinates=(
                Coordinates(
                    lat=ms.location_view.lat,
                    lon=ms.location_view.lon,
                )
                if ms.location_view.lat is not None and ms.location_view.lon is not None
                else None
            ),
            grade_tonnage=[
                GradeTonnage(
                    commodity=gt.commodity,
                    total_contained_metal=gt.contained_metal,
                    total_tonnage=gt.tonnage,
                    total_grade=gt.grade,
                    date=gt.date,
                )
                for gt in site.invs
            ],
            snapshot_id=str(ms.modified_at),
        )

    def clone(self):
        return self.model_copy()

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("id", self.id),
                ("source_id", self.source_id),
                ("record_id", self.record_id),
                ("dedup_site_uri", self.dedup_site_uri),
                ("name", self.name),
                ("created_by", self.created_by),
                ("aliases", self.aliases),
                ("site_rank", self.site_rank),
                ("site_type", self.site_type),
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
                    [c.to_dict() for c in self.deposit_type_candidate],
                ),
                ("mineral_form", self.mineral_form),
                (
                    "geology_info",
                    self.geology_info.to_dict() if self.geology_info else None,
                ),
                ("mineral_inventory", [v.to_dict() for v in self.mineral_inventory]),
                ("discovered_year", self.discovered_year),
                ("reference", [r.to_dict() for r in self.reference]),
                ("is_deleted", self.is_deleted),
                ("deleted_by", self.deleted_by),
                ("deleted_at", self.deleted_at),
                ("modified_at", self.modified_at),
                (
                    "coordinates",
                    self.coordinates if self.coordinates is not None else None,
                ),
                (
                    "grade_tonnage",
                    [gt.model_dump(exclude_none=True) for gt in self.grade_tonnage],
                ),
                (
                    "snapshot_id",
                    self.snapshot_id,
                ),
            )
        )


@dataclass
class InputPublicMineralSite(InputMineralSite):
    dedup_site_uri: Optional[IRI] = None

    def to_kgrel(
        self,
        user_uri: str,
    ) -> MineralSiteAndInventory:
        entser = EntityService.get_instance()
        site = MineralSiteAndInventory.from_raw_site(
            self,
            commodity_form_conversion=entser.get_commodity_form_conversion(),
            crs_names=entser.get_crs_name(),
            source_score=entser.get_data_source_score(),
            dedup_site_id=(
                MINMOD_NS.md.id(self.dedup_site_uri)
                if self.dedup_site_uri is not None
                else None
            ),
        )
        site.ms.modified_at = time.time_ns()
        site.ms.created_by = user_uri
        # deleted_by/deleted_at are server-derived, never trusted from the
        # caller -- same overwrite-every-write convention as created_by above
        # (MineralSite has no edit_history to check "did is_deleted actually
        # change" against, unlike Sample's more careful patch()).
        if site.ms.is_deleted:
            site.ms.deleted_by = user_uri
            site.ms.deleted_at = format_datetime(datetime.now(timezone.utc))
        else:
            site.ms.deleted_by = None
            site.ms.deleted_at = None
        return site

    def to_dict(self):
        d = super().to_dict()
        if self.dedup_site_uri is not None:
            d["dedup_site_uri"] = self.dedup_site_uri
        return d


class UpdateDedupLink(BaseModel):
    """A class represents the latest dedup links"""

    sites: list[InternalID]
