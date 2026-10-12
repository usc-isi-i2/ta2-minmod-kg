from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from minmodkg.api.models.public_mineral_site import InputPublicMineralSite
from minmodkg.libraries.rdf.triple_store import TripleStore
from minmodkg.misc.utils import assert_not_none
from minmodkg.models.kg.measure import Measure
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kgrel.sample import Sample as RelSample
from minmodkg.models.kgrel.user import User
from minmodkg.services.mineral_site import MineralSiteService
from minmodkg.services.sample import SampleService
from minmodkg.services.sync.kgsync_listener import KGSyncListener
from minmodkg.services.sync.sync import process_pending_events
from rdflib import XSD, Graph, Literal
from sqlalchemy import Engine


class TestKGSyncListener:
    def test_add_site(
        self, kg: TripleStore, kgrel: Engine, sync_site1: InputPublicMineralSite
    ):
        service = MineralSiteService(kgrel)
        kgsync_listener = KGSyncListener()

        relsite1 = sync_site1.to_kgrel(sync_site1.created_by)
        # create a new mineral site
        service.create(relsite1)

        # fetch the mineral site from the triple store and we find nothing
        assert not kg.has(sync_site1.uri)

        # the listener is triggered and the mineral site is added to the triple store
        process_pending_events(kgsync_listener)

        # fetch the mineral site from the triple store and we find it
        assert kg.has(sync_site1.uri)
        kgsite = KGMineralSite.from_graph(
            sync_site1.uri,
            kgsync_listener._get_mineral_site_graph_by_uri(sync_site1.uri),
        )
        assert relsite1.ms.to_kg() == kgsite

    def test_update_site(
        self,
        kg: TripleStore,
        kgrel: Engine,
        user1: User,
        sync_site1_update_name_and_inventory: InputPublicMineralSite,
    ):
        service = MineralSiteService(kgrel)
        kgsync_listener = KGSyncListener()

        # this is continue from the previous test -- update existing mineral site
        rel_site1 = sync_site1_update_name_and_inventory.to_kgrel(
            sync_site1_update_name_and_inventory.created_by
        )
        rel_site1.set_id(
            assert_not_none(
                service.get_site_db_id(sync_site1_update_name_and_inventory.id)
            )
        )
        service.update(rel_site1)

        # fetch the mineral site from the triple store and we find it
        assert kg.has(rel_site1.ms.site_uri)
        kgsite = KGMineralSite.from_graph(
            rel_site1.ms.site_uri,
            kgsync_listener._get_mineral_site_graph_by_uri(rel_site1.ms.site_uri),
        )
        assert rel_site1.ms.to_kg() != kgsite

        # the listener is triggered and the mineral site is updated in the triple store
        process_pending_events(kgsync_listener)

        kgsite = KGMineralSite.from_graph(
            rel_site1.ms.site_uri,
            kgsync_listener._get_mineral_site_graph_by_uri(rel_site1.ms.site_uri),
        )
        assert rel_site1.ms.to_kg() == kgsite

    def test_update_same_as(
        self,
        kg: TripleStore,
        kgrel: Engine,
        sync_site1_update_name_and_inventory: InputPublicMineralSite,
        sync_site2: InputPublicMineralSite,
    ):
        service = MineralSiteService(kgrel)
        kgsync_listener = KGSyncListener()

        # first, make sure that we have two mineral sites in the database
        service.create(sync_site2.to_kgrel(sync_site2.created_by))

        service.update_same_as(
            sync_site2.created_by,
            [[sync_site1_update_name_and_inventory.id, sync_site2.id]],
        )

        # fetch the mineral sites from the triple store
        process_pending_events(kgsync_listener)

        existing_links = kgsync_listener._get_all_same_as_links(
            [sync_site1_update_name_and_inventory.id, sync_site2.id]
        )
        key_ns = KGMineralSite.__subj__.key_ns
        assert existing_links == [
            (key_ns[sync_site1_update_name_and_inventory.id], key_ns[sync_site2.id]),
            (key_ns[sync_site2.id], key_ns[sync_site1_update_name_and_inventory.id]),
        ]


def count_doubles(kg: TripleStore) -> int:
    return kg.query(
        "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o FILTER(DATATYPE(?o) = xsd:double) }"
    )[0]["n"]


def untyped_orphans(kg: TripleStore) -> list[dict]:
    return kg.query(
        """
SELECT DISTINCT ?s WHERE {
    ?s ?p ?o .
    FILTER NOT EXISTS { ?s a ?type }
    FILTER NOT EXISTS { ?x ?q ?s }
}"""
    )


