from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from minmodkg.etl.geochem_jsonld import (
    USER_URI,
    EntityResolver,
    clean,
    deposit_record_ids,
    deposit_sites,
    dump_paper,
    node_id,
    paper_source_id,
    read_paper_file,
    sample_key,
    write_paper_file,
)
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.dedup_mineral_site import DedupMineralSite
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSite, MineralSiteAndInventory
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.views.mineral_inventory_view import MineralInventoryView
from minmodkg.services.mineral_site import MineralSiteService
from minmodkg.services.sync.geochem_listener import GeoChemJournal
from minmodkg.transformations import make_sample_id
from minmodkg.typing import InternalID
from sqlalchemy import Engine, distinct, exists, func, select
from sqlalchemy.orm import Session


# paper ids become file names, so keep them to a safe character set
PAPER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")


class PaperConflictError(Exception):
    pass


@dataclass
class Registration:
    created: bool
    paper: Paper
    sites: list[dict] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)


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
        deposit_type: Optional[InternalID] = None,
        country: Optional[InternalID] = None,
        state_or_province: Optional[InternalID] = None,
        dedup_site_id: Optional[InternalID] = None,
        site_id: Optional[InternalID] = None,
        limit: int = 50,
        offset: int = 0,
        return_count: bool = False,
    ) -> tuple[list[tuple[Paper, list[MineralSite]]], Optional[int]]:
        """Papers with a site matching every filter, each with those sites, and
        the number of such papers when `return_count` is set. Deposit type,
        country and state are the dedup site's, as in the editor's search."""
        query = select(Paper, MineralSite).join(MineralSite, self.paper_site_join())
        if commodity is not None:
            # per site, so it uses the (site_id, commodity) index
            query = query.where(
                exists().where(
                    MineralInventoryView.site_id == MineralSite.id,
                    MineralInventoryView.commodity == commodity,
                )
            )
        if any(x is not None for x in (deposit_type, country, state_or_province)):
            query = query.join(
                DedupMineralSite, DedupMineralSite.id == MineralSite.dedup_site_id
            )
            if deposit_type is not None:
                query = query.where(DedupMineralSite.top1_deposit_type == deposit_type)
            if country is not None:
                query = query.where(DedupMineralSite.has_country(country))
            if state_or_province is not None:
                query = query.where(
                    DedupMineralSite.has_state_or_province(state_or_province)
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
            total = None
            if return_count:
                total = session.execute(
                    query.with_only_columns(func.count(distinct(Paper.paper_id)))
                ).scalar_one()
            paper_ids = list(session.execute(page).scalars())
            rows = session.execute(
                query.where(Paper.paper_id.in_(paper_ids)).order_by(
                    Paper.paper_id, MineralSite.site_id
                )
            ).all()
        out: dict[str, tuple[Paper, list[MineralSite]]] = {}
        for paper, site in rows:
            out.setdefault(paper.paper_id, (paper, []))[1].append(site)
        return [out[pid] for pid in paper_ids], total

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

    def get_jsonld(self, paper: Paper, jsonld_dir: Path) -> str:
        """The paper's JSON-LD as MinMod has it now: its file plus the edits
        not yet written back to it."""
        # events before the file: an edit written back in between is then in
        # both, and applying it again changes nothing
        with Session(self.engine) as session:
            events = (
                session.execute(
                    select(EventLog)
                    .where(EventLog.geochem_synced.is_(False))
                    .order_by(EventLog.id)
                )
                .scalars()
                .all()
            )
        journal = GeoChemJournal()
        journal.handle(events)

        path = jsonld_dir / paper.file
        if paper.paper_id not in journal.journal:
            return path.read_text()
        doc = read_paper_file(path)
        journal.apply(paper.paper_id, doc)
        return dump_paper(doc)

    def register(
        self,
        doc: dict,
        registered_by: str,
        jsonld_dir: Path,
        entity_dir: Optional[Path] = None,
    ) -> Registration:
        """Add a GeoChem paper from its canonical JSON-LD, unless MinMod has it
        already: then nothing is changed, so edits made since are kept. Either
        way the ids MinMod uses for its sites and samples come back."""
        # imported here: the loader builds on the services this module uses
        from minmodkg.etl.geochem_loader import load_paper

        paper_id = clean(doc.get("paper_id"))
        if not isinstance(paper_id, str) or not PAPER_ID.match(paper_id):
            raise ValueError(
                "paper_id is required: letters, digits, '.', '_' or '-' only"
            )
        source_id = paper_source_id(doc)
        if source_id is None:
            raise ValueError("paper_doi is required")

        with Session(self.engine, expire_on_commit=False) as session:
            same_id = session.get(Paper, paper_id)
            same_doi = session.execute(
                select(Paper).where(Paper.source_id == source_id)
            ).scalar_one_or_none()
        if same_doi is not None and same_doi.paper_id != paper_id:
            raise PaperConflictError(
                f"DOI {doc['paper_doi']!r} is already registered as paper "
                f"{same_doi.paper_id!r}"
            )
        if same_id is not None:
            if same_id.source_id != source_id:
                raise PaperConflictError(
                    f"paper {paper_id!r} is already registered with DOI {same_id.doi!r}"
                )
            return Registration(False, same_id, *self.id_mapping(doc))

        # a site id taken by another owner means someone published this paper's
        # deposits outside it; loading would collide with them
        site_ids = [site_id for site_id, _ in deposit_sites(doc)]
        with Session(self.engine) as session:
            taken = session.execute(
                select(MineralSite.site_id, MineralSite.created_by).where(
                    MineralSite.site_id.in_(site_ids),
                    MineralSite.created_by != USER_URI,
                )
            ).first()
        if taken is not None:
            raise PaperConflictError(
                f"site {taken[0]!r} already exists, owned by {taken[1]!r}, "
                "outside this paper; resolve it before registering the paper"
            )

        file = f"geochem_{paper_id}.canonical.jsonld"
        path = jsonld_dir / file
        if path.exists():
            raise PaperConflictError(
                f"{file} is already in the JSON-LD directory but not loaded; "
                "load it with the GeoChem loader"
            )
        write_paper_file(path, doc)
        try:
            load_paper(doc, file, EntityResolver.build(entity_dir), False, 50000)
        except Exception:
            # keep the file only if the paper made it into the database, so a
            # failed triple-store load can be finished with the loader
            if self.find_by_id(paper_id) is None:
                path.unlink(missing_ok=True)
            raise

        with Session(self.engine, expire_on_commit=False) as session:
            paper = session.get(Paper, paper_id)
            assert paper is not None
            paper.registered_by = registered_by
            session.commit()
        return Registration(True, paper, *self.id_mapping(doc))

    def id_mapping(self, doc: dict) -> tuple[list[dict], list[dict]]:
        """The site and sample ids MinMod derives for each deposit and sample
        node of the document, and whether MinMod has them."""
        sites, samples = [], []
        seen: set[tuple[str, str]] = set()
        deposits = doc.get("deposits") or []
        for (site_id, deposit), record_id in zip(
            deposit_sites(doc), deposit_record_ids(deposits)
        ):
            sites.append(
                {"node_id": node_id(deposit), "record_id": record_id, "id": site_id}
            )
            for node in deposit.get("samples") or []:
                key = sample_key(node)
                # a sample split across nodes shares one @id; list it once
                if key is not None and (site_id, key) not in seen:
                    seen.add((site_id, key))
                    samples.append(
                        {
                            "node_id": node_id(node),
                            "sample_id": key,
                            "mineral_site_id": site_id,
                            "id": make_sample_id(site_id, key),
                        }
                    )

        with Session(self.engine) as session:
            known_sites = set(
                session.execute(
                    select(MineralSite.site_id).where(
                        MineralSite.site_id.in_([x["id"] for x in sites])
                    )
                ).scalars()
            )
            known_samples = set(
                session.execute(
                    select(Sample.public_id).where(
                        Sample.public_id.in_({x["id"] for x in samples})
                    )
                ).scalars()
            )
        for x in sites:
            x["in_minmod"] = x["id"] in known_sites
        for x in samples:
            x["in_minmod"] = x["id"] in known_samples
        return sites, samples
