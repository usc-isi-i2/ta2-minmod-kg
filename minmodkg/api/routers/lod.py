from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Callable, Literal, Optional
from urllib.parse import urlparse, urlunparse

import htbuilder as H
import rdflib
import rdflib.term
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from minmodkg.api.models.public_dedup_mineral_site import DedupMineralSitePublic
from minmodkg.models.kg.base import MINMOD_KG, MINMOD_NS
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.services.mineral_site import MineralSiteService
from minmodkg.typing import IRI
from rdflib import OWL, RDF, RDFS, BNode, Graph
from rdflib import Literal as RDFLiteral
from rdflib import URIRef

router = APIRouter(tags=["lod"])

DO_NOT_FOLLOW_PREDICATE = {
    OWL.sameAs,
    RDF.type,
    MINMOD_NS.mo.uri("normalized_uri"),
    MINMOD_NS.md.uri("commodity"),
    MINMOD_NS.md.uri("dedup_site"),
    MINMOD_NS.md.uri("site"),
}
DO_NOT_FOLLOW_PREDICATE_OBJECT = {}


@lru_cache
def get_hostname():
    u = urlparse(MINMOD_NS.mr.namespace)
    return urlunparse((u.scheme, u.netloc, "", "", "", ""))


@router.get("/resource/{resource_id}")
def get_resource(
    resource_id: str,
    format: Annotated[Literal["html", "json"], Query()] = "html",
    remove_hostname: Annotated[Literal["yes", "no"], Query()] = "no",
):
    uri = MINMOD_NS.mr.uri(resource_id)

    if not MINMOD_KG.has(uri):
        if resource_id.startswith("site__"):
            msi = MineralSiteService().find_by_id(resource_id)
            if msi is not None:
                return render_dict_html(
                    msi.ms.name or "",
                    uri,
                    msi.to_dict(),
                )

        raise HTTPException(status_code=404, detail="Resource not found")

    if format == "html":
        return render_entity_html(
            uri, remove_hostname=get_hostname() if remove_hostname == "yes" else None
        )
    if format == "json":
        return render_entity_json(uri)
    raise HTTPException(status_code=400, detail="Invalid format")


@router.get("/ontology/{resource_id}")
def get_ontology(
    resource_id: str,
    format: Annotated[Literal["html", "json"], Query()] = "html",
    remove_hostname: Annotated[Literal["yes", "no"], Query()] = "no",
):  # -> HTMLResponse | dict[Any, Any]:
    uri = MINMOD_NS.mo.uri(resource_id)
    if not MINMOD_KG.has(uri):
        raise HTTPException(status_code=404, detail="Resource not found")

    if format == "html":
        return render_entity_html(
            uri, remove_hostname=get_hostname() if remove_hostname == "yes" else None
        )
    if format == "json":
        return render_entity_json(uri)
    raise HTTPException(status_code=400, detail="Invalid format")


@router.get("/derived/{resource_id}")
def get_derived(
    resource_id: str,
    format: Annotated[Literal["html", "json"], Query()] = "html",
    remove_hostname: Annotated[Literal["yes", "no"], Query()] = "no",
):  # -> HTMLResponse | dict[Any, Any]:
    if resource_id.startswith("dedup_site__"):
        dmsi = MineralSiteService().find_dedup_by_id(resource_id)
        if dmsi is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        dedup_site = DedupMineralSitePublic.from_kgrel(dmsi, commodity=None)
        if format == "json":
            return dedup_site.to_dict()
        if format == "html":
            derived_key_uri_fmt = {
                "id": lambda id: (
                    MINMOD_NS.md.uri(id),
                    id,
                ),
                "sites.*.id": lambda id: (
                    MINMOD_NS.mr.uri(id),
                    id,
                ),
                "deposit_types.*.id": lambda id: (
                    MINMOD_NS.mr.uri(id),
                    (
                        ent.name
                        if (
                            ent := EntityService.get_instance()
                            .get_deposit_type_idmap()
                            .get(id, None)
                        )
                        else f"{id} (unknown reference)"
                    ),
                ),
                "grade_tonnage.*.commodity": lambda id: (
                    MINMOD_NS.mr.uri(id),
                    (
                        ent.name
                        if (
                            ent := EntityService.get_instance()
                            .get_commodity_idmap()
                            .get(id, None)
                        )
                        else f"{id} (unknown reference)"
                    ),
                ),
                "location.country.*": lambda id: (
                    MINMOD_NS.mr.uri(id),
                    (
                        ent.name
                        if (
                            ent := EntityService.get_instance()
                            .get_country_idmap()
                            .get(id, None)
                        )
                        else f"{id} (unknown reference)"
                    ),
                ),
                "location.state_or_province.*": lambda id: (
                    MINMOD_NS.mr.uri(id),
                    (
                        ent.name
                        if (
                            ent := EntityService.get_instance()
                            .get_state_or_province_idmap()
                            .get(id, None)
                        )
                        else f"{id} (unknown reference)"
                    ),
                ),
            }

            return render_dict_html(
                dedup_site.name,
                MINMOD_NS.md.uri(resource_id),
                dedup_site.to_dict(),
                remove_hostname=get_hostname() if remove_hostname == "yes" else None,
                key2uri=derived_key_uri_fmt,
            )

    uri = MINMOD_NS.md.uri(resource_id)
    if not MINMOD_KG.has(uri):
        raise HTTPException(status_code=404, detail="Resource not found")

    if format == "html":
        return render_entity_html(
            uri,
            remove_hostname=get_hostname() if remove_hostname == "yes" else None,
        )

    if format == "json":
        return render_entity_json(uri)
    raise HTTPException(status_code=400, detail="Invalid format")


