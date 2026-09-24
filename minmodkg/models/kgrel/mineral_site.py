from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Iterable, Optional

from minmodkg.grade_tonnage_model import GradeTonnageModel
from minmodkg.misc.utils import datetime_to_nanoseconds, format_nanoseconds, makedict
from minmodkg.models.kg.base import NS_MR
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.geology_info import GeologyInfo
from minmodkg.models.kg.mineral_inventory import MineralInventory
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kg.reference import Reference
from minmodkg.models.kgrel.base import Base
from minmodkg.models.kgrel.custom_types import Location, LocationView
from minmodkg.models.kgrel.views.mineral_inventory_view import MineralInventoryView
from minmodkg.transformations import get_source_uri
from minmodkg.typing import IRI, URN, InternalID
from rdflib import URIRef
from sqlalchemy import JSON, BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

if TYPE_CHECKING:
    pass


@dataclass
class MineralSiteAndInventory:
    ms: MineralSite
    invs: list[MineralInventoryView]

    def set_id(self, ms_id: int) -> MineralSiteAndInventory:
        self.ms.id = ms_id
        for inv in self.invs:
            inv.site_id = ms_id
        return self

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (("ms", self.ms.to_dict()), ("invs", [inv.to_dict() for inv in self.invs]))
        )

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            ms=MineralSite.from_dict(d["ms"]),
            invs=[MineralInventoryView.from_dict(inv) for inv in d.get("invs", [])],
        )

    @classmethod
    def from_raw_site(
        cls,
        raw_site: dict | KGMineralSite,
        commodity_form_conversion: dict[str, float],
        crs_names: dict[str, str],
        source_score: dict[IRI, float | None],
        dedup_site_id: Optional[str] = None,
    ) -> MineralSiteAndInventory:
        ms = MineralSite.from_raw_site(raw_site, crs_names, source_score)
        if dedup_site_id is not None:
            ms.dedup_site_id = dedup_site_id

        invs: dict[InternalID, list[GradeTonnageModel.MineralInventory]] = defaultdict(
            list
        )
        grade_tonnage_model = GradeTonnageModel()
        commodities = set()

        for inv_id, inv in enumerate(ms.inventories):
            if inv.commodity.normalized_uri is None:
                continue

            commodity = NS_MR.id(inv.commodity.normalized_uri)
            commodities.add(commodity)

            if (
                inv.ore is None
                or inv.ore.value is None
                or inv.ore.unit is None
                or inv.ore.unit.normalized_uri is None
                or inv.grade is None
                or inv.grade.value is None
                or inv.grade.unit is None
                or inv.grade.unit.normalized_uri is None
                or len(inv.category) == 0
            ):
                continue

            mi_form_conversion = None
            if (
                inv.material_form is not None
                and inv.material_form.normalized_uri is not None
            ):
                mi_form_conversion = commodity_form_conversion[
                    inv.material_form.normalized_uri
                ]

            invs[commodity].append(
                GradeTonnageModel.MineralInventory(
                    id=str(inv_id),
                    date=inv.date,
                    zone=inv.zone,
                    category=[
                        cat.normalized_uri
                        for cat in inv.category
                        if cat.normalized_uri is not None
                    ],
                    material_form_conversion=mi_form_conversion,
                    ore_value=inv.ore.value,
                    ore_unit=inv.ore.unit.normalized_uri,
                    grade_value=inv.grade.value,
                    grade_unit=inv.grade.unit.normalized_uri,
                )
            )

        inv_views = []
        for commodity, gt_invs in invs.items():
            grade_tonnage = grade_tonnage_model(gt_invs)

            if grade_tonnage is not None and grade_tonnage.total_estimate is not None:
                total_contained_metal = grade_tonnage.total_estimate.contained_metal
                total_tonnage = grade_tonnage.total_estimate.tonnage
                total_grade = grade_tonnage.total_estimate.get_grade()
            else:
                total_contained_metal = None
                total_tonnage = None
                total_grade = None

            inv_views.append(
                MineralInventoryView(
                    commodity=commodity,
                    contained_metal=total_contained_metal,
                    tonnage=total_tonnage,
                    grade=total_grade,
                    date=None,
                )
            )
        for comm in commodities:
            if comm not in invs:
                inv_views.append(
                    MineralInventoryView(
                        commodity=comm,
                        contained_metal=None,
                        tonnage=None,
                        grade=None,
                        date=None,
                    )
                )

        return cls(ms, inv_views)


