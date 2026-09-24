from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    Optional,
    Sequence,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)
from uuid import uuid4

from drepr.writers.turtle_writer import MyLiteral
from minmodkg.libraries.rdf.namespace import Namespace, NoRelSingleNS, SingleNS, Term
from minmodkg.typing import IRI
from rdflib import RDF, XSD, Graph
from rdflib import Literal as RDFLiteral
from rdflib import URIRef
from rdflib.term import Node


@dataclass
class Subject:
    type: Term
    key_ns: SingleNS
    key: Optional[str] = None


@dataclass
class ObjProp:
    """Object Property"""

    pred: Term
    is_list: bool
    target: type[RDFModel]


@dataclass
class RefObjProp:
    """Object Property that should not be expanded and its value is a reference to another object"""

    pred: Term
    is_list: bool


@dataclass
class DataProp:
    """Data Property"""

    pred: Term
    datatype: URIRef
    is_list: bool


@dataclass(frozen=True)
class P:
    """Constructor for Obj/Data Property"""

    pred: Optional[Term] = None
    datatype: Optional[URIRef] = None
    is_list: Optional[bool] = None
    is_ref_object: Optional[bool] = None


@dataclass
class ResourceSchema:
    subj: Subject
    dataprops: dict[str, DataProp] = field(init=False, default_factory=dict)
    objectprops: dict[str, ObjProp] = field(init=False, default_factory=dict)
    ref_objectprops: dict[str, RefObjProp] = field(init=False, default_factory=dict)

    def get_uri(self, resource: Any) -> str:
        if self.subj.key is not None:
            return getattr(resource, self.subj.key)
        if not hasattr(resource, "__uri__"):
            setattr(
                resource,
                "__uri__",
                self.subj.key_ns.uristr(
                    self.subj.type.name + "_" + str(uuid4()).replace("-", "_")
                ),
            )
        return getattr(resource, "__uri__")

    def get_uriref(self, resource: Any) -> URIRef:
        if self.subj.key is not None:
            return URIRef(getattr(resource, self.subj.key))
        if not hasattr(resource, "__uri__"):
            setattr(
                resource,
                "__uri__",
                self.subj.key_ns.uri(
                    self.subj.type.name + "_" + str(uuid4()).replace("-", "_")
                ),
            )
        return getattr(resource, "__uri__")

    def get_uri_n3(self, resource: Any) -> str:
        if self.subj.key is not None:
            uri = getattr(resource, self.subj.key)
            if isinstance(self.subj.key_ns, NoRelSingleNS):
                return f"<{uri}>"
            return self.subj.key_ns.abs2rel(uri)

        if not hasattr(resource, "__uri__"):
            setattr(
                resource,
                "__uri__",
                self.subj.key_ns.uristr(
                    self.subj.type.name + "_" + str(uuid4()).replace("-", "_")
                ),
            )
        return self.subj.key_ns.abs2rel(getattr(resource, "__uri__"))

    def add_property(self, attrname: str, prop: DataProp | ObjProp | RefObjProp):
        """
        Args:
            attrname: name of the property in the Python class.
        """
        if isinstance(prop, ObjProp):
            self.objectprops[attrname] = prop
        elif isinstance(prop, RefObjProp):
            self.ref_objectprops[attrname] = prop
        else:
            self.dataprops[attrname] = prop


