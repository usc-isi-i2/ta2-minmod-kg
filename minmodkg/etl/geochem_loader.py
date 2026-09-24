"""Load GeoChem papers from canonical JSON-LD into Postgres and the triple store.

The JSON-LD directory is the source of truth, like the data repository is for
the MinMod ETL: every run replaces each paper -- its sites and samples
included -- with what its file says, removing anything the file no longer has.
Edits made through the API are written back into these files by the sync
service, so they survive a reload.
"""

from __future__ import annotations

import time
from pathlib import Path
from string import Template
from typing import Annotated, Iterable, Optional

import serde.json
import typer
from minmodkg.etl.geochem_jsonld import (
    USER_URI,
    USERNAME,
    EntityResolver,
    PaperContent,
    build_paper,
    clean,
    read_paper,
)
from minmodkg.models.kg.base import MINMOD_KG, NS_GCO, NS_GCR, NS_MR
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.dedup_mineral_site import DedupMineralSite
from minmodkg.models.kgrel.mineral_site import MineralSite, MineralSiteAndInventory
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.user import User
from minmodkg.models.kgrel.views.mineral_inventory_view import (
    DedupMineralInventoryView,
    MineralInventoryView,
)
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.services.mineral_site import MineralSiteService
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from tqdm import tqdm

DELETE_QUERY = Template(
    (Path(__file__).parent / "queries/delete_resources.rq").read_text()
)

app = typer.Typer(pretty_exceptions_short=True, pretty_exceptions_enable=False)


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def to_msi(site: KGMineralSite, entser: EntityService) -> MineralSiteAndInventory:
    msi = MineralSiteAndInventory.from_raw_site(
        site,
        commodity_form_conversion=entser.get_commodity_form_conversion(),
        crs_names=entser.get_crs_name(),
        source_score=entser.get_data_source_score(),
    )
    msi.ms.modified_at = time.time_ns()
    return msi


def refresh_dedup_sites(
    session: Session, service: MineralSiteService, dedup_ids: set[str]
) -> None:
    """Recompute dedup sites from their current members; drop empty ones."""
    for dedup_id in dedup_ids:
        members = service._read_mineral_sites(
            session,
            service._select_mineral_site().where(MineralSite.dedup_site_id == dedup_id),
        )
        session.execute(
            delete(DedupMineralInventoryView).where(
                DedupMineralInventoryView.dedup_site_id == dedup_id
            )
        )
        if not members:
            session.execute(
                delete(DedupMineralSite).where(DedupMineralSite.id == dedup_id)
            )
            continue
        dedup = DedupMineralSite.from_sites(members, dedup_site_id=dedup_id)
        session.execute(dedup.dms.get_update_query())
        session.add_all(dedup.invs)


def save_postgres(
    session: Session, paper: Paper, content: PaperContent
) -> tuple[list[MineralSiteAndInventory], list[Sample], list[str]]:
    """Replace the paper's rows; returns the new sites and samples, and the
    site ids the paper had before."""
    entser = EntityService.get_instance()
    service = MineralSiteService(engine)
    session.merge(paper)

    old = {
        site_id: (id, dedup_id)
        for id, site_id, dedup_id in session.execute(
            select(
                MineralSite.id, MineralSite.site_id, MineralSite.dedup_site_id
            ).where(
                MineralSite.source_id == paper.source_id,
                MineralSite.created_by == USER_URI,
            )
        ).all()
    }
    msis = [to_msi(site, entser) for site in content.sites]
    new_ids = [msi.ms.site_id for msi in msis]
    affected_dedups = {dedup_id for _, dedup_id in old.values()}

    # a new site joins the dedup group of a site with the same source/record
    # id (MinMod's automatic same-as rule), or starts its own
    for msi in msis:
        if msi.ms.site_id in old:
            msi.ms.id, msi.ms.dedup_site_id = old[msi.ms.site_id]
            continue
        linked = session.execute(
            select(MineralSite.dedup_site_id)
            .where(
                MineralSite.source_id == msi.ms.source_id,
                MineralSite.record_id == msi.ms.record_id,
            )
            .limit(1)
        ).scalar_one_or_none()
        if linked is not None:
            msi.ms.dedup_site_id = linked
        else:
            msi.ms.dedup_site_id = MineralSite.get_dedup_id([msi.ms.site_id])
            session.add(
                DedupMineralSite.from_sites(
                    [msi], dedup_site_id=msi.ms.dedup_site_id
                ).dms
            )
        affected_dedups.add(msi.ms.dedup_site_id)
    session.flush()

    for msi in msis:
        if msi.ms.site_id in old:
            session.execute(msi.ms.get_update_query())
            session.execute(
                delete(MineralInventoryView).where(
                    MineralInventoryView.site_id == msi.ms.id
                )
            )
        else:
            session.add(msi.ms)
            session.flush()
        for inv in msi.invs:
            inv.site_id = msi.ms.id
        session.add_all(msi.invs)

    stale = [site_id for site_id in old if site_id not in set(new_ids)]
    if stale:
        session.execute(delete(MineralSite).where(MineralSite.site_id.in_(stale)))
    session.execute(delete(Sample).where(Sample.mineral_site_id.in_(new_ids)))
    session.flush()
    refresh_dedup_sites(session, service, affected_dedups)

    now = time.time_ns()
    samples = []
    for s in content.samples:
        rel = Sample.from_raw_sample(s)
        rel.modified_at = now
        samples.append(rel)
    session.add_all(samples)
    session.commit()
    return msis, samples, list(old)