class MineralSite(MappedAsDataclass, Base):
    __tablename__ = "mineral_site"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    site_id: Mapped[InternalID] = mapped_column(unique=True)
    dedup_site_id: Mapped[
        Annotated[InternalID, "Id of the mineral site that this site is the same as"]
    ] = mapped_column(
        ForeignKey("dedup_mineral_site.id", ondelete="SET NULL"), index=True
    )
    source_id: Mapped[URN] = mapped_column(index=True)
    source_score: Mapped[Optional[float]] = mapped_column()
    record_id: Mapped[str] = mapped_column(index=True)
    name: Mapped[str | None] = mapped_column()
    aliases: Mapped[list[str]] = mapped_column(JSON)
    rank: Mapped[str | None] = mapped_column()
    type: Mapped[str | None] = mapped_column()

    location: Mapped[Location | None] = mapped_column()
    location_view: Mapped[LocationView] = mapped_column()
    deposit_type_candidates: Mapped[list[CandidateEntity]] = mapped_column()
    inventories: Mapped[list[MineralInventory]] = mapped_column()
    reference: Mapped[list[Reference]] = mapped_column()

    mineral_form: Mapped[list[str]] = mapped_column(JSON)
    geology_info: Mapped[Optional[GeologyInfo]] = mapped_column()
    discovered_year: Mapped[int | None] = mapped_column()
    # Soft-delete, same convention as Sample -- see the GeoChem ontology.
    # No mapped_column default: created_by/modified_at below have none either,
    # and MappedAsDataclass requires defaulted fields to come last, so every
    # caller passes these explicitly (see from_raw_site, from_dict).
    is_deleted: Mapped[bool] = mapped_column()
    deleted_by: Mapped[str | None] = mapped_column()
    deleted_at: Mapped[str | None] = mapped_column()

    created_by: Mapped[IRI] = mapped_column()
    # timestamp in nano seconds
    modified_at: Mapped[int] = mapped_column(BigInteger)

    def set_id(self, id: int) -> MineralSite:
        self.id = id
        return self

    @property
    def site_uri(self) -> URIRef:
        return URIRef(KGMineralSite.__subj__.key_ns.uri(self.site_id))

    def has_dedup_site(self) -> bool:
        return self.dedup_site_id != ""

    @staticmethod
    def from_raw_site(
        raw_site: dict | KGMineralSite,
        crs_names: dict[str, str],
        source_score: dict[IRI, float | None],
    ) -> MineralSite:
        site = (
            KGMineralSite.from_dict(raw_site)
            if isinstance(raw_site, dict)
            else raw_site
        )
        location = None
        location_view = LocationView()

        if site.location_info is not None:
            location = Location(
                country=site.location_info.country,
                state_or_province=site.location_info.state_or_province,
                crs=site.location_info.crs,
                coordinates=site.location_info.location,
            )
            location_view = LocationView.from_location(location, crs_names)

        out_site = MineralSite(
            site_id=site.id,
            dedup_site_id="",  # raw site doesn't have dedup site id
            source_id=site.source_id,
            source_score=source_score.get(site.source_id),
            record_id=str(site.record_id),
            name=site.name,
            aliases=site.aliases,
            rank=site.site_rank,
            type=site.site_type,
            location=location,
            location_view=location_view,
            deposit_type_candidates=site.deposit_type_candidate,
            geology_info=site.geology_info,
            mineral_form=site.mineral_form,
            discovered_year=site.discovered_year,
            is_deleted=site.is_deleted,
            deleted_by=site.deleted_by,
            deleted_at=site.deleted_at,
            inventories=site.mineral_inventory,
            reference=site.reference,
            created_by=site.created_by,
            modified_at=datetime_to_nanoseconds(
                datetime.fromisoformat(site.modified_at)
            ),
        )
        return out_site

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("id", self.id),
                ("site_id", self.site_id),
                ("dedup_site_id", self.dedup_site_id),
                ("source_id", self.source_id),
                ("source_score", self.source_score),
                ("record_id", self.record_id),
                ("name", self.name),
                ("aliases", self.aliases),
                ("rank", self.rank),
                ("type", self.type),
                (
                    "location",
                    (self.location.to_dict() if self.location is not None else None),
                ),
                (
                    "location_view",
                    (
                        self.location_view.to_dict()
                        if not self.location_view.is_empty()
                        else None
                    ),
                ),
                (
                    "deposit_type_candidates",
                    [x.to_dict() for x in self.deposit_type_candidates],
                ),
                (
                    "inventories",
                    [x.to_dict() for x in self.inventories],
                ),
                ("reference", [x.to_dict() for x in self.reference]),
                (
                    "geology_info",
                    (
                        self.geology_info.to_dict()
                        if self.geology_info is not None
                        else None
                    ),
                ),
                ("mineral_form", self.mineral_form),
                ("discovered_year", self.discovered_year),
                ("is_deleted", self.is_deleted),
                ("deleted_by", self.deleted_by),
                ("deleted_at", self.deleted_at),
                ("created_by", self.created_by),
                ("modified_at", self.modified_at),
            )
        )

    @classmethod
    def from_dict(cls, d):
        obj = cls(
            site_id=d["site_id"],
            dedup_site_id=d["dedup_site_id"],
            source_id=d["source_id"],
            source_score=d.get("source_score"),
            record_id=d["record_id"],
            name=d.get("name"),
            aliases=d.get("aliases", []),
            rank=d.get("rank"),
            type=d.get("type"),
            location=Location.from_dict(d["location"]) if d.get("location") else None,
            location_view=LocationView.from_dict(d.get("location_view", {})),
            deposit_type_candidates=[
                CandidateEntity.from_dict(x)
                for x in d.get("deposit_type_candidates", [])
            ],
            inventories=[
                MineralInventory.from_dict(x) for x in d.get("inventories", [])
            ],
            geology_info=(
                GeologyInfo.from_dict(d["geology_info"])
                if d.get("geology_info")
                else None
            ),
            mineral_form=d.get("mineral_form", []),
            discovered_year=d.get("discovered_year"),
            is_deleted=d.get("is_deleted", False),
            deleted_by=d.get("deleted_by"),
            deleted_at=d.get("deleted_at"),
            reference=[Reference.from_dict(x) for x in d.get("reference", [])],
            created_by=d["created_by"],
            modified_at=d["modified_at"],
        )
        if "id" in d:
            obj.id = d["id"]
        return obj

    @staticmethod
    def get_dedup_id(site_ids: Iterable[InternalID]):
        return "dedup_" + min(site_ids)

    def to_kg(self) -> KGMineralSite:
        return KGMineralSite(
            source_id=self.source_id,
            record_id=self.record_id,
            name=self.name,
            aliases=self.aliases,
            site_rank=self.rank,
            site_type=self.type,
            mineral_form=self.mineral_form,
            geology_info=self.geology_info,
            location_info=self.location.to_kg() if self.location is not None else None,
            deposit_type_candidate=self.deposit_type_candidates,
            mineral_inventory=self.inventories,
            reference=self.reference,
            discovered_year=self.discovered_year,
            is_deleted=self.is_deleted,
            deleted_by=self.deleted_by,
            deleted_at=self.deleted_at,
            created_by=self.created_by,
            modified_at=format_nanoseconds(self.modified_at),
        )
