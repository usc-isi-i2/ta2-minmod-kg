from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence, TypedDict

from minmodkg.misc.utils import makedict
from minmodkg.models.kg.base import MINMOD_NS
from minmodkg.models.kgrel.base import Base, GeologyInfo
from minmodkg.models.kgrel.custom_types import (
    DedupMineralSiteDepositType,
    GeoCoordinate,
    RefGeoCoordinate,
    RefListID,
    RefValue,
    SiteAndScore,
    SiteScore,
)
from minmodkg.models.kgrel.custom_types.ref_value import (
    RefDepositType,
    RefGeologyInfo,
    RefListStr,
)
from minmodkg.models.kgrel.mineral_site import (
    MineralInventoryView,
    MineralSite,
    MineralSiteAndInventory,
)
from minmodkg.models.kgrel.views.mineral_inventory_view import DedupMineralInventoryView
from minmodkg.typing import InternalID
from sqlalchemy import TEXT, VARCHAR, BigInteger, ColumnElement, Index, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, MappedAsDataclass, composite, mapped_column


@dataclass
class DedupMineralSiteAndInventory:
    dms: DedupMineralSite
    invs: list[DedupMineralInventoryView]


class DedupMineralSite(MappedAsDataclass, Base):
    __tablename__ = "dedup_mineral_site"
    # GIN, so the array containment filters below can use an index
    __table_args__ = (
        Index(
            "ix_dedup_mineral_site_country_val_gin",
            "country_val",
            postgresql_using="gin",
        ),
        Index(
            "ix_dedup_mineral_site_state_or_province_val_gin",
            "state_or_province_val",
            postgresql_using="gin",
        ),
    )

    id: Mapped[InternalID] = mapped_column(primary_key=True)
    name: Mapped[Optional[RefValue[str]]] = composite(
        RefValue.as_composite,
        mapped_column("name_val", String, nullable=True),
        mapped_column("name_refid", String, nullable=True),
    )
    type: Mapped[Optional[RefValue[str]]] = composite(
        RefValue.as_composite,
        mapped_column("type_val", String, nullable=True),
        mapped_column("type_refid", String, nullable=True),
    )
    rank: Mapped[Optional[RefValue[str]]] = composite(
        RefValue.as_composite,
        mapped_column("rank_val", String, nullable=True),
        mapped_column("rank_refid", String, nullable=True),
    )

    # for searching only
    top1_deposit_type: Mapped[Optional[InternalID]] = mapped_column(index=True)

    ranked_deposit_types: Mapped[list[RefDepositType]] = mapped_column()

    coordinates: Mapped[Optional[RefGeoCoordinate]] = mapped_column()
    country: Mapped[RefListID] = composite(
        mapped_column("country_val", ARRAY(VARCHAR(7)), index=True),
        mapped_column(
            "country_refid",
            String,
        ),
    )
    state_or_province: Mapped[RefListID] = composite(
        mapped_column("state_or_province_val", ARRAY(VARCHAR(7)), index=True),
        mapped_column(
            "state_or_province_refid",
            String,
        ),
    )
    mineral_form: Mapped[RefListStr] = mapped_column()
    geology_info: Mapped[RefGeologyInfo] = mapped_column()
    discovered_year: Mapped[Optional[RefValue[int]]] = mapped_column()

    ranked_sites: Mapped[list[SiteAndScore]] = mapped_column()
    modified_at: Mapped[int] = mapped_column(BigInteger)

    @staticmethod
    def has_country(country: InternalID) -> ColumnElement[bool]:
        return DedupMineralSite.country._comparable_elements[0].contains([country])

    @staticmethod
    def has_state_or_province(state: InternalID) -> ColumnElement[bool]:
        return DedupMineralSite.state_or_province._comparable_elements[0].contains(
            [state]
        )

    @staticmethod
    def from_dedup_sites(
        dedup_sites: list[DedupMineralSite],
        sites: Sequence[MineralSiteAndInventory],
        *,
        is_site_ranked: bool,
    ) -> DedupMineralSiteAndInventory:
        assert is_site_ranked, "Only support merging ranked sites"
        rank_dedup_sites = sorted(
            (
                (
                    dedup_site,
                    dedup_site.ranked_sites[0].score,
                )
                for dedup_site in dedup_sites
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        ms2score = {
            ms.site_id: ms.score for dms in dedup_sites for ms in dms.ranked_sites
        }

        _tmp_deposit_types: dict[str, RefDepositType] = {}
        for dedup_site in dedup_sites:
            for dt in dedup_site.ranked_deposit_types:
                if dt.value.id not in _tmp_deposit_types or (
                    dt.value.confidence,
                    ms2score[dt.refid],
                ) > (
                    _tmp_deposit_types[dt.value.id].value.confidence,
                    ms2score[_tmp_deposit_types[dt.value.id].refid],
                ):
                    _tmp_deposit_types[dt.value.id] = dt

        ranked_deposit_types = sorted(
            _tmp_deposit_types.values(),
            key=lambda x: (x.value.confidence, ms2score[x.refid]),
            reverse=True,
        )[:5]
        if len(ranked_deposit_types) > 0:
            top1_deposit_type = ranked_deposit_types[0].value.id
        else:
            top1_deposit_type = None

        merged_dedup_site = DedupMineralSite(
            id=dedup_sites[0].id,
            name=next(
                (site.name for site, _ in rank_dedup_sites if site.name is not None),
                None,
            ),
            rank=next(
                (site.rank for site, _ in rank_dedup_sites if site.rank is not None),
                None,
            ),
            type=next(
                (site.type for site, _ in rank_dedup_sites if site.type is not None),
                None,
            ),
            ranked_deposit_types=ranked_deposit_types,
            top1_deposit_type=top1_deposit_type,
            coordinates=next(
                (
                    site.coordinates
                    for site, _ in rank_dedup_sites
                    if site.coordinates is not None
                ),
                None,
            ),
            country=next(
                (
                    site.country
                    for site, _ in rank_dedup_sites
                    if len(site.country.value) > 0
                ),
                dedup_sites[0].country,
            ),
            state_or_province=next(
                (
                    site.state_or_province
                    for site, _ in rank_dedup_sites
                    if len(site.state_or_province.value) > 0
                ),
                dedup_sites[0].state_or_province,
            ),
            mineral_form=next(
                (
                    site.mineral_form
                    for site, _ in rank_dedup_sites
                    if len(site.mineral_form.value) > 0
                ),
                dedup_sites[0].mineral_form,
            ),
            geology_info=RefGeologyInfo.from_dedup_sites(dedup_sites),
            discovered_year=next(
                (
                    site.discovered_year
                    for site, _ in rank_dedup_sites
                    if site.discovered_year is not None
                ),
                dedup_sites[0].discovered_year,
            ),
            ranked_sites=sorted(
                (ms for dms in dedup_sites for ms in dms.ranked_sites),
                key=lambda x: x.score,
                reverse=True,
            ),
            modified_at=max(dedup_site.modified_at for dedup_site in dedup_sites),
        )
        # As in from_sites: prefer the highest-ranked partial merge whose country and
        # state come from one record; the two scans above stay as the fallback.
        both = next(
            (
                site
                for site, _ in rank_dedup_sites
                if len(site.country.value) > 0
                and len(site.state_or_province.value) > 0
                and site.country.refid == site.state_or_province.refid
            ),
            None,
        )
        if both is not None:
            merged_dedup_site.country = both.country
            merged_dedup_site.state_or_province = both.state_or_province
        merged_dedup_invs = merged_dedup_site.select_inventories(
            {msi.ms.site_id: msi.invs for msi in sites}
        )
        return DedupMineralSiteAndInventory(merged_dedup_site, merged_dedup_invs)

    @classmethod
    def from_sites(
        cls,
        sites: Sequence[MineralSiteAndInventory],
        dedup_site_id: Optional[InternalID] = None,
    ) -> DedupMineralSiteAndInventory:
        _rank_ss: list[tuple[MineralSite, SiteScore]] = sorted(
            (
                (
                    site.ms,
                    SiteScore.get_score(
                        site.ms,
                    ),
                )
                for site in sites
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        rank_sites = [site for site, _ in _rank_ss]
        rank_site_scores = [
            SiteAndScore(site.site_id, score) for site, score in _rank_ss
        ]

        coordinates = next(
            (
                RefGeoCoordinate(
                    GeoCoordinate(site.location_view.lat, site.location_view.lon),
                    site.site_id,
                )
                for site in rank_sites
                if site.location_view.lat is not None
                and site.location_view.lon is not None
            ),
            None,
        )

        country = next(
            (
                RefListID(site.location_view.country, site.site_id)
                for site in rank_sites
                if len(site.location_view.country) > 0
            ),
            RefListID([], rank_sites[0].site_id),
        )
        state_or_province = next(
            (
                RefListID(site.location_view.state_or_province, site.site_id)
                for site in rank_sites
                if len(site.location_view.state_or_province) > 0
            ),
            RefListID([], rank_sites[0].site_id),
        )
        # Prefer the highest-ranked site carrying both a country and a state, so the
        # merged pair is one a record actually asserts; the two independent scans
        # above stay as the fallback when no site carries both.
        both = next(
            (
                site
                for site in rank_sites
                if len(site.location_view.country) > 0
                and len(site.location_view.state_or_province) > 0
            ),
            None,
        )
        if both is not None:
            country = RefListID(both.location_view.country, both.site_id)
            state_or_province = RefListID(
                both.location_view.state_or_province, both.site_id
            )

        ranked_deposit_types = top_5_deposit_types(_rank_ss)
        if len(ranked_deposit_types) > 0:
            top1_deposit_type = ranked_deposit_types[0].value.id
        else:
            top1_deposit_type = None
        dedup_site = DedupMineralSite(
            id=(
                dedup_site_id
                if dedup_site_id is not None
                else rank_sites[0].dedup_site_id
            ),
            name=RefValue.from_sites(rank_sites, lambda site: site.name),
            type=RefValue.from_sites(rank_sites, lambda site: site.type),
            rank=RefValue.from_sites(rank_sites, lambda site: site.rank),
            top1_deposit_type=top1_deposit_type,
            ranked_deposit_types=ranked_deposit_types,
            coordinates=coordinates,
            country=country,
            state_or_province=state_or_province,
            mineral_form=next(
                (
                    RefListStr(site.mineral_form, site.site_id)
                    for site in rank_sites
                    if len(site.mineral_form) > 0
                ),
                RefListStr([], rank_sites[0].site_id),
            ),
            geology_info=RefGeologyInfo.from_sites(rank_sites),
            discovered_year=RefValue.from_sites(
                rank_sites, lambda site: site.discovered_year
            ),
            ranked_sites=rank_site_scores,
            modified_at=max(msi.ms.modified_at for msi in sites),
        )
        dedup_invs = dedup_site.select_inventories(
            {msi.ms.site_id: msi.invs for msi in sites}
        )
        return DedupMineralSiteAndInventory(dedup_site, dedup_invs)

    def update_site(self, site: MineralSite):
        new_site_score = SiteScore.get_score(site)
        site_to_score = {ms.site_id: ms.score for ms in self.ranked_sites}
        if site.name is not None:
            if self.name is None or new_site_score > site_to_score[self.name.refid]:
                self.name = RefValue(site.name, site.site_id)

        if site.type is not None:
            if self.type is None or new_site_score > site_to_score[self.type.refid]:
                self.type = RefValue(site.type, site.site_id)

        if site.rank is not None:
            if self.rank is None or new_site_score > site_to_score[self.rank.refid]:
                self.rank = RefValue(site.rank, site.site_id)

        if site.deposit_type_candidates:
            pass

        raise NotImplementedError()

    def select_inventories(
        self,
        id_to_inventories: dict[InternalID, list[MineralInventoryView]],
    ) -> list[DedupMineralInventoryView]:
        """Select the inventories for this dedup site so that we always prefer the data presented by the users.
        If there are mutliple commodities reported by the users or by the system, we choose the one
        with the most recent date (if there is no date, we choose the one with the highest contained metal)
        """
        Data4CmpInv = TypedDict(
            "Data4CmpInv", {"inv": DedupMineralInventoryView, "is_from_user": bool}
        )
        comm2inv: dict[InternalID, Data4CmpInv] = {}

        for site in self.ranked_sites:
            is_from_user = site.score.is_from_user()
            for inv in id_to_inventories[site.site_id]:
                if inv.commodity not in comm2inv:
                    comm2inv[inv.commodity] = {
                        "inv": inv.to_dedup_view(site.site_id, self.id),
                        "is_from_user": is_from_user,
                    }
                    continue

                inv_cmp_data = comm2inv[inv.commodity]

                if inv_cmp_data["is_from_user"] != is_from_user:
                    if is_from_user:
                        # we prefer data from the users, even if the user data has no grade-tonnage
                        # the reason is this allow the users to delete invalid gt when the correct data isn't available
                        comm2inv[inv.commodity] = {
                            "inv": inv.to_dedup_view(site.site_id, self.id),
                            "is_from_user": is_from_user,
                        }
                    # the current one is from the user, it's better than the data from the model
                    # so no update
                    continue

                # now the data are both from the same source (users or systems)
                cmp_inv = inv_cmp_data["inv"]

                if inv.contained_metal is not None:
                    if cmp_inv.contained_metal is None:
                        # anything with grade-tonnage is better than no grade-tonnage
                        comm2inv[inv.commodity] = {
                            "inv": inv.to_dedup_view(site.site_id, self.id),
                            "is_from_user": is_from_user,
                        }
                        continue

                    if inv.date is not None and (
                        cmp_inv.date is None or inv.date > cmp_inv.date
                    ):
                        # chose the most recent date if available
                        comm2inv[inv.commodity] = {
                            "inv": inv.to_dedup_view(site.site_id, self.id),
                            "is_from_user": is_from_user,
                        }
                        continue

                    # choose the one with highest contained metal
                    if inv.contained_metal > cmp_inv.contained_metal:
                        comm2inv[inv.commodity] = {
                            "inv": inv.to_dedup_view(site.site_id, self.id),
                            "is_from_user": is_from_user,
                        }
                        continue

        return [inv_data["inv"] for inv_data in comm2inv.values()]

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("id", self.id),
                ("name", self.name.to_dict() if self.name is not None else None),
                ("type", self.type.to_dict() if self.type is not None else None),
                ("rank", self.rank.to_dict() if self.rank is not None else None),
                (
                    "deposit_types",
                    [dt.to_dict() for dt in self.ranked_deposit_types],
                ),
                (
                    "coordinates",
                    (
                        self.coordinates.to_dict()
                        if self.coordinates is not None
                        else None
                    ),
                ),
                ("country", self.country.to_dict()),
                ("state_or_province", self.state_or_province.to_dict()),
                ("mineral_form", self.mineral_form.to_dict()),
                ("geology_info", self.geology_info.to_dict()),
                (
                    "discovered_year",
                    (
                        self.discovered_year.to_dict()
                        if self.discovered_year is not None
                        else None
                    ),
                ),
                ("ranked_sites", [site.to_dict() for site in self.ranked_sites]),
                ("modified_at", self.modified_at),
            )
        )

    @classmethod
    def from_dict(cls, d):
        ranked_deposit_types = [
            RefDepositType.from_dict(dt) for dt in d.get("deposit_types", [])
        ]
        if len(ranked_deposit_types) > 0:
            top1_deposit_type = ranked_deposit_types[0].value.id
        else:
            top1_deposit_type = None

        dedup_site = DedupMineralSite(
            id=d["id"],
            name=RefValue.from_dict(d["name"]) if d.get("name") is not None else None,
            type=RefValue.from_dict(d["type"]) if d.get("type") is not None else None,
            rank=RefValue.from_dict(d["rank"]) if d.get("rank") is not None else None,
            top1_deposit_type=top1_deposit_type,
            ranked_deposit_types=ranked_deposit_types,
            coordinates=(
                RefGeoCoordinate.from_dict(d["coordinates"])
                if d.get("coordinates") is not None
                else None
            ),
            country=RefListID.from_dict(d["country"]),
            state_or_province=RefListID.from_dict(d["state_or_province"]),
            mineral_form=RefListStr.from_dict(d["mineral_form"]),
            geology_info=RefGeologyInfo.from_dict(d["geology_info"]),
            discovered_year=(
                RefValue.from_dict(d["discovered_year"])
                if d.get("discovered_year") is not None
                else None
            ),
            ranked_sites=[
                SiteAndScore.from_dict(site) for site in d.get("ranked_sites", [])
            ],
            modified_at=d["modified_at"],
        )
        return dedup_site


def top_5_deposit_types(
    ranked_sites: list[tuple[MineralSite, SiteScore]],
) -> list[RefDepositType]:
    _tmp_deposit_types: dict[str, tuple[RefDepositType, SiteScore]] = {}

    for site, score in ranked_sites:
        for dt in site.deposit_type_candidates:
            if dt.normalized_uri is None:
                continue
            dt_id = MINMOD_NS.mr.id(dt.normalized_uri)
            if dt_id not in _tmp_deposit_types or (dt.confidence, score) > (
                _tmp_deposit_types[dt_id][0].value.confidence,
                _tmp_deposit_types[dt_id][1],
            ):
                _tmp_deposit_types[dt_id] = (
                    RefDepositType(
                        value=DedupMineralSiteDepositType(
                            id=dt_id,
                            source=dt.source,
                            confidence=dt.confidence,
                        ),
                        refid=site.site_id,
                    ),
                    score,
                )

    return [
        val[0]
        for val in sorted(
            _tmp_deposit_types.values(),
            key=lambda x: (x[0].value.confidence, x[1]),
            reverse=True,
        )[:5]
    ]