class RDFModel:

    registry: ClassVar[dict[type, ResourceSchema]]
    namespace: ClassVar[Namespace]

    if TYPE_CHECKING:
        __subj__: ClassVar[Subject]
        __schema__: ClassVar[ResourceSchema]
        # for getting back the object from the RDF graph
        __rdf_graph_deser__: ClassVar[int]
        # unique identifier of an instance of the model
        # if the model does not have an ID
        __uri__: ClassVar[IRI]

    def __init_subclass__(cls, *kw: Any) -> None:
        if not hasattr(cls, "__subj__"):
            raise KeyError("Subclass of RDFModel must defined __subj__")

        if not hasattr(RDFModel, "registry"):
            RDFModel.registry = {}

        schema = ResourceSchema(subj=cls.__subj__)

        field_types = get_type_hints(cls, include_extras=True)
        for field_name, field_type in field_types.items():
            args = get_args(field_type)
            origin = get_origin(field_type)

            if origin is not Annotated:
                continue

            for arg in args:
                if isinstance(arg, P):
                    cfg = RDFModel._parse_type_hint(field_type)
                    pred = arg.pred or Term(schema.subj.type.ns, field_name)
                    if arg.is_ref_object is True:
                        prop = RefObjProp(pred=pred, is_list=cfg["is_list"])
                    elif cfg["is_object"]:
                        prop = ObjProp(
                            pred=pred, is_list=cfg["is_list"], target=cfg["target"]
                        )
                    else:
                        prop = DataProp(
                            pred=pred,
                            datatype=arg.datatype or cfg["datatype"],
                            is_list=cfg["is_list"],
                        )
                    schema.add_property(field_name, prop)
                    break
                elif isinstance(arg, (DataProp, ObjProp)):
                    schema.add_property(field_name, arg)
                    break

        RDFModel.registry[cls] = schema
        cls.__schema__ = schema
        super().__init_subclass__(*kw)

    @classmethod
    def from_graph(cls, uid: Node, g: Graph):
        schema = cls.__schema__
        attrs = {}
        for name, prop in schema.dataprops.items():
            lst = g.objects(uid, prop.pred.uri)
            if prop.is_list:
                attrs[name] = [norm_literal(x) for x in lst]
            else:
                attrs[name] = norm_literal(next(lst, None))
        for name, prop in schema.ref_objectprops.items():
            lst = g.objects(uid, prop.pred.uri)
            if prop.is_list:
                attrs[name] = [norm_uriref(x) for x in lst]
            else:
                attrs[name] = norm_uriref(next(lst, None))
        for name, prop in schema.objectprops.items():
            lst = g.objects(uid, prop.pred.uri)
            if prop.is_list:
                attrs[name] = [prop.target.from_graph(x, g) for x in lst]
            else:
                attrs[name] = norm_object(prop.target, next(lst, None), g)
        return cls(**attrs)

    def to_triples(self):
        schema = self.__schema__
        subj = schema.get_uri_n3(self)
        triples = [(subj, Namespace.rdf.type, schema.subj.type.reluri)]

        for name, prop in schema.dataprops.items():
            value = getattr(self, name)
            if value is None:
                continue
            if prop.is_list:
                for x in value:
                    triples.append(
                        (
                            subj,
                            prop.pred.reluri,
                            MyLiteral(x, datatype=prop.datatype).n3(
                                RDFModel.namespace.rdflib_namespace_manager
                            ),
                        )
                    )
            else:
                triples.append(
                    (
                        subj,
                        prop.pred.reluri,
                        MyLiteral(value, datatype=prop.datatype).n3(
                            RDFModel.namespace.rdflib_namespace_manager
                        ),
                    )
                )
        for name, prop in schema.ref_objectprops.items():
            value = getattr(self, name)
            if value is None:
                continue
            if prop.is_list:
                for x in value:
                    triples.append((subj, prop.pred.reluri, f"<{x}>"))
            else:
                triples.append((subj, prop.pred.reluri, f"<{value}>"))
        for name, prop in schema.objectprops.items():
            value = getattr(self, name)
            if value is None:
                continue
            if prop.is_list:
                for x in value:
                    triples.append((subj, prop.pred.reluri, x.__schema__.get_uri_n3(x)))
                    triples.extend(x.to_triples())
            else:
                triples.append(
                    (subj, prop.pred.reluri, value.__schema__.get_uri_n3(value))
                )
                triples.extend(value.to_triples())
        return triples

    def to_graph(self, g: Optional[Graph] = None):
        if g is None:
            g = Graph()

        schema = self.__schema__
        subj = schema.get_uriref(self)
        g.add((subj, RDF.type, schema.subj.type.uri))

        for name, prop in schema.dataprops.items():
            value = getattr(self, name)
            if value is None:
                continue
            if prop.is_list:
                for x in value:
                    g.add(
                        (
                            subj,
                            prop.pred.uri,
                            RDFLiteral(x, datatype=prop.datatype),
                        )
                    )
            else:
                g.add(
                    (
                        subj,
                        prop.pred.uri,
                        RDFLiteral(value, datatype=prop.datatype),
                    )
                )
        for name, prop in schema.ref_objectprops.items():
            value = getattr(self, name)
            if value is None:
                continue
            if prop.is_list:
                for x in value:
                    g.add((subj, prop.pred.uri, URIRef(x)))
            else:
                g.add((subj, prop.pred.uri, URIRef(value)))
        for name, prop in schema.objectprops.items():
            value = getattr(self, name)
            if value is None:
                continue
            if prop.is_list:
                for x in value:
                    g.add((subj, prop.pred.uri, x.__schema__.get_uriref(x)))
                    x.to_graph(g)
            else:
                g.add((subj, prop.pred.uri, value.__schema__.get_uriref(value)))
                value.to_graph(g)
        return g

    @staticmethod
    def _parse_type_hint(_type: type | Annotated) -> dict:
        typeorigin = get_origin(_type)
        typeargs = get_args(_type)

        if typeorigin is Annotated:
            return RDFModel._parse_type_hint(typeargs[0])

        if typeorigin is None:
            assert len(typeargs) == 0
            typeorigin = _type

        if typeorigin is Union:
            # detect if it's union
            if len(typeargs) == 2 and typeargs[1] is type(None):
                return RDFModel._parse_type_hint(typeargs[0])
            raise NotImplementedError(_type)

        if typeorigin is Literal:
            if all(isinstance(arg, str) for arg in typeargs):
                return {"is_object": False, "is_list": False, "datatype": XSD.string}
            raise NotImplementedError(_type)

        if issubclass(typeorigin, (str, int, float, bool)):
            return {
                "is_object": False,
                "is_list": False,
                "datatype": {
                    str: XSD.string,
                    int: XSD.integer,
                    float: XSD.decimal,
                    bool: XSD.boolean,
                }[typeorigin],
            }

        if issubclass(typeorigin, Sequence):
            assert len(typeargs) == 1, _type
            result = RDFModel._parse_type_hint(typeargs[0])
            # do not supported nested list
            assert result["is_list"] is False
            result["is_list"] = True
            return result

        if issubclass(typeorigin, RDFModel):
            return {
                "is_object": True,
                "is_list": False,
                "datatype": None,
                "target": typeorigin,
            }

        raise NotImplementedError(typeorigin)


def norm_literal(value: Annotated[Any, Literal]) -> Any:
    return (
        None
        if value is None
        else (value.value if value.value is not None else str(value.value))
    )


def norm_uriref(value: Annotated[Any, URIRef]) -> Optional[IRI]:
    return None if value is None else str(value)


M = TypeVar("M", bound=RDFModel)


def norm_object(clz: type[M], id: Optional[Node], g: Graph) -> Optional[M]:
    return None if id is None else clz.from_graph(id, g)
