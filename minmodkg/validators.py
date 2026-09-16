from __future__ import annotations

import hashlib
import importlib
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import cached_property, lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Callable,
    NotRequired,
    Optional,
    Sequence,
    TypedDict,
)

import orjson
import typer
from drepr.main import convert
from joblib import Parallel, delayed
from loguru import logger
from minmodkg.api.models.public_mineral_site import IRI, InputPublicMineralSite
from minmodkg.config import MINMOD_DEBUG
from minmodkg.misc.deserializer import get_dataclass_deserializer
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.geology_info import RockType
from minmodkg.models.kg.measure import Measure
from minmodkg.services.kgrel_entity import EntityService
from rdflib import RDF, SH, XSD, Graph, Literal, URIRef
from tqdm.auto import tqdm

from statickg.helper import CacheProcess, import_func
from statickg.models.file_and_path import BaseType, RelPath
from statickg.models.prelude import ETLOutput, RelPath, Repository
from statickg.services.interface import BaseFileService

if TYPE_CHECKING:
    from minmodkg.models.kg.sample import Sample as KGSample


class FilenameValidatorServiceConstructArgs(TypedDict):
    pattern: str
    verbose: NotRequired[int]


class FilenameValidatorServiceInvokeArgs(TypedDict):
    input: RelPath | list[RelPath]


TempMineralSiteValidator = get_dataclass_deserializer(InputPublicMineralSite)


def mineral_site_deser(site: InputPublicMineralSite | dict) -> InputPublicMineralSite:
    if not isinstance(site, InputPublicMineralSite):
        norm_site: InputPublicMineralSite = TempMineralSiteValidator(site)
    else:
        norm_site = site
    if len(norm_site.reference) != 1:
        raise ValueError(f"Expect 1 reference but got {len(norm_site.reference)}")
    return norm_site


class FilenameValidatorService(BaseFileService[FilenameValidatorServiceConstructArgs]):

    def forward(
        self,
        repo: Repository,
        args: FilenameValidatorServiceInvokeArgs,
        output: ETLOutput,
    ):
        verbose = self.args.get("verbose", 1)
        infiles = self.list_files(
            repo,
            args["input"],
            unique_filepath=False,
            optional=False,
            compute_missing_file_key=False,
        )

        regex = re.compile(self.args["pattern"])

        invalid_filenames = []
        for infile in tqdm(infiles, desc="Validate file names", disable=verbose != 1):
            if not regex.match(infile.path.stem):
                invalid_filenames.append(infile.relpath)

        if len(invalid_filenames) > 0:
            self.logger.error(
                "Invalid filenames: \n{}",
                "\n".join(f"\t{x}" for x in invalid_filenames),
            )
            raise Exception("Found invalid filenames")