def numbers(g: Graph) -> list[tuple[str, Decimal]]:
    return sorted(
        (str(p), Decimal(str(o)))
        for _, p, o in g
        if isinstance(o, Literal) and o.datatype in {XSD.decimal, XSD.double, XSD.float}
    )


def assert_stored_as_model(kg: TripleStore, stored: Graph, model: Graph):
    assert count_doubles(kg) == 0
    assert untyped_orphans(kg) == []
    assert {
        o.datatype
        for _, _, o in stored
        if isinstance(o, Literal) and o.datatype in {XSD.decimal, XSD.double, XSD.float}
    } == {XSD.decimal}
    assert numbers(stored) == numbers(model)


class TestKGSyncSiteDatatypes:
    """Site updates must not retype numbers or strand the old ones (#107)."""

    def test_site_saved_twice(
        self,
        kg: TripleStore,
        kgrel: Engine,
        sync_site1_update_name_and_inventory: InputPublicMineralSite,
    ):
        service = MineralSiteService(kgrel)
        kgsync_listener = KGSyncListener()
        site = sync_site1_update_name_and_inventory

        rel_site = site.to_kgrel(site.created_by)
        service.create(rel_site)
        process_pending_events(kgsync_listener)
        assert_stored_as_model(
            kg,
            kgsync_listener._get_mineral_site_graph_by_uri(rel_site.ms.site_uri),
            rel_site.ms.to_kg().to_graph(),
        )

        site = deepcopy(site)
        site.name = "Toad Mine"
        for _ in range(2):
            rel_site = site.to_kgrel(site.created_by)
            rel_site.set_id(assert_not_none(service.get_site_db_id(site.id)))
            service.update(rel_site)
            process_pending_events(kgsync_listener)
            assert_stored_as_model(
                kg,
                kgsync_listener._get_mineral_site_graph_by_uri(rel_site.ms.site_uri),
                rel_site.ms.to_kg().to_graph(),
            )


class TestKGSyncSampleDatatypes:
    """Sample updates must not retype numbers or strand the old ones (#107)."""

    def test_sample_saved_twice(
        self,
        kg: TripleStore,
        kgrel: Engine,
        sync_site2: InputPublicMineralSite,
    ):
        MineralSiteService(kgrel).create(sync_site2.to_kgrel(sync_site2.created_by))
        service = SampleService(kgrel)
        kgsync_listener = KGSyncListener()

        raw_sample = {
            "public_id": "",
            "mineral_site_id": sync_site2.id,
            "sample_id": "S1",
            "sample_name": "kawazulite",
            "top_depth_m": 12.5,
            "bottom_depth_m": 13.0,
            "analyses": [
                {
                    "analysis_id": "A1",
                    "elements": [
                        {"label": "Te", "grade": 29.33, "detection_limit": 0.04}
                    ],
                }
            ],
            "modified_at": 0,
        }
        sample = service.create(RelSample.from_dict(raw_sample), sync_site2.created_by)
        process_pending_events(kgsync_listener)
        uri = sample.to_kg().uri
        assert_stored_as_model(
            kg,
            kgsync_listener._get_sample_graph_by_uri(uri),
            sample.to_kg().to_graph(),
        )

        raw_sample["public_id"] = sample.public_id
        for change in [{"sample_name": "kawazulite (edited)"}, {"top_depth_m": 12.75}]:
            raw_sample.update(change)
            sample = RelSample.from_dict(raw_sample).set_id(
                assert_not_none(service.get_sample_db_id(sample.public_id))
            )
            service.update(sample, sync_site2.created_by)
            process_pending_events(kgsync_listener)
            assert_stored_as_model(
                kg,
                kgsync_listener._get_sample_graph_by_uri(uri),
                sample.to_kg().to_graph(),
            )

        assert kg.query(
            "SELECT ?depth WHERE { <%s> gco:top_depth_m ?depth }" % uri
        ) == [{"depth": 12.75}]


def test_small_decimal_written_without_exponent():
    def written(value: float):
        triples = [o for _, _, o in Measure(value=value).to_triples()]
        graph = [o.n3() for o in Measure(value=value).to_graph().objects()]
        return triples, graph

    decimal = "^^<http://www.w3.org/2001/XMLSchema#decimal>"
    for value, lexical in [(5e-05, "0.00005"), (1.25, "1.25"), (1.0, "1.0")]:
        triples, graph = written(value)
        assert f'"{lexical}"^^xsd:decimal' in triples
        assert f'"{lexical}"{decimal}' in graph
