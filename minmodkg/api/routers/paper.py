from __future__ import annotations

import hashlib
from typing import Annotated, Optional

from fastapi import (
    APIRouter,
    Body,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from minmodkg.api.dependencies import (
    CurrentUserDep,
    PaperServiceDep,
    norm_commodity,
    norm_country_or_alias,
    norm_deposit_type,
    norm_element,
    norm_state_or_province,
)
from minmodkg.api.models.public_mineral_site import OutputPublicMineralSite
from minmodkg.api.models.public_sample import OutputPublicSample
from minmodkg.models.kgrel.paper import Paper
from minmodkg.config import GEOCHEM_ENTITY_DIR, GEOCHEM_JSONLD_DIR
from minmodkg.services.paper import PaperConflictError, PaperService
from minmodkg.typing import InternalID

router = APIRouter(tags=["papers"])

ElementQuery = Annotated[
    Optional[str],
    Query(
        description="An element measured in the samples: symbol or name, e.g. Ga or Gallium."
    ),
]
IncludeBdlQuery = Annotated[
    bool,
    Query(
        description="Also match readings below the detection limit. Needs `element`."
    ),
]


def paper_dict(paper: Paper) -> dict:
    d = paper.to_dict()
    d.pop("file", None)
    return d


def norm_element_filter(element: Optional[str], include_bdl: bool) -> Optional[str]:
    if element is None:
        if include_bdl:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="include_bdl needs element",
            )
        return None
    return norm_element(element)


def get_paper_or_404(paper_service: PaperService, paper_id: str) -> Paper:
    paper = paper_service.find_by_id(paper_id)
    if paper is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested paper does not exist.",
        )
    return paper


@router.get("/papers")
def list_papers(
    paper_service: PaperServiceDep,
    commodity: Optional[str] = None,
    element: ElementQuery = None,
    include_bdl: IncludeBdlQuery = False,
    deposit_type: Optional[str] = None,
    country: Optional[str] = None,
    state_or_province: Optional[str] = None,
    dedup_site_id: Optional[InternalID] = None,
    site_id: Optional[InternalID] = None,
    limit: Annotated[int, Query(ge=0)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    return_count: Annotated[bool, Query()] = False,
):
    """Search papers: those with a site matching every filter, each with its
    matching sites. Entity filters take a name or a MinMod id; country also
    an ISO alpha-2/alpha-3 code."""
    results, total = paper_service.find_papers(
        commodity=norm_commodity(commodity) if commodity is not None else None,
        element=norm_element_filter(element, include_bdl),
        include_bdl=include_bdl,
        deposit_type=(
            norm_deposit_type(deposit_type) if deposit_type is not None else None
        ),
        country=norm_country_or_alias(country) if country is not None else None,
        state_or_province=(
            norm_state_or_province(state_or_province)
            if state_or_province is not None
            else None
        ),
        dedup_site_id=dedup_site_id,
        site_id=site_id,
        limit=limit,
        offset=offset,
        return_count=return_count,
    )
    items = [
        {
            **paper_dict(paper),
            "sites": [
                {
                    "id": site.site_id,
                    "record_id": site.record_id,
                    "name": site.name,
                    "dedup_site_id": site.dedup_site_id,
                }
                for site in sites
            ],
        }
        for paper, sites in results
    ]
    if return_count:
        return {"items": items, "total": total}
    return items


@router.post("/papers")
def register_paper(
    doc: Annotated[dict, Body()],
    paper_service: PaperServiceDep,
    user: CurrentUserDep,
    response: Response,
):
    """Register a GeoChem paper from its canonical JSON-LD. Call it on every
    upload: a new paper is added to MinMod; one MinMod already has is left
    exactly as it is. Returns the site and sample ids to use from then on."""
    if GEOCHEM_JSONLD_DIR is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This server is not configured to register GeoChem papers.",
        )
    try:
        reg = paper_service.register(
            doc, user.get_uri(), GEOCHEM_JSONLD_DIR, GEOCHEM_ENTITY_DIR
        )
    except PaperConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    response.status_code = (
        status.HTTP_201_CREATED if reg.created else status.HTTP_200_OK
    )
    return {
        "created": reg.created,
        "message": (
            "The paper was added to MinMod."
            if reg.created
            else "This paper already exists in MinMod; nothing was changed. "
            "Use the ids below."
        ),
        "paper": paper_dict(reg.paper),
        "sites": reg.sites,
        "samples": reg.samples,
    }


@router.get("/papers/{paper_id}")
def get_paper(
    paper_id: str,
    paper_service: PaperServiceDep,
    element: ElementQuery = None,
    include_bdl: IncludeBdlQuery = False,
):
    element = norm_element_filter(element, include_bdl)
    paper = get_paper_or_404(paper_service, paper_id)
    return {
        **paper_dict(paper),
        "sites": [
            {
                **OutputPublicMineralSite.from_kgrel(site).to_dict(),
                "sample_count": count,
            }
            for site, count in paper_service.get_sites(paper, element, include_bdl)
        ],
    }


@router.get("/papers/{paper_id}/samples")
def get_paper_samples(
    paper_id: str,
    paper_service: PaperServiceDep,
    site_id: Optional[InternalID] = None,
    element: ElementQuery = None,
    include_bdl: IncludeBdlQuery = False,
    limit: Annotated[int, Query(ge=0)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    element = norm_element_filter(element, include_bdl)
    paper = get_paper_or_404(paper_service, paper_id)
    samples, total = paper_service.find_samples(
        paper,
        site_id=site_id,
        element=element,
        include_bdl=include_bdl,
        limit=limit,
        offset=offset,
    )
    return {
        "total": total,
        "items": [OutputPublicSample.from_kgrel(s).to_dict() for s in samples],
    }


@router.get("/papers/{paper_id}/jsonld")
def get_paper_jsonld(
    paper_id: str,
    paper_service: PaperServiceDep,
    if_none_match: Annotated[Optional[str], Header()] = None,
):
    """The paper's canonical JSON-LD, with every edit published so far."""
    if GEOCHEM_JSONLD_DIR is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This server is not configured to serve GeoChem papers.",
        )
    paper = get_paper_or_404(paper_service, paper_id)
    try:
        body = paper_service.get_jsonld(paper, GEOCHEM_JSONLD_DIR).encode()
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"The JSON-LD file of paper {paper_id!r} is missing.",
        )

    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    if if_none_match is not None and etag in if_none_match:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
        )
    return Response(
        content=body, media_type="application/ld+json", headers={"ETag": etag}
    )
