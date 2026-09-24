from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Annotated, Optional

from minmodkg.libraries.rdf.rdf_model import P, RDFModel, Subject
from minmodkg.models.kg.base import NS_GCO, NS_GCR
from minmodkg.transformations import make_paper_id
from minmodkg.typing import IRI, CleanedNotEmptyStr, InternalID
from rdflib import XSD, URIRef


@dataclass
class Paper(RDFModel):
    __subj__ = Subject(
        type=NS_GCO.term("MineralResourcePaper"), key_ns=NS_GCR, key="uri"
    )

    # corpus id, only used to mint the uri (:paper_id's domain is mo:Reference)
    paper_id: str
    paper_title: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    paper_doi: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    paper_authors: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    paper_journal: Annotated[Optional[CleanedNotEmptyStr], P()] = None
    paper_year: Annotated[Optional[int], P()] = None
    paper_url: Annotated[Optional[IRI], P(datatype=XSD.anyURI)] = None
    has_mineral_site: Annotated[list[IRI], P(is_ref_object=True)] = field(
        default_factory=list
    )

    @cached_property
    def id(self) -> InternalID:
        return make_paper_id(self.paper_id)

    @cached_property
    def uri(self) -> URIRef:
        return NS_GCR.uri(self.id)