def save_kg(
    paper: Paper,
    msis: list[MineralSiteAndInventory],
    samples: list[Sample],
    old_site_ids: list[str],
    old_sample_ids: list[str],
    batch_size: int,
) -> None:
    """Replace the paper's subgraph, keeping same-as links."""
    site_ids = [m.ms.site_id for m in msis]
    roots = (
        [str(paper.to_kg([]).uri)]
        + [NS_MR.uristr(s) for s in {*old_site_ids, *site_ids}]
        + [NS_GCR.uristr(s) for s in {*old_sample_ids, *(s.public_id for s in samples)}]
    )
    for batch in chunks(roots, 200):
        MINMOD_KG.delete(
            DELETE_QUERY.substitute(roots=" ".join(f"<{uri}>" for uri in batch))
        )

    has_sample = f"<{NS_GCO.uristr('has_sample')}>"
    triples = paper.to_kg(site_ids).to_triples()
    for msi in msis:
        triples.extend(msi.ms.to_kg().to_triples())
    for sample in samples:
        triples.append(
            (
                f"<{NS_MR.uristr(sample.mineral_site_id)}>",
                has_sample,
                f"<{NS_GCR.uristr(sample.public_id)}>",
            )
        )
        triples.extend(sample.to_kg().to_triples())
    MINMOD_KG.batch_insert(triples, batch_size=batch_size)


def load_paper(
    doc: dict, file: str, resolver: EntityResolver, skip_kg: bool, batch_size: int
) -> PaperContent:
    paper = build_paper(doc, file, time.time_ns())
    content = read_paper(doc, resolver)
    with Session(engine, expire_on_commit=False) as session:
        old_sample_ids = list(
            session.execute(
                select(Sample.public_id)
                .join(MineralSite, MineralSite.site_id == Sample.mineral_site_id)
                .where(
                    MineralSite.source_id == paper.source_id,
                    MineralSite.created_by == USER_URI,
                )
            ).scalars()
        )
        msis, samples, old_site_ids = save_postgres(session, paper, content)
    if not skip_kg:
        save_kg(paper, msis, samples, old_site_ids, old_sample_ids, batch_size)
    return content


@app.command()
def main(
    jsonld_dir: Annotated[
        Path, typer.Argument(help="Directory of GeoChem *.jsonld papers")
    ],
    entity_dir: Annotated[
        Optional[Path],
        typer.Option(help="ta2-minmod-data/data/entities, for ISO country codes"),
    ] = None,
    paper: Annotated[
        Optional[list[str]], typer.Option(help="Only load these paper ids")
    ] = None,
    skip_kg: Annotated[bool, typer.Option(help="Only load Postgres")] = False,
    batch_size: Annotated[int, typer.Option(help="Triples per SPARQL update")] = 5120,
):
    files = sorted(jsonld_dir.rglob("*.jsonld"))
    if not files:
        raise typer.BadParameter(f"no *.jsonld files in {jsonld_dir}")

    with Session(engine) as session:
        if session.get(User, USERNAME) is None:
            typer.secho(
                f"warning: user {USERNAME!r} does not exist; the HMI can't edit these "
                "sites until it's created",
                fg="yellow",
            )

    resolver = EntityResolver.build(entity_dir)
    n_papers = n_sites = n_samples = 0
    skipped, merged = [], []
    for file in tqdm(files, desc="Loading papers"):
        doc = serde.json.deser(file)
        if paper and clean(doc.get("paper_id")) not in paper:
            continue
        if not clean(doc.get("paper_doi")):
            skipped.append(file.name)
            continue
        content = load_paper(
            doc, str(file.relative_to(jsonld_dir)), resolver, skip_kg, batch_size
        )
        n_papers += 1
        n_sites += len(content.sites)
        n_samples += len(content.samples)
        merged.extend(content.merged_samples)

    typer.echo(f"Loaded {n_papers} papers, {n_sites} sites, {n_samples} samples")
    if skipped:
        typer.echo(f"Skipped {len(skipped)} papers without a DOI: {', '.join(skipped)}")
    if merged:
        typer.echo(
            f"Merged {len(merged)} samples split across nodes, e.g. {merged[:3]}"
        )


if __name__ == "__main__":
    app()
