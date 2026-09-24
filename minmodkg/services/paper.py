from __future__ import annotations

from typing import Optional

from minmodkg.etl.geochem_jsonld import USER_URI
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.mineral_site import MineralSite, MineralSiteAndInventory
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.views.mineral_inventory_view import MineralInventoryView
from minmodkg.services.mineral_site import MineralSiteService
from minmodkg.typing import InternalID
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session


class PaperService:
    """GeoChem papers; a paper's sites are the geochem-hmi sites sharing its
    source_id."""

    def __init__(self, _engine: Optional[Engine] = None):
        self.engine = _engine or engine

    def paper_site_join(self):
        return (MineralSite.source_id == Paper.source_id) & (
            MineralSite.created_by == USER_URI
        )

    def find_papers(
        self,
        commodity: Optional[InternalID] = None,
        dedup_site_id: Optional[InternalID] = None,
        site_id: Optional[InternalID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[Paper, list[MineralSite]]]:
        """Papers with a site matching every filter, each with those sites."""
        query = select(Paper, MineralSite).join(MineralSite, self.paper_site_join())
        if commodity is not None:
            query = query.where(
                MineralSite.id.in_(
                    select(MineralInventoryView.site_id).where(
                        MineralInventoryView.commodity == commodity
                    )
                )
            )
        if dedup_site_id is not None:
            query = query.where(MineralSite.dedup_site_id == dedup_site_id)
        if site_id is not None:
            # any site of the same dedup group, whoever created it
            same_group = select(MineralSite.dedup_site_id).where(
                MineralSite.site_id == site_id
            )
            query = query.where(MineralSite.dedup_site_id.in_(same_group))

        page = query.with_only_columns(Paper.paper_id).distinct()
        page = page.order_by(Paper.paper_id).offset(offset)
        if limit > 0:
            page = page.limit(limit)

        with Session(self.engine, expire_on_commit=False) as session:
            paper_ids = list(session.execute(page).scalars())
            rows = session.execute(
                query.where(Paper.paper_id.in_(paper_ids)).order_by(
                    Paper.paper_id, MineralSite.site_id
                )
            ).all()
        out: dict[str, tuple[Paper, list[MineralSite]]] = {}
        for paper, site in rows:
            out.setdefault(paper.paper_id, (paper, []))[1].append(site)
        return [out[pid] for pid in paper_ids]

    def find_by_id(self, paper_id: str) -> Optional[Paper]:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(Paper, paper_id)

    def get_sites(self, paper: Paper) -> list[tuple[MineralSiteAndInventory, int]]:
        """The paper's sites, each with its number of samples."""
        service = MineralSiteService(self.engine)
        with Session(self.engine, expire_on_commit=False) as session:
            sites = service._read_mineral_sites(
                session,
                service._select_mineral_site()
                .where(
                    MineralSite.source_id == paper.source_id,
                    MineralSite.created_by == USER_URI,
                )
                .order_by(MineralSite.site_id),
            )
            counts = dict(
                session.execute(
                    select(Sample.mineral_site_id, func.count())
                    .where(Sample.mineral_site_id.in_([s.ms.site_id for s in sites]))
                    .group_by(Sample.mineral_site_id)
                ).all()
            )
        return [(s, counts.get(s.ms.site_id, 0)) for s in sites]

    def find_samples(
        self,
        paper: Paper,
        site_id: Optional[InternalID] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Sample], int]:
        """A page of the paper's samples, and the total count."""
        query = (
            select(Sample)
            .join(MineralSite, MineralSite.site_id == Sample.mineral_site_id)
            .where(
                MineralSite.source_id == paper.source_id,
                MineralSite.created_by == USER_URI,
            )
        )
        if site_id is not None:
            query = query.where(Sample.mineral_site_id == site_id)
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.execute(
                select(func.count()).select_from(query.subquery())
            ).scalar_one()
            page = query.order_by(Sample.mineral_site_id, Sample.public_id).offset(
                offset
            )
            if limit > 0:
                page = page.limit(limit)
            samples = list(session.execute(page).scalars())
        return samples, total