class ContentValidator:
    instance = None

    @dataclass
    class DReprModel:
        key: str
        validate_model: Callable[[Path], None]
        transform_model: Callable[[Path], str]

    def __init__(self, workdir: Path, shacl_bin: Path, predefined_ent_dir: RelPath):
        self.workdir = workdir
        self.predefined_ent_dir = predefined_ent_dir
        self.shacl_bin = shacl_bin
        self.shape_file = Path(__file__).parent.parent / "schema/shapes.ttl"

        assert shacl_bin.exists(), f"SHACL binary not found at {shacl_bin}"

        # create additional directories
        (workdir / "mineral-sites").mkdir(parents=True, exist_ok=True)
        (workdir / "entities").mkdir(parents=True, exist_ok=True)

        self.extractor_dir = RelPath(
            basetype=BaseType.CFG_DIR,
            basepath=Path(__file__).parent.parent,
            relpath="extractors",
        )
        self.pkgdir = self.setup(workdir)
        self.drepr_version = version("drepr-v2").strip()
        self.cache = CacheProcess(workdir / "cache.db")

    @staticmethod
    def get_instance(workdir: Path, shacl_bin: Path, predefined_ent_dir: RelPath):
        if ContentValidator.instance is None:
            ContentValidator.instance = ContentValidator(
                workdir, shacl_bin, predefined_ent_dir
            )
        return ContentValidator.instance

    def setup(self, workdir: Path):
        pkgname = "gen_validate_programs"
        pkgdir = workdir / pkgname

        try:
            m = importlib.import_module(pkgname)
            if Path(m.__path__[0]) != pkgdir:
                raise ValueError(
                    f"Existing a python package named {pkgname}, please uninstall it because it is reserved to store generated DREPR programs"
                )
        except ModuleNotFoundError:
            # we can use services as the name of the folder containing the services as it doesn't conflict with any existing
            # python packages
            pass

        pkgdir.mkdir(parents=True, exist_ok=True)
        (pkgdir / "__init__.py").touch(exist_ok=True)

        # add the package to the path
        if str(pkgdir.parent) not in sys.path:
            sys.path.insert(0, str(pkgdir.parent))

        return pkgdir

    def get_cache_key(self, filepath: RelPath):
        return f"validator:{self.drepr_version}:{hashlib.sha256(filepath.get_path().read_bytes()).hexdigest()}"

    @cached_property
    def site_model(self):
        return self._get_drepr_model("mineral_site")

    @cached_property
    def predefined_entity_models(self):
        return {
            key: self._get_drepr_model(key)
            for key in [
                "category",
                "commodity",
                "deposit_type",
                "unit",
                "country",
                "state_or_province",
                "crs",
                "material_form",
                "source",
            ]
        }

    @cached_property
    def _predefined_entity_graph(self):
        data = []
        for file in self.predefined_ent_dir.iterdir():
            if file.suffix != ".json" and file.suffix != ".csv":
                continue

            outfile = self.workdir / "entities" / (file.stem + ".ttl")
            self._exec_drepr_model(
                self.predefined_entity_models[file.stem], file, outfile
            )
            data.append(outfile.read_text())
        return "\n".join(data)

    def _get_drepr_model(self, name: str):
        model_file = self.extractor_dir / f"{name}.yml"
        model_filepath = model_file.get_path()

        model_outfile = self.pkgdir / f"{model_filepath.stem}.py"
        model_key = self.get_cache_key(model_file)

        with self.cache.auto(
            filepath=model_file.get_ident(),
            key=model_key,
        ) as notfound:
            if notfound:
                try:
                    convert(
                        repr=model_filepath,
                        resources={},
                        progfile=model_outfile,
                    )
                except:
                    logger.error(
                        "Error when generating program {}", model_file.get_ident()
                    )
                    raise
                logger.info("generate program {}", model_file.get_ident())

        return ContentValidator.DReprModel(
            key=model_key,
            validate_model=lambda x: None,
            transform_model=import_func(
                f"{model_outfile.parent.name}.{model_outfile.stem}.main"
            ),
        )

    def _exec_drepr_model(
        self, model: ContentValidator.DReprModel, infile: RelPath, outfile: Path
    ):
        with self.cache.auto(
            filepath=infile.get_ident(),
            key=self.get_cache_key(infile) + ":model:" + model.key,
            outfile=outfile,
        ) as notfound:
            if notfound:
                try:
                    output = model.transform_model(infile.get_path())
                except:
                    logger.error("Encounter error when converting data to TTL")
                    raise
                outfile.write_text(output)

    def validate_mineral_site(self, site_file: RelPath):
        print("Validating file {}", site_file)
        outfile = self.workdir / "mineral-sites" / (site_file.stem + ".ttl")

        # ------- schema check - pass 1 -------
        print("Check if the data is complied with JSON schema (part 1)...")
        for site in orjson.loads(site_file.get_path().read_bytes()):
            # MineralSiteValidator.validate(site)
            TempMineralSiteValidator(site)

        # ------- schema check - pass 2 -------
        print("Check if the data is valid with D-REPR (part 2)...")

        # ------- convert the site file to ttl -------
        print("Check if the data can be converted to TTL...")
        self._exec_drepr_model(self.site_model, site_file, outfile)
        print("Check if the data can be converted to TTL... Success!")

        # ------- validate again using SHACL to ensure all references are correct -------
        print("Check if the data is valid with SHACL (part 3)...")
        # prepare a graph
        full_graph = self.workdir / "mineral-sites" / (site_file.stem + ".full.ttl")
        with open(full_graph, "w") as f:
            f.write(self._predefined_entity_graph)
            f.write("\n")
            f.write(outfile.read_text())

        process = subprocess.run(
            [
                "bash",
                str(self.shacl_bin),
                "-datafile",
                str(full_graph),
                "-shapesfile",
                str(self.shape_file),
            ],
            stdout=subprocess.PIPE,
        )
        shacl_output = process.stdout.decode("utf-8")

        # removing warning
        shacl_output_lines = shacl_output.splitlines()
        for i, line in enumerate(shacl_output_lines):
            if not line.startswith("@prefix"):
                assert re.match(r"\d*", line) is not None
                assert "WARN" in line
            else:
                shacl_output = "\n".join(shacl_output_lines[i:])
                break

        if process.returncode != 0:
            print("=" * 80, "\n", "-" * 10, "SHACL output", "-" * 10, "\n")
            print(shacl_output)
            print("=" * 80)
            raise Exception("SHACL validation failed")
        else:
            g = Graph()
            try:
                g.parse(data=shacl_output, format="turtle")
            except:
                print("=" * 80, "\n", "-" * 10, "SHACL output", "-" * 10, "\n")
                print(shacl_output)
                print("=" * 80)
                raise Exception("SHACL validation failed")

            for subj in g.subjects(RDF.type, SH.ValidationReport):
                (conforms,) = list(g.objects(subj, SH.conforms))
                if not conforms:
                    print("=" * 80, "\n", "-" * 10, "SHACL output", "-" * 10, "\n")
                    print(shacl_output)
                    print("=" * 80)
                    raise Exception("SHACL validation failed")

        print("Check if the data is valid with SHACL (part 2)... Success!")


