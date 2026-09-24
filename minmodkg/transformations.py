from __future__ import annotations

import hashlib
from uuid import uuid4

from minmodkg.models.kg.base import MINMOD_KG
from minmodkg.models.kg.reference import PageInfo
from minmodkg.models.kgrel.user import get_username, is_valid_user_uri
from slugify import slugify

MR_NS = MINMOD_KG.ns.mr.namespace
MO_NS = MINMOD_KG.ns.mo.namespace


def make_site_ids(value: dict, namespace: str = MR_NS):
    """Make all ids for a mineral site"""
    assert is_valid_user_uri(value["created_by"])
    username = get_username(value["created_by"])
    site_uri = namespace + make_site_id(
        username, value["source_id"], value["record_id"]
    )
    site_id = site_uri[len(namespace) :] + "__user_" + slugify(username) + "__"

    # we create id for each reference as it is likely to be the same or re-ordered
    value["id"] = site_uri
    for ref in value["reference"]:
        make_reference_ids(ref, site_id, namespace)
    for inv in value.get("mineral_inventory", []):
        make_reference_ids(inv["reference"], site_id, namespace)

    # for rest, we create id based on its index
    if "location_info" in value:
        make_location_info_ids(value["location_info"], site_id, namespace)

    for deptype_i, deptype in enumerate(value.get("deposit_type_candidate", [])):
        deptype["id"] = f"{namespace}{site_id}__deptype__{deptype_i}"

    for inv_i, inv in enumerate(value.get("mineral_inventory", [])):
        make_inventory_ids(inv, inv_i, site_id, namespace)

    value["source_uri"] = get_source_uri(value["source_id"])

    if "geology_info" in value:
        value["geology_info"]["id"] = f"{namespace}{site_id}__geology"
        if "host_rock" in value["geology_info"]:
            value["geology_info"]["host_rock"][
                "id"
            ] = f"{namespace}{site_id}__geology__host_rock"
        if "associated_rock" in value["geology_info"]:
            value["geology_info"]["associated_rock"][
                "id"
            ] = f"{namespace}{site_id}__geology__associated_rock"


def get_source_uri(source_id: str):
    return "https://minmod.isi.edu/resource/source__" + slugify(source_id)


def make_location_info_ids(loc_info: dict, site_id: str, namespace: str = MR_NS):
    loc_info["id"] = namespace + site_id + "__location_info"
    for i, country in enumerate(loc_info.get("country", [])):
        country["id"] = namespace + site_id + f"__country__{i}"
    for i, state in enumerate(loc_info.get("state_or_province", [])):
        state["id"] = namespace + site_id + f"__state__{i}"
    if loc_info.get("crs") is not None:
        loc_info["crs"]["id"] = namespace + site_id + f"__crs"


def make_inventory_ids(inv: dict, inv_index: int, site_id: str, namespace: str = MR_NS):
    inv_id = f"{site_id}__inv__{inv_index}"
    inv["id"] = f"{namespace}{inv_id}"
    for cat_idx, cat in enumerate(inv.get("category", [])):
        cat["id"] = f"{namespace}{inv_id}__cat__{cat_idx}"
    for key in ["commodity", "material_form"]:
        if inv.get(key) is not None:
            inv[key]["id"] = f"{namespace}{inv_id}__{key}"
    for key in ["cutoff_grade", "grade", "ore"]:
        if inv.get(key) is not None:
            inv[key]["id"] = f"{namespace}{inv_id}__{key}"
            if inv[key].get("unit") is not None:
                inv[key]["unit"]["id"] = f"{namespace}{inv_id}__{key}__unit"
    make_reference_ids(inv["reference"], site_id, namespace)


def make_reference_ids(ref: dict, site_id: str, namespace: str = MR_NS):
    ref["document"]["id"] = make_document_uri(ref["document"], site_id, namespace)

    docid = ref["document"]["id"]
    if docid.startswith(namespace):
        docid = docid[len(namespace) :]
    elif docid.startswith("https://"):
        docid = docid[8:]
    elif docid.startswith("http://"):
        docid = docid[7:]
    docid = slugify(docid)

    ref["id"] = make_reference_uri(ref, slugify(docid), namespace)
    for i, pageinfo in enumerate(ref.get("page_info", [])):
        pageinfo["id"] = f"{ref['id']}__pageinfo__{i}"