def get_entity_data(subj: URIRef) -> Graph:
    # https://stackoverflow.com/questions/37186530/how-do-i-construct-get-the-whole-sub-graph-from-a-given-resource-in-rdf-graph/37213209#37213209
    # adapt from this answer to fit our need
    # we do not blindly follow all paths (<>|!<>)* and filter out the URI nodes because it follows
    # the sameAs path, which leads to explosive results
    return MINMOD_KG.construct(
        """
    CONSTRUCT { 
        ?s ?p ?o . 
        ?p rdfs:label ?pname .
        ?o rdfs:label ?oname .
    }
    WHERE {
        <%s> (!(owl:sameAs|rdf:type))* ?s .
        ?s ?p ?o .
        OPTIONAL { ?o rdfs:label ?oname . }
        OPTIONAL { ?p rdfs:label ?pname .}
    }
"""
        % subj
    )


def render_entity_json(subj: URIRef, g: Optional[Graph] = None):
    if g is None:
        g = get_entity_data(subj)

    def label(obj: rdflib.term.Node):
        if obj == RDFS.label:
            return "@label"
        if obj == RDF.type:
            return "@type"
        assert isinstance(obj, (URIRef, RDFLiteral))
        return obj.n3(g.namespace_manager).rsplit(":", 1)[-1]

    def make_tree(obj: rdflib.term.Node, visited: set):
        if isinstance(obj, RDFLiteral):
            return obj
        if isinstance(obj, URIRef):
            out: dict = {"@id": obj}
        else:
            assert isinstance(obj, BNode)
            out: dict = {}

        if obj in visited:
            # skip visited nodes
            if (obj, RDFS.label, None) in g:
                out["@label"] = next(g.objects(obj, RDFS.label))
            return out

        visited.add(obj)
        for p in g.predicates(obj, unique=True):
            plabel = label(p)
            subobjs = list(g.objects(obj, p))
            fmtsubobjs = [make_tree(subobj, visited) for subobj in subobjs]
            if len(fmtsubobjs) == 1:
                out[plabel] = fmtsubobjs[0]
            else:
                out[plabel] = fmtsubobjs

        return out

    out: dict = {"@id": subj}
    visited = {subj}
    for p in g.predicates(subj, unique=True):
        plabel = label(p)
        objs = list(g.objects(subj, p))
        fmtobjs = [make_tree(obj, visited) for obj in objs]
        if len(fmtobjs) == 1:
            out[plabel] = fmtobjs[0]
        else:
            out[plabel] = fmtobjs
    return out