def validate_mineral_site(
    data: str | Path | Sequence[dict] | Sequence[InputPublicMineralSite],
    ent_service: EntityService,
    verbose: bool = False,
):
    if isinstance(data, (str, Path)):
        sites = orjson.loads(Path(data).read_bytes())
    else:
        sites = data

    if len(sites) == 0:
        return

    # validate data format
    norm_sites: list[InputPublicMineralSite] = []
    if isinstance(sites[0], dict):
        for i, site in tqdm(
            enumerate(sites),
            total=len(sites),
            desc="Validate data format",
            disable=not verbose,
        ):
            if isinstance(site, dict) and "modified_at" not in site:
                raise ValueError(
                    f"Invalid site data at record {i}: missing modified_at field"
                )
            try:
                norm_site: InputPublicMineralSite = mineral_site_deser(site)
            except Exception as e:
                raise ValueError(f"Invalid site data at record {i}") from e
            norm_sites.append(norm_site)
    else:
        for i, site in enumerate(sites):
            try:
                assert isinstance(site, InputPublicMineralSite)
                mineral_site_deser(site.to_dict())
            except Exception as e:
                raise ValueError(f"Invalid site data at record {i}") from e
            norm_sites.append(site)

    countries = ent_service.get_country_uris()
    sops = ent_service.get_state_or_province_uris()
    crss = ent_service.get_crs_name()
    deptypes = ent_service.get_deposit_type_uris()
    commodities = ent_service.get_commodity_uris()
    commodity_forms = ent_service.get_commodity_form_uris()
    cats = ent_service.get_category_uris()
    units = ent_service.get_unit_uris()

    # validate data content
    for i, site in tqdm(
        enumerate(norm_sites),
        total=len(norm_sites),
        desc="Validate data content",
        disable=not verbose,
    ):
        if site.location_info is not None:
            for country in site.location_info.country:
                ValidatorHelper.optional_uri(
                    country.normalized_uri, "location_info.country", countries
                )
            for state in site.location_info.state_or_province:
                ValidatorHelper.optional_uri(
                    state.normalized_uri, "location_info.state_or_province", sops
                )
            if site.location_info.crs is not None:
                ValidatorHelper.optional_uri(
                    site.location_info.crs.normalized_uri, "location_info.crs", crss
                )
        for dt in site.deposit_type_candidate:
            ValidatorHelper.optional_uri(
                dt.normalized_uri, "deposit_type_candidate", deptypes
            )
        for inv in site.mineral_inventory:
            if inv.commodity is not None:
                ValidatorHelper.optional_uri(
                    inv.commodity.normalized_uri,
                    "mineral_inventory.commodity",
                    commodities,
                )
            for cat in inv.category:
                ValidatorHelper.optional_uri(
                    cat.normalized_uri, "mineral_inventory.category", cats
                )

            ValidatorHelper.optional_measure(
                inv.grade,
                "mineral_inventory.grade",
                allow_uris=units,
            )
            ValidatorHelper.optional_measure(
                inv.cutoff_grade,
                "mineral_inventory.cutoff_grade",
                allow_uris=units,
            )
            ValidatorHelper.optional_measure(
                inv.ore,
                "mineral_inventory.ore",
                allow_uris=units,
            )
            if inv.material_form is not None:
                ValidatorHelper.optional_uri(
                    inv.material_form.normalized_uri,
                    "mineral_inventory.material_form",
                    commodity_forms,
                )


