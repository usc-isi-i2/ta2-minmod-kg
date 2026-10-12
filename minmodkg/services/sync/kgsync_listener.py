from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Literal, Optional, Sequence

from minmodkg.misc.utils import norm_literal
from minmodkg.models.kg.base import MINMOD_KG, MINMOD_NS, NS_GCO, NS_GCR, NS_MR
from minmodkg.models.kg.mineral_site import MineralSite
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.services.sync.geochem_routing import GeoChemRouting
from minmodkg.services.sync.listener import Listener
from minmodkg.typing import IRI, InternalID
from rdflib import Graph, URIRef


class KGSyncListener(Listener):
    owl_same_as = MINMOD_NS.owl.sameAs
    rdf_type = MINMOD_NS.rdf.type
    mo_normalized_uri = MINMOD_NS.mo.normalized_uri
    gco_has_sample = f"<{NS_GCO.uristr('has_sample')}>"

    def __init__(self, lane: Literal["minmod", "geochem"] = "minmod"):
        """`minmod` syncs every edit except those of GeoChem papers, which the
        GeoChem sync process handles with its own `geochem` lane."""
        super().__init__()
        self.lane = lane

    def handle_begin(self, events: Sequence[EventLog]):
        self.routing = GeoChemRouting()

    def owns(self, paper: Optional[Paper]) -> bool:
        return (paper is not None) == (self.lane == "geochem")

    def handle_site_add(
        self,
        event: EventLog,
        site: MineralSiteAndInventory,
        same_site_ids: list[InternalID],
    ):
        paper = self.routing.site_paper(site)
        if not self.owns(paper):
            return
        key_ns = MineralSite.__subj__.key_ns
        triples = site.ms.to_kg().to_triples()
        for same_site_id in same_site_ids:
            triples.append(
                (key_ns[site.ms.site_id], self.owl_same_as, key_ns[same_site_id])
            )
        if paper is not None:
            triples.append(
                (
                    f"<{paper.to_kg([]).uri}>",
                    f"<{NS_GCO.uristr('has_mineral_site')}>",
                    f"<{NS_MR.uristr(site.ms.site_id)}>",
                )
            )
        MINMOD_KG.insert(triples)

    def handle_site_update(self, event: EventLog, site: MineralSiteAndInventory):
        if not self.owns(self.routing.site_paper(site)):
            return
        kgms = site.ms.to_kg()
        ng = kgms.to_graph()
        og = self._get_mineral_site_graph_by_uri(kgms.uri)

        self._sync_graph(og, ng)

    def handle_same_as_update(
        self,
        event: EventLog,
        user_uri: str,
        groups: list[list[InternalID]],
        diff_groups: dict[InternalID, list[InternalID]],
    ):
        if self.lane != "minmod":
            return
        key_ns = MineralSite.__subj__.key_ns
        # potential_existing_links = self._get_all_same_as_links(
        #     {id for group in groups for id in group}
        # )
        # delete same as link to/from other sites, and then insert the new same as links
        delete_links = []
        for site, diff_sites in diff_groups.items():
            s = key_ns[site]
            for diff_site in diff_sites:
                o = key_ns[diff_site]
                delete_links.append((s, self.owl_same_as, o))
                delete_links.append((o, self.owl_same_as, s))

        MINMOD_KG.delete_insert(
            delete_links,
            [
                (key_ns[group[0]], self.owl_same_as, key_ns[target])
                for group in groups
                for target in group[1:]
            ],
        )

    def handle_sample_add(self, event: EventLog, sample: Sample):
        if not self.owns(self.routing.sample_paper(sample)):
            return
        triples = sample.to_kg().to_triples()
        triples.append(
            (
                f"<{NS_MR.uristr(sample.mineral_site_id)}>",
                f"<{NS_GCO.uristr('has_sample')}>",
                f"<{NS_GCR.uristr(sample.public_id)}>",
            )
        )
        MINMOD_KG.insert(triples)

    def handle_sample_update(self, event: EventLog, sample: Sample):
        if not self.owns(self.routing.sample_paper(sample)):
            return
        kgsample = sample.to_kg()
        ng = kgsample.to_graph()
        og = self._get_sample_graph_by_uri(kgsample.uri)

        self._sync_graph(og, ng)

    def _sync_graph(self, og: Graph, ng: Graph):
        """Replace the stored graph `og` with the model's graph `ng`.

        `norm_literal` is only the comparison key. We delete the terms exactly
        as they are stored and insert the model's own terms: deleting the
        normalized `xsd:double` terms never matched the stored `xsd:decimal`
        ones and left them behind as orphans (#107).
        """
        old, new = defaultdict(list), defaultdict(list)
        for g, terms in [(og, old), (ng, new)]:
            for s, p, o in g:
                terms[(s, p, norm_literal(o))].append((s, p, o))

        ns_manager = MINMOD_KG.ns.rdflib_namespace_manager
        MINMOD_KG.delete_insert(
            [
                (s.n3(ns_manager), p.n3(ns_manager), o.n3(ns_manager))
                for key in old.keys() - new.keys()
                for s, p, o in old[key]
            ],
            [
                (s.n3(ns_manager), p.n3(ns_manager), o.n3(ns_manager))
                for key in new.keys() - old.keys()
                for s, p, o in new[key]
            ],
        )

    def _get_sample_graph_by_uri(self, uri: IRI | URIRef) -> Graph:
        # Sample has no owl:sameAs of its own, but excluding it here too is harmless
        # and keeps this consistent with _get_mineral_site_graph_by_uri above.
        return MINMOD_KG.construct(
            f"""
CONSTRUCT {{
    ?s ?p ?o
}}
WHERE {{
    <{uri}> (!({self.owl_same_as}|{self.rdf_type}|{self.mo_normalized_uri}))* ?s .
    ?s ?p ?o .

    FILTER (?p != {self.owl_same_as})
}}
"""
        )

    def _get_all_same_as_links(
        self, ids: Iterable[InternalID]
    ) -> list[tuple[InternalID, InternalID]]:
        key_ns = MineralSite.__subj__.key_ns

        # retrieve all the sameAs relations for the affected entities
        lst = MINMOD_KG.query(
            """
SELECT ?s ?o
WHERE {
    ?s (%s|^%s) ?o .
    VALUES ?s { %s }
}
"""
            % (self.owl_same_as, self.owl_same_as, " ".join(key_ns[id] for id in ids)),
            keys=["s", "o"],
        )
        return [(key_ns.abs2rel(so["s"]), key_ns.abs2rel(so["o"])) for so in lst]

    def _get_mineral_site_graph_by_uri(self, uri: IRI | URIRef) -> Graph:
        # Fuseki can optimize this case, but I don't know why sometimes it cannot
        return MINMOD_KG.construct(
            f"""
CONSTRUCT {{
    ?s ?p ?o
}}
WHERE {{
    <{uri}> (!({self.owl_same_as}|{self.rdf_type}|{self.mo_normalized_uri}|{self.gco_has_sample}))* ?s .
    ?s ?p ?o .

    # Exclude owl:sameAs because it's not part of the model, and the site's
    # samples, which are synced on their own
    FILTER (?p != {self.owl_same_as} && ?p != {self.gco_has_sample})
}}
"""
        )