def make_site_id(username: str, source_id: str, record_id: str) -> str:
    assert source_id.find("::") == -1, source_id
    assert isinstance(record_id, str) and record_id == record_id.strip(), record_id

    if source_id.startswith("http://"):
        if source_id.startswith("http://"):
            source_id = source_id[7:]
            if source_id.endswith("/"):
                source_id = source_id[:-1]
    elif source_id.startswith("https://"):
        source_id = source_id[8:]
        if source_id.endswith("/"):
            source_id = source_id[:-1]

    source_id = slugify(source_id)
    record_id = slugify(record_id)

    path = shorten_id(f"{source_id}__{record_id}", 120) + f"__{username}"
    return f"site__{path}"


def make_sample_id(parent_site_id: str, sample_id: str) -> str:
    """Make an internal id for a GeoChem sample, scoped by its parent mineral site.

    :sample_id (the ontology property) is source-provided data, not guaranteed globally
    unique on its own -- so the actual lookup/URI key combines it with the parent site's
    already-unique site_id, mirroring how make_site_id scopes source_id+record_id.
    """
    assert isinstance(sample_id, str) and sample_id == sample_id.strip(), sample_id
    path = shorten_id(f"{parent_site_id}__{slugify(sample_id)}", 120)
    return f"sample__{path}"


def make_site_uri_deprecated(
    source_id: str, record_id: str | int, namespace: str = MR_NS
) -> str:
    if source_id.find("::") != -1:
        # we need to remove the category from the source_id -- note that the new `source_id`
        # may contain other `::` at the end to add username, etc; and we want the username to be part of the site id
        # otherwise, we may have duplicated site ids
        category, source_id = source_id.split("::", 1)
        assert category in {"mining-report", "article", "database", "unpublished"}

    if source_id.startswith("http://"):
        if source_id.startswith("http://"):
            source_id = source_id[7:]
            if source_id.endswith("/"):
                source_id = source_id[:-1]
    elif source_id.startswith("https://"):
        source_id = source_id[8:]
        if source_id.endswith("/"):
            source_id = source_id[:-1]

    source_id = slugify(source_id)

    if isinstance(record_id, int):
        record_id = str(record_id)
    else:
        record_id = slugify(record_id)

    path = shorten_id(f"{source_id}__{record_id}", 120)
    return f"{namespace}site__{path}"


def make_document_uri(doc: dict, site_id: str, namespace: str = MR_NS):
    if "uri" in doc:
        return doc["uri"]
    if "doi" in doc:
        assert not (
            doc["doi"].startswith("https://")
            or doc["doi"].startswith("http://")
            or doc["doi"].startswith("doi:")
            or doc["doi"].startswith("/")
        )
        return "https://doi.org/" + doc["doi"]

    if "title" not in doc:
        raise ValueError("Document must have a URI, DOI, or at least a title")

    path = site_id + "__doc__" + shorten_id(slugify(doc["title"]), 120)
    return f"{namespace}{path}"


def make_reference_uri(ref: dict, doc_id: str, namespace: str = MR_NS):
    # gen pageinfo id
    if len(ref.get("page_info", [])) > 0:
        pageinfo_id = hashlib.sha256(
            b"|".join(
                (
                    PageInfo.from_dict(page_info).to_enc_str().encode()
                    for page_info in ref["page_info"]
                )
            )
        ).hexdigest()[:16]
    else:
        pageinfo_id = ""

    if "property" in ref:
        property = ref["property"]
        if property.startswith(MO_NS):
            property = property[len(MO_NS) :]
        elif property.startswith("http://www.w3.org/2000/01/rdf-schema#"):
            property = f"rdfs_{property[37:]}"
        else:
            raise NotImplementedError(property)
    else:
        property = ""

    constraintinfo = property + "_" + pageinfo_id
    if len(constraintinfo) == 1:
        return namespace + doc_id + "__ref"
    else:
        return namespace + doc_id + "__ref__" + shorten_id(slugify(constraintinfo), 120)


def get_uuid4(prefix: str = "B", namespace: str = MR_NS):
    return f"{namespace}{prefix}_{str(uuid4()).replace('-', '')}"


def shorten_id(long_id: str, max_length: int = 120):
    if len(long_id) > max_length:
        return (
            long_id[:max_length]
            + "__"
            + hashlib.sha256(long_id.encode()).hexdigest()[:8]
        )
    return long_id
