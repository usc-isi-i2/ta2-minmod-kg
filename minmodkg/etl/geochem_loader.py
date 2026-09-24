"""Load GeoChem canonical JSON-LD into Postgres and the triple store.

One MinMod mineral site per deposit, owned by the geochem-hmi system user, so
site ids match what the HMI computes. Postgres is written first and is the
source of truth; the triple store only receives entities it doesn't have yet,
generated from the Postgres rows. Re-running is safe.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterable, Optional

import serde.json
import typer
from minmodkg.misc.utils import format_datetime
from minmodkg.models.kg.base import MINMOD_KG, NS_GCR, NS_MR
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.location_info import LocationInfo
from minmodkg.models.kg.mineral_inventory import MineralInventory
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kg.reference import BoundingBox, Document, PageInfo, Reference
from minmodkg.models.kg.sample import Analysis, EditEvent, Element
from minmodkg.models.kg.sample import Sample as KGSample
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.dedup_mineral_site import DedupMineralSite
from minmodkg.models.kgrel.mineral_site import MineralSite, MineralSiteAndInventory
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.user import User
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.services.sample import SampleService
from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm import tqdm

USERNAME = "geochem-hmi"
USER_URI = f"https://minmod.isi.edu/users/s/{USERNAME}"
SOURCE = "GeoChem JSON-LD loader"
WGS84 = "EPSG:4326"

app = typer.Typer(pretty_exceptions_short=True, pretty_exceptions_enable=False)


def clean(v: Any) -> Any:
    """Unwrap `{"value"}`/`{"@value"}` nodes; blank strings become None."""
    if isinstance(v, dict):
        for key in ("value", "@value"):
            if key in v:
                return clean(v[key])
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def to_float(v: Any) -> Optional[float]:
    v = clean(v)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def uid_from_node(node: dict) -> Optional[str]:
    """`@id` tail with the `site__<doi-slug>__` prefix dropped, as the HMI does."""
    raw = clean(node.get("@id")) or clean(node.get("uri"))
    if not isinstance(raw, str):
        return None
    segment = raw.rstrip("/").rsplit("/", 1)[-1]
    parts = segment.split("__")
    if len(parts) > 2 and parts[0] == "site":
        segment = "__".join(parts[2:]) or segment
    return segment or None


def deposit_record_ids(deposits: list[dict]) -> list[str]:
    """Per-paper deposit uids, de-duplicated the same way as the HMI."""
    used: set[str] = set()
    out = []
    for i, deposit in enumerate(deposits):
        base = str(
            clean(deposit.get("deposit_id"))
            or uid_from_node(deposit)
            or clean(deposit.get("name"))
            or f"deposit-{i + 1}"
        ).strip()
        candidate, suffix = base, 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        out.append(candidate)
    return out


def sample_key(sample: dict) -> Optional[str]:
    """`@id` tail after `__sample__`; unique within a deposit, unlike sample_id."""
    raw = clean(sample.get("@id")) or clean(sample.get("uri"))
    if isinstance(raw, str) and "__sample__" in raw:
        return raw.rsplit("__sample__", 1)[1] or None
    return clean(sample.get("sample_id"))


def point_wkt(node: dict) -> Optional[str]:
    lat, lon = to_float(node.get("latitude")), to_float(node.get("longitude"))
    if lat is None or lon is None:
        return None
    return f"POINT ({lon} {lat})"


@dataclass
class EntityResolver:
    """Maps observed names (and ISO3 country codes) to MinMod entity URIs."""

    countries: dict[str, str] = field(default_factory=dict)
    states: dict[str, str] = field(default_factory=dict)
    deposit_types: dict[str, str] = field(default_factory=dict)
    commodities: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    wgs84: Optional[str] = None

    @staticmethod
    def build(entity_dir: Optional[Path]) -> EntityResolver:
        entser = EntityService.get_instance()
        res = EntityResolver()
        for c in entser.get_countries():
            for name in [c.name, *c.aliases]:
                res.countries[name.lower()] = NS_MR.uristr(c.id)
        if entity_dir is not None and (entity_dir / "country.csv").exists():
            with open(entity_dir / "country.csv", newline="") as f:
                for row in csv.DictReader(f):
                    for code in (row.get("iso3"), row.get("iso2")):
                        if code:
                            res.countries[code.lower()] = NS_MR.uristr(row["minmod_id"])
        for s in entser.get_state_or_provinces():
            res.states[s.name.lower()] = NS_MR.uristr(s.id)
        for d in entser.get_deposit_types():
            res.deposit_types[d.name.lower()] = NS_MR.uristr(d.id)
        for c in entser.get_commodities():
            if c.parent is None:
                for name in [c.name, *c.aliases]:
                    res.commodities[name.lower()] = NS_MR.uristr(c.id)
        for u in entser.get_units():
            for name in [u.name, *u.aliases]:
                res.units[name.strip().lower()] = u.uri
        res.wgs84 = next((c.uri for c in entser.get_crs() if c.name == WGS84), None)
        return res

    def state(self, name: str) -> Optional[str]:
        key = name.lower()
        for suffix in (" province", " prefecture", " state"):
            key = key.removesuffix(suffix)
        return self.states.get(name.lower()) or self.states.get(key)


def candidate(
    node: Any, index: dict[str, str] | None = None, uri: Optional[str] = None
) -> Optional[CandidateEntity]:
    observed = (
        clean(node.get("observed_name")) if isinstance(node, dict) else clean(node)
    )
    if observed is None:
        return None
    confidence = to_float(node.get("confidence")) if isinstance(node, dict) else None
    source = clean(node.get("source")) if isinstance(node, dict) else None
    if uri is None and index is not None:
        uri = index.get(observed.lower())
    return CandidateEntity(
        source=source or SOURCE,
        confidence=1.0 if confidence is None else min(max(confidence, 0.0), 1.0),
        observed_name=observed,
        normalized_uri=uri,
    )


def paper_document(paper: dict) -> Document:
    doi = clean(paper.get("paper_doi"))
    return Document(
        doi=doi, uri=f"https://doi.org/{doi}", title=clean(paper.get("paper_title"))
    )


def site_references(deposit: dict, doc: Document) -> list[Reference]:
    refs = []
    for r in deposit.get("mo_reference") or []:
        page_info = []
        pi = r.get("page_info")
        if isinstance(pi, dict) and pi.get("page") is not None:
            bb = pi.get("bounding_box") or {}
            coords = {
                k: to_float(bb.get(k)) for k in ("x_max", "x_min", "y_max", "y_min")
            }
            page_info.append(
                PageInfo(
                    page=int(pi["page"]),
                    bounding_box=(
                        BoundingBox(**coords)  # type: ignore[arg-type]
                        if all(c is not None for c in coords.values())
                        else None
                    ),
                )
            )
        refs.append(
            Reference(
                document=doc,
                page_info=page_info,
                comment=clean(r.get("evidence_quote")),
                property=clean(r.get("property")),
            )
        )
    return refs or [Reference(document=doc)]


def build_site(
    deposit: dict, record_id: str, doc: Document, resolver: EntityResolver
) -> KGMineralSite:
    country = candidate(deposit.get("country"), resolver.countries)
    state = deposit.get("state")
    state_ce = candidate(state)
    if state_ce is not None:
        state_ce.normalized_uri = resolver.state(state_ce.observed_name)
    wkt = point_wkt(deposit)
    location_info = None
    if country or state_ce or wkt:
        location_info = LocationInfo(
            country=[country] if country else [],
            state_or_province=[state_ce] if state_ce else [],
            crs=candidate(WGS84, uri=resolver.wgs84) if wkt else None,
            location=wkt,
        )
    deposit_type = candidate(deposit.get("deposit_type"), resolver.deposit_types)
    commodities = [
        candidate(c, resolver.commodities) for c in deposit.get("commodity") or []
    ]
    return KGMineralSite(
        source_id=doc.uri,
        record_id=record_id,
        created_by=USER_URI,
        name=clean(deposit.get("name")),
        location_info=location_info,
        deposit_type_candidate=[deposit_type] if deposit_type else [],
        mineral_inventory=[
            MineralInventory(commodity=c, reference=Reference(document=doc))
            for c in commodities
            if c is not None
        ],
        reference=site_references(deposit, doc),
    )


SAMPLE_FIELDS = [
    "sample_name",
    "sample_local_id",
    "sample_type",
    "collection_date",
    "description",
    "mineral",
    "sampling_method",
    "sample_preparation",
    "material_class",
    "material_class_comment",
    "analysed_material",
    "sample_deposit_relation",
    "geological_province",
    "strat_unit_name",
    "strat_grouping",
    "earth_material_group",
    "earth_material_qualifier",
    "mode_occurrence",
    "metamorphic_grade",
    "alteration",
    "paragenetic_stage",
    "texture",
    "color",
    "associated_minerals",
    "feature_type",
    "feature_name",
    "feature_local_uid",
    "comments",
]
ANALYSIS_FIELDS = {
    "analytical_method": "analytical_method",
    "instrument_type": "instrument_type_model",
    "laboratory_location": "laboratory_location",
    "operating_conditions": "operating_conditions",
    "standards_used": "standards_used",
    "aggregation_method": "aggregation_method",
    "data_quality": "data_quality",
    "analysis_date": "analysis_date",
}


def unit_candidate(node: Any, resolver: EntityResolver) -> Optional[CandidateEntity]:
    if not isinstance(node, dict):
        return candidate(node, resolver.units)
    label = clean(node.get("rdfs:label")) or clean(node.get("observed_name"))
    return candidate(label, resolver.units)


def build_element(node: dict, resolver: EntityResolver) -> Optional[Element]:
    label = clean(node.get("rdfs:label")) or clean(node.get("label"))
    if label is None:
        return None
    return Element(
        label=label,
        grade=to_float(node.get("grade")),
        grade_unit=unit_candidate(node.get("grade_unit"), resolver),
        detection_limit=to_float(node.get("detection_limit")),
        detection_limit_unit=unit_candidate(node.get("detection_limit_unit"), resolver),
    )


def build_analysis(node: dict, resolver: EntityResolver) -> Analysis:
    elements = [build_element(e, resolver) for e in node.get("element") or []]
    return Analysis(
        analysis_id=clean(node.get("analysis_id")),
        elements=[e for e in elements if e is not None],
        **{k: clean(node.get(src)) for k, src in ANALYSIS_FIELDS.items()},
    )


def build_sample(
    node: dict, key: str, site_id: str, doc: Document, resolver: EntityResolver
) -> KGSample:
    wkt = point_wkt(node)
    return KGSample(
        sample_id=key,
        mineral_site_id=site_id,
        top_depth_m=to_float(node.get("top_depth_m")),
        bottom_depth_m=to_float(node.get("bottom_depth_m")),
        location_info=(
            LocationInfo(crs=candidate(WGS84, uri=resolver.wgs84), location=wkt)
            if wkt
            else None
        ),
        analyses=[build_analysis(a, resolver) for a in node.get("analyses") or []],
        reference=[Reference(document=doc)],
        **{k: clean(node.get(k)) for k in SAMPLE_FIELDS},
    )


def read_edits(edits_dir: Optional[Path]) -> dict[str, dict[str, dict]]:
    """Curator-edited samples backed up by the API, by mineral_site_id then id."""
    out: dict[str, dict[str, dict]] = {}
    if edits_dir is None or not edits_dir.exists():
        return out
    for file in sorted(edits_dir.glob("*.json")):
        records = serde.json.deser(file)
        for r in records if isinstance(records, list) else [records]:
            out.setdefault(r["mineral_site_id"], {})[r["id"]] = r
    return out


@dataclass
class PaperLoad:
    sites: list[KGMineralSite]
    samples: list[KGSample]
    merged_samples: list[str]


def build_paper(
    paper: dict, resolver: EntityResolver, edits: dict[str, dict[str, dict]]
) -> PaperLoad:
    doc = paper_document(paper)
    deposits = paper.get("deposits") or []
    sites, samples, merged = [], [], []
    for deposit, record_id in zip(deposits, deposit_record_ids(deposits)):
        site = build_site(deposit, record_id, doc, resolver)
        sites.append(site)
        by_id: dict[str, KGSample] = {}
        for node in deposit.get("samples") or []:
            key = sample_key(node)
            if key is None:
                continue
            sample = build_sample(node, key, site.id, doc, resolver)
            first = by_id.setdefault(sample.id, sample)
            if first is not sample:
                # same sample split across nodes; keep the first node's fields
                seen = {a.analysis_id for a in first.analyses}
                first.analyses.extend(
                    a for a in sample.analyses if a.analysis_id not in seen
                )
                merged.append(sample.id)
        for public_id, record in edits.get(site.id, {}).items():
            by_id[public_id] = KGSample.from_dict(record)
        samples.extend(by_id.values())
    return PaperLoad(sites, samples, merged)


def to_rel_sample(sample: KGSample, now: str) -> Sample:
    rel = Sample.from_raw_sample(sample)
    rel.modified_at = time.time_ns()
    if not rel.edit_history:
        rel.edit_history = [
            EditEvent(
                updated_by=USER_URI,
                updated_at=now,
                changed_properties=SampleService._changed_properties({}, rel.to_dict()),
            )
        ]
    return rel


def save_postgres(session: Session, load: PaperLoad) -> tuple[int, int]:
    """Insert sites and samples not already present; returns counts inserted."""
    entser = EntityService.get_instance()
    site_ids = [s.id for s in load.sites]
    existing_sites = set(
        session.execute(
            select(MineralSite.site_id).where(MineralSite.site_id.in_(site_ids))
        )
        .scalars()
        .all()
    )
    n_sites = 0
    for site in load.sites:
        if site.id in existing_sites:
            continue
        msi = MineralSiteAndInventory.from_raw_site(
            site,
            commodity_form_conversion=entser.get_commodity_form_conversion(),
            crs_names=entser.get_crs_name(),
            source_score=entser.get_data_source_score(),
        )
        msi.ms.modified_at = time.time_ns()
        msi.ms.dedup_site_id = MineralSite.get_dedup_id([msi.ms.site_id])
        dedup = DedupMineralSite.from_sites([msi], dedup_site_id=msi.ms.dedup_site_id)
        session.add(dedup.dms)
        session.add_all(dedup.invs)
        session.flush()
        session.add(msi.ms)
        session.flush()
        for inv in msi.invs:
            inv.site_id = msi.ms.id
        session.add_all(msi.invs)
        n_sites += 1

    public_ids = [s.id for s in load.samples]
    existing_samples = set(
        session.execute(
            select(Sample.public_id).where(Sample.public_id.in_(public_ids))
        )
        .scalars()
        .all()
    )
    now = format_datetime(datetime.now(timezone.utc))
    new_samples = [
        to_rel_sample(s, now) for s in load.samples if s.id not in existing_samples
    ]
    session.add_all(new_samples)
    session.commit()
    return n_sites, len(new_samples)


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def missing_in_kg(uris: list[str]) -> set[str]:
    present = set()
    for batch in chunks(uris, 500):
        values = " ".join(f"<{u}>" for u in batch)
        rows = MINMOD_KG.query(
            f"SELECT DISTINCT ?s WHERE {{ VALUES ?s {{ {values} }} ?s a ?type }}",
            keys=["s"],
        )
        present.update(str(r["s"]) for r in rows)
    return set(uris) - present


def save_kg(session: Session, load: PaperLoad, batch_size: int) -> tuple[int, int]:
    """Insert triples for sites and samples the triple store doesn't have yet,
    generated from their Postgres rows."""
    site_uris = {NS_MR.uristr(s.id): s.id for s in load.sites}
    sample_uris = {NS_GCR.uristr(s.id): s.id for s in load.samples}
    missing_sites = [site_uris[u] for u in missing_in_kg(list(site_uris))]
    missing_samples = [sample_uris[u] for u in missing_in_kg(list(sample_uris))]

    triples = []
    if missing_sites:
        for ms in session.execute(
            select(MineralSite).where(MineralSite.site_id.in_(missing_sites))
        ).scalars():
            triples.extend(ms.to_kg().to_triples())
    for batch in chunks(missing_samples, 500):
        for sample in session.execute(
            select(Sample).where(Sample.public_id.in_(batch))
        ).scalars():
            triples.extend(sample.to_kg().to_triples())
    if triples:
        MINMOD_KG.batch_insert(triples, batch_size=batch_size)
    return len(missing_sites), len(missing_samples)


@app.command()
def main(
    jsonld_dir: Annotated[Path, typer.Argument(help="Directory of *.jsonld files")],
    data_dir: Annotated[
        Optional[Path],
        typer.Option(
            help="ta2-minmod-data checkout: ISO country codes and curator sample edits"
        ),
    ] = None,
    skip_kg: Annotated[bool, typer.Option(help="Only load Postgres")] = False,
    batch_size: Annotated[int, typer.Option(help="Triples per SPARQL update")] = 5120,
):
    files = sorted(jsonld_dir.glob("*.jsonld"))
    if not files:
        raise typer.BadParameter(f"no *.jsonld files in {jsonld_dir}")

    with Session(engine) as session:
        if session.get(User, USERNAME) is None:
            typer.secho(
                f"warning: user {USERNAME!r} does not exist; the HMI can't edit these "
                "sites until it's created",
                fg="yellow",
            )

    resolver = EntityResolver.build(data_dir / "data/entities" if data_dir else None)
    edits = read_edits(data_dir / "data/geochem-samples" if data_dir else None)

    totals = dict(sites=0, samples=0, kg_sites=0, kg_samples=0)
    skipped, merged = [], []
    for file in tqdm(files, desc="Loading papers"):
        paper = serde.json.deser(file)
        if not clean(paper.get("paper_doi")):
            skipped.append(file.name)
            continue
        load = build_paper(paper, resolver, edits)
        merged.extend(load.merged_samples)
        with Session(engine, expire_on_commit=False) as session:
            n_sites, n_samples = save_postgres(session, load)
            totals["sites"] += n_sites
            totals["samples"] += n_samples
            if not skip_kg:
                kg_sites, kg_samples = save_kg(session, load, batch_size)
                totals["kg_sites"] += kg_sites
                totals["kg_samples"] += kg_samples

    typer.echo(
        f"Postgres: {totals['sites']} sites, {totals['samples']} samples inserted; "
        f"triple store: {totals['kg_sites']} sites, {totals['kg_samples']} samples inserted"
    )
    if skipped:
        typer.echo(f"Skipped {len(skipped)} papers without a DOI: {', '.join(skipped)}")
    if merged:
        typer.echo(
            f"Merged {len(merged)} samples split across nodes, e.g. {merged[:3]}"
        )


if __name__ == "__main__":
    app()
