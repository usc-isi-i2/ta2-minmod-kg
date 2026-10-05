from __future__ import annotations

from pathlib import Path
from typing import Mapping, NotRequired, TypedDict

import serde.csv
import serde.json
from libactor.cache import cache
from minmodkg.models.kg.base import MINMOD_KG
from minmodkg.models.kgrel.data_source import DataSource
from minmodkg.models.kgrel.entities.category import Category
from minmodkg.models.kgrel.entities.commodity import Commodity
from minmodkg.models.kgrel.entities.commodity_form import CommodityForm
from minmodkg.models.kgrel.entities.country import Country
from minmodkg.models.kgrel.entities.crs import CRS
from minmodkg.models.kgrel.entities.deposit_type import DepositType
from minmodkg.models.kgrel.entities.state_or_province import StateOrProvince
from minmodkg.models.kgrel.entities.unit import Unit

from statickg.helper import FileSqliteBackend
from statickg.models.etl import ETLOutput
from statickg.models.file_and_path import InputFile, RelPath
from statickg.models.repository import Repository
from statickg.services.interface import BaseFileService, BaseService


class KGRelEntityETLServiceConstructArgs(TypedDict):
    parallel: NotRequired[bool]


class KGRelEntityETLServiceInvokeArgs(TypedDict):
    input: RelPath
    output: RelPath


class KGRelEntityETLService(BaseFileService[KGRelEntityETLServiceConstructArgs]):
    def __init__(
        self,
        name: str,
        workdir: Path,
        args: KGRelEntityETLServiceConstructArgs,
        services: Mapping[str, BaseService],
    ):
        super().__init__(name, workdir, args, services)
        self.parallel = args.get("parallel", True)

    def forward(
        self, repo: Repository, args: KGRelEntityETLServiceInvokeArgs, output: ETLOutput
    ):
        input = args["input"]
        outdir = args["output"]
        outdir.get_path().mkdir(parents=True, exist_ok=True)

        outfiles = set()

        for filename in [
            "commodity.csv",
            "country.csv",
            "deposit_type.csv",
            "data_source.csv",
            "state_or_province.csv",
            "unit.csv",
            "category.csv",
            "commodity_form.csv",
            "crs.csv",
        ]:
            outfiles.update(
                EntityDeserFn.exec(
                    self.workdir,
                    infile=InputFile.from_relpath(input / filename),
                    outdir=outdir.get_path(),
                )
            )

        self.remove_unknown_files(outfiles, outdir.get_path())