def validate_sample(
    data: Sequence[dict] | Sequence["InputPublicSample"],
    ent_service: EntityService,
    verbose: bool = False,
):
    from minmodkg.api.models.public_sample import InputPublicSample

    if len(data) == 0:
        return

    norm_samples: list[InputPublicSample] = []
    if isinstance(data[0], dict):
        for i, sample in enumerate(data):
            assert isinstance(sample, dict)
            try:
                norm_samples.append(InputPublicSample.from_dict(sample))
            except Exception as e:
                raise ValueError(f"Invalid sample data at record {i}") from e
    else:
        for sample in data:
            assert isinstance(sample, InputPublicSample)
            norm_samples.append(sample)

    units = ent_service.get_unit_uris()

    for i, sample in tqdm(
        enumerate(norm_samples),
        total=len(norm_samples),
        desc="Validate data content",
        disable=not verbose,
    ):
        for a_i, analysis in enumerate(sample.analyses):
            for e_i, element in enumerate(analysis.elements):
                if element.grade_unit is not None:
                    ValidatorHelper.optional_uri(
                        element.grade_unit.normalized_uri,
                        f"analyses[{a_i}].elements[{e_i}].grade_unit",
                        units,
                    )
                if element.detection_limit_unit is not None:
                    ValidatorHelper.optional_uri(
                        element.detection_limit_unit.normalized_uri,
                        f"analyses[{a_i}].elements[{e_i}].detection_limit_unit",
                        units,
                    )


# Vendored from ta2-table-understanding/schema/ -- that repo is the source of
# truth for the ontology/shapes, this is a snapshot copy so the API doesn't
# depend on a sibling checkout existing at runtime (same reasoning geochem-hmi
# used for its own vendored copy at hmi_backend/data/ontology/). Re-copy both
# files here whenever the ontology changes in a way that affects validation.
_GEOCHEM_SCHEMA_DIR = Path(__file__).parent.parent / "schema"
_GEOCHEM_SHAPES_FILE = _GEOCHEM_SCHEMA_DIR / "geochem_v1.2.0.shacl.ttl"
_GEOCHEM_ONTOLOGY_FILE = _GEOCHEM_SCHEMA_DIR / "geochem_v1.2.0.ttl"

@lru_cache(maxsize=1)
def _shacl_shapes_graph() -> Graph:
    return Graph().parse(_GEOCHEM_SHAPES_FILE, format="turtle")


@lru_cache(maxsize=1)
def _shacl_ontology_graph() -> Graph:
    return Graph().parse(_GEOCHEM_ONTOLOGY_FILE, format="turtle")


