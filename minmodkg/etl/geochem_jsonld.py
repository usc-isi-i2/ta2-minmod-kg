"""GeoChem canonical JSON-LD <-> MinMod models, in both directions.

Reading builds a paper, one mineral site per deposit (owned by the geochem-hmi
system user, so site ids match the HMI's) and the deposits' samples. Writing
applies a site or sample back onto the paper document in place, touching only
the fields MinMod models and leaving everything else in the node alone.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from minmodkg.models.kg.base import NS_GCR, NS_MR
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.location_info import LocationInfo
from minmodkg.models.kg.mineral_inventory import MineralInventory
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kg.reference import BoundingBox, Document, PageInfo, Reference
from minmodkg.models.kg.sample import Analysis, EditEvent, Element
from minmodkg.models.kg.sample import Sample as KGSample
from minmodkg.models.kgrel.paper import Paper
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.transformations import make_site_id
from slugify import slugify

USERNAME = "geochem-hmi"
USER_URI = f"https://minmod.isi.edu/users/s/{USERNAME}"
SOURCE = "GeoChem JSON-LD loader"
WGS84 = "EPSG:4326"

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
    "strat_unit_uid",
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
SAMPLE_FLOAT_FIELDS = ["top_depth_m", "bottom_depth_m"]
# model field -> JSON-LD key
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
DELETION_FIELDS = ["is_deleted", "deleted_by", "deleted_at"]
POINT = re.compile(r"POINT\s*\(\s*(\S+)\s+(\S+)\s*\)", re.IGNORECASE)


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
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v: Any) -> Optional[int]:
    f = to_float(v)
    return int(f) if f is not None else None


def node_id(node: dict) -> Optional[str]:
    return clean(node.get("@id")) or clean(node.get("uri"))


def uid_from_node(node: dict) -> Optional[str]:
    """`@id` tail with the `site__<doi-slug>__` prefix dropped, as the HMI does."""
    raw = node_id(node)
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
    raw = node_id(sample)
    if isinstance(raw, str) and "__sample__" in raw:
        return raw.rsplit("__sample__", 1)[1] or None
    return clean(sample.get("sample_id"))


def paper_source_id(paper: dict) -> Optional[str]:
    doi = clean(paper.get("paper_doi"))
    return f"https://doi.org/{doi}" if doi else None


def deposit_sites(paper: dict) -> list[tuple[str, dict]]:
    """(site_id, deposit node) for every deposit of the paper."""
    source_id = paper_source_id(paper)
    assert source_id is not None
    deposits = paper.get("deposits") or []
    return [
        (make_site_id(USERNAME, source_id, record_id), deposit)
        for deposit, record_id in zip(deposits, deposit_record_ids(deposits))
    ]


def point_wkt(node: dict) -> Optional[str]:
    lat, lon = to_float(node.get("latitude")), to_float(node.get("longitude"))
    if lat is None or lon is None:
        return None
    return f"POINT ({lon} {lat})"


def wkt_lat_lon(wkt: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    m = POINT.match(wkt or "")
    if m is None:
        return None, None
    return float(m.group(2)), float(m.group(1))


@dataclass
class EntityResolver:
    """Maps observed names (and ISO country codes) to MinMod entity URIs."""

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
    is_node = isinstance(node, dict)
    observed = clean(node.get("observed_name")) if is_node else clean(node)
    if is_node and observed is None:
        observed = clean(node.get("rdfs:label"))
    if observed is None:
        return None
    confidence = to_float(node.get("confidence")) if is_node else None
    source = clean(node.get("source")) if is_node else None
    if uri is None and is_node:
        uri = clean(node.get("normalized_uri"))
    if uri is None and index is not None:
        uri = index.get(observed.lower())
    return CandidateEntity(
        source=source or SOURCE,
        confidence=1.0 if confidence is None else min(max(confidence, 0.0), 1.0),
        observed_name=observed,
        normalized_uri=uri,
    )


def deletion(node: dict) -> dict:
    return {
        "is_deleted": clean(node.get("is_deleted")) is True,
        "deleted_by": clean(node.get("deleted_by")),
        "deleted_at": clean(node.get("deleted_at")),
    }


def build_paper(paper: dict, file: str, modified_at: int) -> Paper:
    doi = clean(paper["paper_doi"])
    return Paper(
        paper_id=clean(paper.get("paper_id")) or Path(file).name.split(".")[0],
        source_id=f"https://doi.org/{doi}",
        doi=doi,
        title=clean(paper.get("paper_title")),
        authors=clean(paper.get("paper_authors")),
        journal=clean(paper.get("paper_journal")),
        year=to_int(paper.get("paper_year")),
        url=clean(paper.get("paper_url")),
        file=file,
        modified_at=modified_at,
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
    state = candidate(deposit.get("state"))
    if state is not None and state.normalized_uri is None:
        state.normalized_uri = resolver.state(str(state.observed_name))
    wkt = point_wkt(deposit)
    location_info = None
    if country or state or wkt:
        location_info = LocationInfo(
            country=[country] if country else [],
            state_or_province=[state] if state else [],
            crs=candidate(WGS84, uri=resolver.wgs84) if wkt else None,
            location=wkt,
        )
    deposit_type = candidate(deposit.get("deposit_type"), resolver.deposit_types)
    commodities = [
        candidate(c, resolver.commodities) for c in deposit.get("commodity") or []
    ]
    return KGMineralSite(
        source_id=str(doc.uri),
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
        **deletion(deposit),
    )


def build_element(node: dict, resolver: EntityResolver) -> Optional[Element]:
    label = clean(node.get("rdfs:label")) or clean(node.get("label"))
    if label is None:
        return None
    return Element(
        label=label,
        grade=to_float(node.get("grade")),
        grade_unit=candidate(node.get("grade_unit"), resolver.units),
        detection_limit=to_float(node.get("detection_limit")),
        detection_limit_unit=candidate(
            node.get("detection_limit_unit"), resolver.units
        ),
        **deletion(node),
    )


def build_analysis(node: dict, resolver: EntityResolver) -> Analysis:
    elements = [build_element(e, resolver) for e in node.get("element") or []]
    return Analysis(
        analysis_id=clean(node.get("analysis_id")),
        elements=[e for e in elements if e is not None],
        **{k: clean(node.get(src)) for k, src in ANALYSIS_FIELDS.items()},
        **deletion(node),
    )


def build_sample(
    node: dict, key: str, site_id: str, doc: Document, resolver: EntityResolver
) -> KGSample:
    wkt = point_wkt(node)
    return KGSample(
        sample_id=key,
        mineral_site_id=site_id,
        location_info=(
            LocationInfo(crs=candidate(WGS84, uri=resolver.wgs84), location=wkt)
            if wkt
            else None
        ),
        analyses=[build_analysis(a, resolver) for a in node.get("analyses") or []],
        reference=[Reference(document=doc)],
        edit_history=[EditEvent.from_dict(e) for e in node.get("edit_history") or []],
        **{k: clean(node.get(k)) for k in SAMPLE_FIELDS},
        **{k: to_float(node.get(k)) for k in SAMPLE_FLOAT_FIELDS},
        **deletion(node),
    )


@dataclass
class PaperContent:
    sites: list[KGMineralSite]
    samples: list[KGSample]
    merged_samples: list[str]


def read_paper(paper: dict, resolver: EntityResolver) -> PaperContent:
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
        samples.extend(by_id.values())
    return PaperContent(sites, samples, merged)


def put(node: dict, key: str, value: Any) -> None:
    """Set a field only when its value changed, so wrapped values (evidence
    quotes etc.) survive when untouched."""
    current = node.get(key)
    if isinstance(value, bool):
        if (clean(current) is True) == value:
            return
    elif isinstance(value, float):
        if to_float(current) == value:
            return
    elif clean(current) == value:
        return
    if value is None:
        node.pop(key, None)
    else:
        node[key] = value


def put_deletion(node: dict, obj: Any) -> None:
    for k in DELETION_FIELDS:
        put(node, k, getattr(obj, k))


def put_candidate(node: dict, key: str, ce: Optional[CandidateEntity]) -> None:
    current = candidate(node.get(key))
    if ce is None:
        node.pop(key, None)
        return
    if current is not None and (
        current.observed_name,
        current.normalized_uri,
        current.source,
        current.confidence,
    ) == (ce.observed_name, ce.normalized_uri, ce.source, ce.confidence):
        return
    existing = node.get(key) if isinstance(node.get(key), dict) else {}
    node[key] = {
        **existing,
        "observed_name": ce.observed_name,
        "source": ce.source,
        "confidence": ce.confidence,
        **({"normalized_uri": ce.normalized_uri} if ce.normalized_uri else {}),
    }
    if not ce.normalized_uri:
        node[key].pop("normalized_uri", None)


def put_unit(node: dict, key: str, ce: Optional[CandidateEntity]) -> None:
    current = candidate(node.get(key))
    if ce is None:
        node.pop(key, None)
        return
    # a unit is identified by its label; its uri is resolved from it on load
    if current is not None and current.observed_name == ce.observed_name:
        return
    node[key] = {
        "@type": "mo:UnitCandidate",
        "rdfs:label": ce.observed_name,
        "source": ce.source,
        "confidence": ce.confidence,
        **({"normalized_uri": ce.normalized_uri} if ce.normalized_uri else {}),
    }


def put_point(node: dict, wkt: Optional[str]) -> None:
    lat, lon = wkt_lat_lon(wkt)
    if (to_float(node.get("latitude")), to_float(node.get("longitude"))) == (lat, lon):
        return
    put(node, "latitude", None if lat is None else str(lat))
    put(node, "longitude", None if lon is None else str(lon))


def find_deposit(paper: dict, site_id: str) -> Optional[dict]:
    return next((d for sid, d in deposit_sites(paper) if sid == site_id), None)


def apply_site(paper: dict, site: KGMineralSite) -> None:
    """Write a site onto its deposit node, appending one for a new site."""
    deposit = find_deposit(paper, site.id)
    if deposit is None:
        doi_slug = slugify(str(clean(paper.get("paper_doi"))))
        uri = NS_GCR.uristr(f"site__{doi_slug}__{site.record_id}")
        deposit = {"@id": uri, "@type": "mo:MineralSite", "uri": uri, "samples": []}
        paper.setdefault("deposits", []).append(deposit)

    put(deposit, "name", site.name)
    loc = site.location_info
    put_candidate(deposit, "country", loc.country[0] if loc and loc.country else None)
    put_candidate(
        deposit,
        "state",
        loc.state_or_province[0] if loc and loc.state_or_province else None,
    )
    put_point(deposit, loc.location if loc else None)
    put_candidate(
        deposit,
        "deposit_type",
        site.deposit_type_candidate[0] if site.deposit_type_candidate else None,
    )
    existing = {
        clean(c.get("observed_name")): c
        for c in deposit.get("commodity") or []
        if isinstance(c, dict)
    }
    commodities = []
    for inv in site.mineral_inventory:
        c = inv.commodity
        entry = {**existing.get(c.observed_name, {}), "observed_name": c.observed_name}
        if c.normalized_uri:
            entry["normalized_uri"] = c.normalized_uri
        else:
            entry.pop("normalized_uri", None)
        commodities.append(entry)
    if commodities != (deposit.get("commodity") or []):
        if commodities:
            deposit["commodity"] = commodities
        else:
            deposit.pop("commodity", None)
    put_deletion(deposit, site)


def apply_sample(paper: dict, sample: KGSample) -> None:
    """Write a sample onto its node(s), appending nodes for new samples,
    analyses and elements."""
    deposit = find_deposit(paper, sample.mineral_site_id)
    if deposit is None:
        raise KeyError(f"no deposit for site {sample.mineral_site_id}")
    samples = deposit.setdefault("samples", [])
    nodes = [n for n in samples if sample_key(n) == sample.sample_id]
    if not nodes:
        uri = f"{node_id(deposit)}__sample__{sample.sample_id}"
        nodes = [{"@id": uri, "@type": "geochem:Sample", "uri": uri, "analyses": []}]
        samples.append(nodes[0])
    first = nodes[0]

    put(first, "sample_id", clean(first.get("sample_id")) or sample.sample_id)
    for k in SAMPLE_FIELDS + SAMPLE_FLOAT_FIELDS:
        put(first, k, getattr(sample, k))
    put_point(first, sample.location_info.location if sample.location_info else None)
    put_deletion(first, sample)
    if sample.edit_history:
        first["edit_history"] = [e.to_dict() for e in sample.edit_history]

    analysis_nodes = {
        clean(a.get("analysis_id")): a for n in nodes for a in n.get("analyses") or []
    }
    for analysis in sample.analyses:
        anode = analysis_nodes.get(analysis.analysis_id)
        if anode is None:
            uri = f"{node_id(first)}__analysis__{slugify(str(analysis.analysis_id))}"
            anode = {
                "@id": uri,
                "@type": "geochem:Analysis",
                "analysis_id": analysis.analysis_id,
                "element": [],
            }
            nodes[-1].setdefault("analyses", []).append(anode)
            analysis_nodes[analysis.analysis_id] = anode
        apply_analysis(anode, analysis)


def apply_analysis(anode: dict, analysis: Analysis) -> None:
    for k, src in ANALYSIS_FIELDS.items():
        put(anode, src, getattr(analysis, k))
    put_deletion(anode, analysis)
    element_nodes: dict[str, dict] = {}
    for e in anode.get("element") or []:
        element_nodes.setdefault(clean(e.get("rdfs:label")), e)
    for element in analysis.elements:
        enode = element_nodes.get(element.label)
        if enode is None:
            uri = f"{node_id(anode)}__element__{slugify(element.label)}"
            enode = {
                "@id": uri,
                "@type": "geochem:Element",
                "rdfs:label": element.label,
            }
            anode.setdefault("element", []).append(enode)
            element_nodes[element.label] = enode
        put(enode, "grade", element.grade)
        put_unit(enode, "grade_unit", element.grade_unit)
        put(enode, "detection_limit", element.detection_limit)
        put_unit(enode, "detection_limit_unit", element.detection_limit_unit)
        put_deletion(enode, element)


def read_paper_file(path: Path) -> dict:
    return json.loads(path.read_text())


def write_paper_file(path: Path, paper: dict) -> None:
    # same serialization as the extraction output, so diffs stay minimal
    path.write_text(json.dumps(paper, indent=2))