class EntityDeserFn:
    """Deserialize Entity Data to RDF and Relational data"""

    VERSION = "v107"

    instances = {}

    def __init__(self, workdir: Path):
        self.workdir = workdir

    @staticmethod
    def get_instance(workdir: Path) -> EntityDeserFn:
        if workdir not in EntityDeserFn.instances:
            EntityDeserFn.instances[workdir] = EntityDeserFn(workdir)
        return EntityDeserFn.instances[workdir]

    @classmethod
    def exec(cls, workdir: Path, **kwargs) -> list[Path]:
        return cls.get_instance(workdir).invoke(**kwargs)

    @cache(
        backend=FileSqliteBackend.factory(
            filename=f"transform-{VERSION}.sqlite", multi_files=True
        ),
        cache_ser_args={
            "infile": lambda x: x.get_ident(),
        },
    )
    def invoke(self, infile: InputFile, outdir: Path) -> list[Path]:
        records = self.read_file(infile.path)
        clsname = records[0].__class__.__name__

        serde.json.ser(
            {clsname: [r.to_dict() for r in records]},
            outdir / f"{infile.path.stem}.json",
            indent=2,
        )
        (outdir / f"{infile.path.stem}.ttl").write_text(
            MINMOD_KG.prefix_part
            + "\n"
            + "\n".join(
                (
                    f"{s} {p} {o} ."
                    for r in records
                    for s, p, o in r.to_kg().to_triples()
                )
            )
        )
        return [outdir / f"{infile.path.stem}.json", outdir / f"{infile.path.stem}.ttl"]

    @staticmethod
    def read_file(infile: Path) -> list:
        if infile.name == "commodity.csv":
            return EntityDeserFn.read_commodity(infile)
        elif infile.name == "commodity_form.csv":
            return EntityDeserFn.read_commodity_form(infile)
        elif infile.name == "crs.csv":
            return EntityDeserFn.read_crs(infile)
        elif infile.name == "country.csv":
            return EntityDeserFn.read_country(infile)
        elif infile.name == "deposit_type.csv":
            return EntityDeserFn.read_deposit_type(infile)
        elif infile.name == "data_source.csv":
            return EntityDeserFn.read_source(infile)
        elif infile.name == "state_or_province.csv":
            return EntityDeserFn.read_state_or_province(infile)
        elif infile.name == "unit.csv":
            return EntityDeserFn.read_unit(infile)
        elif infile.name == "category.csv":
            return EntityDeserFn.read_category(infile)
        else:
            raise NotImplementedError(infile)

    @staticmethod
    def read_commodity(infile: Path) -> list[Commodity]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records: list[Commodity] = []
        for raw_record in raw_records:
            try:
                r = Commodity(
                    id=raw_record["minmod_id"],
                    name=raw_record["name"],
                    aliases=(
                        [s.strip() for s in raw_record["aliases"].split("|")]
                        if raw_record["aliases"].strip() != ""
                        else []
                    ),
                    parent=(
                        raw_record["parent"]
                        if raw_record["parent"].strip() != ""
                        else None
                    ),
                    is_critical=bool(int(raw_record["is_critical_commodity"])),
                )
            except Exception:
                print("Error while processing", raw_record)
                raise
            records.append(r)

        # insert parentless records first
        records.sort(key=lambda x: (int(x.parent is not None), x.id))
        return records

    @staticmethod
    def read_commodity_form(infile: Path) -> list[CommodityForm]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records: list[CommodityForm] = []
        for raw_record in raw_records:
            r = CommodityForm(
                id=raw_record["minmod_id"],
                name=raw_record["name"],
                formula=raw_record["formula"],
                commodity=raw_record["commodity_id"],
                conversion=float(raw_record["conversion"]),
            )
            records.append(r)
        return records

    @staticmethod
    def read_crs(infile: Path) -> list[CRS]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            records.append(CRS(raw_record["minmod_id"], raw_record["name"]))
        return records

    @staticmethod
    def read_country(infile: Path) -> list[Country]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            aliases = [s.strip() for s in raw_record["alt names"].split("|")]
            for key in ["iso3", "iso2"]:
                if raw_record[key].strip() != "":
                    aliases.append(raw_record[key].strip())
            records.append(
                Country(
                    id=raw_record["minmod_id"], name=raw_record["name"], aliases=aliases
                )
            )
        return records

    @staticmethod
    def read_deposit_type(infile: Path) -> list[DepositType]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            records.append(
                DepositType(
                    id=raw_record["minmod_id"],
                    name=raw_record["deposit_type"],
                    environment=raw_record["deposit_environment"],
                    group=raw_record["deposit_group"],
                )
            )
        return records

    @staticmethod
    def read_source(infile: Path) -> list[DataSource]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            score = None
            if raw_record["score"].strip() != "":
                score = float(raw_record["score"])
            records.append(
                DataSource(
                    id=raw_record["uri"],
                    name=raw_record["name"],
                    type=raw_record["type"],
                    created_by=raw_record["created_by"],
                    description=raw_record["description"],
                    score=score,
                    connection=(
                        raw_record["connection"].strip()
                        if raw_record["connection"].strip() != ""
                        else None
                    ),
                )
            )
        return records

    @staticmethod
    def read_state_or_province(infile: Path) -> list[StateOrProvince]:
        country_infile = infile.parent / "country.csv"
        assert country_infile.exists()
        countries = EntityDeserFn.read_country(country_infile)
        name2country = {c.name: c for c in countries}
        assert len(name2country) == len(countries)

        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            records.append(
                StateOrProvince(
                    id=raw_record["minmod_id"],
                    name=raw_record["name"],
                    country=name2country[raw_record["country_name"]].id,
                    state_code=raw_record.get("state_code") or None,
                )
            )
        return records

    @staticmethod
    def read_unit(infile: Path) -> list[Unit]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            records.append(
                Unit(
                    id=raw_record["minmod_id"],
                    name=raw_record["unit name"],
                    aliases=[s.strip() for s in raw_record["unit aliases"].split("|")],
                )
            )
        return records

    @staticmethod
    def read_category(infile: Path) -> list[Category]:
        raw_records = serde.csv.deser(infile, deser_as_record=True)
        records = []
        for raw_record in raw_records:
            records.append(
                Category(
                    id=raw_record["id"],
                    name=raw_record["label"],
                )
            )
        return records