@lru_cache(maxsize=1)
def _shacl_advisory_messages() -> frozenset[str]:
    """Messages declared `sh:severity sh:Warning` anywhere in the shapes graph
    (e.g. "An Analysis should reference at least one :element or :isotope",
    "If :grade is specified, :grade_unit should also be specified") -- these
    are recommendations, not requirements, by the shape file's own design.

    pyshacl's SPARQLConstraintComponent results don't actually carry the
    shape's declared severity through, though -- they come back as
    sh:Violation regardless, both in resultSeverity and in the report text.
    geochem-hmi's own SHACL test independently hit this exact tool limitation
    and filters "by message rather than severity" for the same reason (its
    docstring calls out the identical bug against this identical shape file).
    Deriving the set from the shapes graph itself, rather than hardcoding the
    one message that happened to get hit first, covers every Warning-severity
    SPARQL rule in the file -- there are 8 as of this shape file's version, not
    just the AnalyticalMethod one geochem-hmi's comment documents.
    """
    g = _shacl_shapes_graph()
    return frozenset(
        str(msg)
        for shape in g.subjects(SH.severity, SH.Warning)
        for msg in g.objects(shape, SH.message)
    )


_GEO_WKT_LITERAL = URIRef("http://www.opengis.net/ont/geosparql#wktLiteral")
# mo:location -- the one WKT-geometry leaf RDFModel doesn't have a Python type
# for -- gets typed xsd:string by RDFModel.to_graph() (there's no WKT-aware type
# in the Python model), but the ontology declares its range geo:wktLiteral.
# Same gap on MineralSite's own location, not something new to Sample.
_WKT_PREDICATES = (URIRef("https://minmod.isi.edu/ontology/location"),)


def _normalize_graph_for_shacl(g: Graph) -> Graph:
    """Two narrow, validation-only literal-retyping fixes, applied to a copy of
    the graph -- neither touches the real serialization used for Fuseki/backups.
    Both are pre-existing gaps in RDFModel.to_graph(), not introduced by SHACL
    validation, that would otherwise fail every sample regardless of content:

    1. `Literal(some_python_float, datatype=XSD.decimal)` -- what RDFModel
       builds for every decimal-ranged field (grade, confidence, ...) -- keeps
       its cached Python value as a `float`, not a `decimal.Decimal`, because
       rdflib doesn't re-coerce an explicitly-typed literal's value at
       construction time. pyshacl's datatype constraint checks that cached
       Python type, not just the declared datatype URI, so it fails every one
       of them. Round-tripping through `str()` forces rdflib to re-derive a
       real Decimal from the lexical form.
    2. `mo:location`'s xsd:string -> geo:wktLiteral, see _WKT_PREDICATES above.
    """
    normalized = Graph()
    for s, p, o in g:
        if isinstance(o, Literal):
            if o.datatype == XSD.decimal:
                o = Literal(str(o), datatype=XSD.decimal)
            elif p in _WKT_PREDICATES and o.datatype == XSD.string:
                o = Literal(str(o), datatype=_GEO_WKT_LITERAL)
        normalized.add((s, p, o))
    return normalized


def validate_sample_shacl(sample: KGSample) -> list[str]:
    """Validate one Sample (and everything nested under it -- analyses,
    elements, location, references) against the GeoChem SHACL shapes.

    Returns a list of human-readable violation messages, empty if it conforms.
    Never raises on a validation failure -- SampleService.publish() folds this
    into its own per-sample errors[] the same way unit/identity validation
    already work, so one sample failing SHACL doesn't abort the whole batch.

    The ontology graph is loaded alongside the shapes, not just the shapes
    alone -- without it, subclass edges (e.g. a concrete unit resource typed
    under a commodity-form subclass) are invisible to `inference="rdfs"` and
    produce false failures. geochem-hmi's own SHACL test documents hitting
    exactly this gotcha; loading both graphs here avoids repeating it.
    """
    from pyshacl import validate as pyshacl_validate

    data_graph = _normalize_graph_for_shacl(sample.to_graph())
    conforms, _, report_text = pyshacl_validate(
        data_graph,
        shacl_graph=_shacl_shapes_graph(),
        ont_graph=_shacl_ontology_graph(),
        advanced=True,
        inference="rdfs",
        allow_warnings=True,
    )
    if conforms:
        return []

    messages = [
        line.split("Message:", 1)[1].strip().strip("'\"")
        for line in report_text.splitlines()
        if "Message:" in line
    ]
    advisories = _shacl_advisory_messages()
    return [m for m in messages if m not in advisories]


