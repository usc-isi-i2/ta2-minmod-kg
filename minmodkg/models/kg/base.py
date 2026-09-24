from __future__ import annotations

from rdflib import RDF
from statickg.helper import import_attr

from minmodkg.config import MINMOD_KG_CLSARGS, MINMOD_KG_CLSPATH, MINMOD_NS_CFG
from minmodkg.libraries.rdf.namespace import Namespace, NoRelSingleNS, SingleNS
from minmodkg.libraries.rdf.rdf_model import RDFModel
from minmodkg.libraries.rdf.triple_store import TripleStore

NS_RDF = Namespace.rdf
NS_RDFS = Namespace.rdfs

NS_MR_NO_REL = NoRelSingleNS("mr", MINMOD_NS_CFG["mr"])
MINMOD_NS = Namespace(MINMOD_NS_CFG)
NS_MR = MINMOD_NS.mr
NS_MO = MINMOD_NS.mo
NS_MD = MINMOD_NS.md
NS_GCO = MINMOD_NS.gco
NS_GCR = MINMOD_NS.gcr
MINMOD_KG: TripleStore = import_attr(MINMOD_KG_CLSPATH)(MINMOD_NS, **MINMOD_KG_CLSARGS)

RDFModel.namespace = MINMOD_NS