def render_entity_html(
    subj: URIRef, remove_hostname: Optional[str] = None, g: Optional[Graph] = None
):
    if g is None:
        g = get_entity_data(subj)

    def label(g, subj: rdflib.term.Node):
        if (subj, RDFS.label, None) in g:
            return next(g.objects(subj, RDFS.label))
        assert isinstance(subj, (URIRef, RDFLiteral))
        return subj.n3(g.namespace_manager)

    def get_href(subj: rdflib.term.Node):
        if (
            remove_hostname is not None
            and isinstance(subj, URIRef)
            and subj.startswith(remove_hostname)
        ):
            return subj[len(remove_hostname) :] + "?remove_hostname=yes"
        return subj

    def make_tree(g: Graph, p: rdflib.term.Node, subj: rdflib.term.Node, visited: set):
        if isinstance(subj, RDFLiteral):
            if str(subj).startswith(("http://", "https://")):
                return H.a(href=str(subj), target="_blank")(subj)
            return H.p(subj)

        if isinstance(subj, URIRef):
            if p in DO_NOT_FOLLOW_PREDICATE:
                subj_name = subj.n3(g.namespace_manager)
                if (subj, RDFS.label, None) in g:
                    subj_name = next(g.objects(subj, RDFS.label))
                return H.a(href=get_href(subj))(subj_name)

            if p in DO_NOT_FOLLOW_PREDICATE_OBJECT:
                if any(
                    obj in DO_NOT_FOLLOW_PREDICATE_OBJECT[p]
                    for obj in g.objects(subj, RDF.type)
                ):
                    subj_name = subj.n3(g.namespace_manager)
                    if (subj, RDFS.label, None) in g:
                        subj_name = next(g.objects(subj, RDFS.label))
                    return H.a(href=get_href(subj))(subj_name)

        if subj in visited:
            return H.p(style="font-style: italic")("skiped as visited before")

        visited.add(subj)
        children = []
        if isinstance(subj, URIRef):
            subj_name = subj.n3(g.namespace_manager)
            if (subj, RDFS.label, None) in g:
                subj_name = next(g.objects(subj, RDFS.label))
            children.append(H.tr(H.td(colspan=2)(H.a(href=get_href(subj))(subj_name))))

        for p, o in g.predicate_objects(subj):
            children.append(
                H.tr(
                    H.td(H.a(href=get_href(p))(label(g, p))),
                    H.td(make_tree(g, p, o, visited)),
                )
            )

        return H.table(_class="table")(*children)

    subj_label = label(g, subj)
    visited = {subj}
    children = []
    for p, o in g.predicate_objects(subj):
        if p != RDFS.label:
            children.append(
                (H.a(href=get_href(p))(label(g, p)), make_tree(g, p, o, visited))
            )

    tree = H.div(_class="container-fluid")(
        H.div(_class="row", style="margin-top: 20px; margin-bottom: 20px")(
            H.div(_class="col")(
                H.h4(
                    H.a(href=get_href(subj))(subj_label),
                ),
                H.small(_class="text-muted fw-semibold")(subj),
            )
        ),
        H.table(_class="table table-striped")(
            *[
                H.tr(
                    H.td(p),
                    H.td(o),
                )
                for p, o in children
            ]
        ),
    )
    return HTMLResponse(
        content=f"""
<html>
    <head>
        <title>{subj_label}</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
        <style>a {{ text-decoration: none; }}</style>
    </head>
    <body>
        {tree}
        <script src="https://cdn.jsdelivr.net/npm/@popperjs/core@2.11.8/dist/umd/popper.min.js" integrity="sha384-I7E8VVD/ismYTF4hNIPjVp/Zjvgyol6VFvRkX/vR+Vc4jQkC+hVqc2pM8ODewa9r" crossorigin="anonymous"></script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.min.js" integrity="sha384-0pUGZvbkm6XF6gxjEnlmuGrJXVbNuzT9qBBavbLwCsOGabYfZo0T0to5eqruptLy" crossorigin="anonymous"></script>
    </body>
</html>
                        """,
        status_code=200,
    )


def render_dict_html(
    obj_name: str,
    obj_uri: IRI,
    obj: dict,
    remove_hostname: Optional[str] = None,
    key2uri: Optional[dict[str, Callable]] = None,
):
    if key2uri is None:
        key2uri = {}

    def get_href(subj: IRI):
        if remove_hostname is not None and subj.startswith(remove_hostname):
            return subj[len(remove_hostname) :] + "?remove_hostname=yes"
        return subj

    def make_tree(d: dict | list | str | bool | int | float, current_key: str):
        if not isinstance(d, (dict, list)):
            if current_key in key2uri:
                uri, label = key2uri[current_key](d)
                return H.a(href=get_href(uri))(label)
            else:
                return H.p(d)
        if isinstance(d, list):
            return H.table(_class="table")(
                *[H.tr(H.td(make_tree(x, current_key + ".*"))) for x in d]
            )
        children = []
        for k, v in d.items():
            children.append(
                H.tr(
                    H.td(H.a()(k)),
                    H.td(make_tree(v, current_key + "." + k)),
                )
            )
        return H.table(_class="table")(*children)

    children = []
    for k, v in obj.items():
        children.append((H.a()(k), make_tree(v, k)))

    tree = H.div(_class="container-fluid")(
        H.div(_class="row", style="margin-top: 20px; margin-bottom: 20px")(
            H.div(_class="col")(
                H.h4(
                    H.a(href=get_href(obj_uri))(obj_name),
                ),
                H.small(_class="text-muted fw-semibold")(obj_uri),
            )
        ),
        H.table(_class="table table-striped")(
            *[
                H.tr(
                    H.td(p),
                    H.td(o),
                )
                for p, o in children
            ]
        ),
    )
    return HTMLResponse(
        content=f"""
<html>
    <head>
        <title>{obj_name}</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
        <style>a {{ text-decoration: none; }}</style>
    </head>
    <body>
        {tree}
        <script src="https://cdn.jsdelivr.net/npm/@popperjs/core@2.11.8/dist/umd/popper.min.js" integrity="sha384-I7E8VVD/ismYTF4hNIPjVp/Zjvgyol6VFvRkX/vR+Vc4jQkC+hVqc2pM8ODewa9r" crossorigin="anonymous"></script>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.min.js" integrity="sha384-0pUGZvbkm6XF6gxjEnlmuGrJXVbNuzT9qBBavbLwCsOGabYfZo0T0to5eqruptLy" crossorigin="anonymous"></script>
    </body>
</html>
                        """,
        status_code=200,
    )