class ValidatorHelper:
    @staticmethod
    def optional_uri(
        s: Optional[IRI],
        prop: str,
        allow_uris: Optional[set[IRI] | dict[IRI, Any]] = None,
    ):
        if s is None:
            return None

        if not isinstance(s, str):
            raise ValueError(f"{prop} must be a string, got {type(s).__name__}")

        if s == "":
            raise ValueError(f"{prop} cannot be empty")

        # Check if URI is in the allowed set if provided
        if allow_uris is not None and s not in allow_uris:
            raise ValueError(f"{prop} has URI '{s}' which is not in the allowed set")

        return s

    @staticmethod
    def optional_measure(
        m: Optional[Measure],
        prop: str,
        allow_uris: Optional[set[IRI] | dict[IRI, Any]] = None,
    ):
        if m is None:
            return None

        if not isinstance(m, Measure):
            raise ValueError(f"{prop} must be a Measure, got {type(m).__name__}")

        if m.unit is not None:
            ValidatorHelper.optional_uri(
                m.unit.normalized_uri,
                prop + ".unit",
                allow_uris=allow_uris,
            )
        if m.value is not None and not isinstance(m.value, (int, float)):
            raise ValueError(
                f"{prop}.value must be a number, got {type(m.value).__name__}"
            )
        return m


if __name__ == "__main__":
    app = typer.Typer(pretty_exceptions_short=True, pretty_exceptions_enable=False)

    @app.command()
    def validate(
        datadir: Annotated[
            Path, typer.Argument(help="A directory containing the data Git repository")
        ],
        workdir: Annotated[
            Path,
            typer.Argument(help="A directory for storing intermediate ETL results"),
        ],
        shacl_bin: Annotated[
            Path,
            typer.Argument(
                help="A path to the SHACL binary",
            ),
        ],
        changed_files_lst: Annotated[
            Path,
            typer.Argument(
                help="A list of changed files (relative path) in the data repository",
            ),
        ],
        parallel: Annotated[
            bool,
            typer.Option(
                "--parallel/--no-parallel",
                help="Whether to run the validation in parallel",
            ),
        ] = False,
    ):
        validator_args = (
            workdir,
            shacl_bin,
            RelPath(
                basetype=BaseType.DATA_DIR, basepath=datadir, relpath="data/entities"
            ),
        )

        if changed_files_lst.name.endswith(".json"):
            changed_files = orjson.loads(changed_files_lst.read_bytes())
        else:
            assert changed_files_lst.name.endswith(".txt")
            changed_files = changed_files_lst.read_text().splitlines()

        def validate_file(relpath: str):
            validator = ContentValidator.get_instance(*validator_args)
            if relpath.startswith("data/entities/"):
                # validate the entities
                raise NotImplementedError()
            elif relpath.startswith("data/mineral-sites/") and relpath.endswith(
                ".json"
            ):
                print("::group::Validate file", relpath)
                validator.validate_mineral_site(
                    RelPath(
                        basetype=BaseType.DATA_DIR, basepath=datadir, relpath=relpath
                    )
                )
                print("::endgroup::")
            else:
                print("::group::Not validate file", relpath)
                print("::endgroup::")

        if not parallel:
            for relpath in changed_files:
                validate_file(relpath)
        else:
            parallel_executor = Parallel(n_jobs=-1, return_as="generator_unordered")
            it = parallel_executor(
                delayed(validate_file)(relpath) for relpath in changed_files
            )
            for _ in tqdm(it, total=len(changed_files), desc="Validate files"):
                pass

    app()
