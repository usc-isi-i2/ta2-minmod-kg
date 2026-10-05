from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, TypeVar
from urllib.parse import urljoin

import httpx
import serde.json
from minmodkg.misc.state_repair import StateCountryIndex
from minmodkg.models.kg.base import NS_MR
from minmodkg.models.kg.entities.commodity_form import CommodityForm as KGCommodityForm
from minmodkg.models.kg.entities.crs import CRS as KGCRS
from minmodkg.models.kgrel.base import Base, engine
from minmodkg.models.kgrel.data_source import DataSource
from minmodkg.models.kgrel.entities.category import Category
from minmodkg.models.kgrel.entities.commodity import Commodity
from minmodkg.models.kgrel.entities.commodity_form import CommodityForm
from minmodkg.models.kgrel.entities.country import Country
from minmodkg.models.kgrel.entities.crs import CRS
from minmodkg.models.kgrel.entities.deposit_type import DepositType
from minmodkg.models.kgrel.entities.state_or_province import StateOrProvince
from minmodkg.models.kgrel.entities.unit import Unit
from minmodkg.typing import IRI, InternalID
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

T = TypeVar("T", bound=Base)


class EntityService:
    instance = None

    def __init__(self, _engine: Optional[Engine] = None):
        self.engine = _engine or engine

        self.units: Optional[list[Unit]] = None
        self.commodities: Optional[list[Commodity]] = None
        self.deposit_types: Optional[list[DepositType]] = None
        self.countries: Optional[list[Country]] = None
        self.categories: Optional[list[Category]] = None
        self.state_or_provinces: Optional[list[StateOrProvince]] = None
        self.commodity_forms: Optional[list[CommodityForm]] = None
        self.data_sources: Optional[dict[IRI, DataSource]] = None
        self.crs: Optional[list[CRS]] = None

        self.commodity_form_conversion: Optional[dict[IRI, float]] = None
        self.data_source_score: Optional[dict[IRI, float | None]] = None
        self.crs_name: Optional[dict[IRI, str]] = None
        self.deposit_type_idmap: Optional[dict[InternalID, DepositType]] = None
        self.commodity_idmap: Optional[dict[InternalID, Commodity]] = None

    @staticmethod
    def get_instance():
        if EntityService.instance is None:
            EntityService.instance = EntityService()
        return EntityService.instance

    def get_commodity_form_conversion(self) -> dict[IRI, float]:
        if self.commodity_form_conversion is None:
            self.commodity_form_conversion = {
                comm_form.uri: comm_form.conversion
                for comm_form in self.get_commodity_forms()
            }
        return self.commodity_form_conversion

    def get_data_source_score(self) -> dict[IRI, float | None]:
        if self.data_source_score is None:
            self.data_source_score = {
                sid: source.score for sid, source in self.get_data_sources().items()
            }
        return self.data_source_score

    def get_crs_name(self) -> dict[IRI, str]:
        if self.crs_name is None:
            self.crs_name = {crs.uri: crs.name for crs in self.get_crs()}
        return self.crs_name

    def get_state_or_province_index(self) -> StateCountryIndex:
        """Every state with its country, name and code, for the merge-time repair."""
        if (
            not hasattr(self, "state_or_province_index")
            or self.state_or_province_index is None
        ):
            self.state_or_province_index = StateCountryIndex.build(
                self.get_state_or_province_idmap().values()
            )
        return self.state_or_province_index

    def get_deposit_type_idmap(self) -> dict[InternalID, DepositType]:
        if self.deposit_type_idmap is None:
            self.deposit_type_idmap = {dt.id: dt for dt in self.get_deposit_types()}
        return self.deposit_type_idmap

    def get_commodity_idmap(self) -> dict[InternalID, Commodity]:
        if self.commodity_idmap is None:
            self.commodity_idmap = {
                commodity.id: commodity for commodity in self.get_commodities()
            }
        return self.commodity_idmap

    def get_country_idmap(self) -> dict[InternalID, Country]:
        if not hasattr(self, "country_idmap") or self.country_idmap is None:
            self.country_idmap = {
                country.id: country for country in self.get_countries()
            }
        return self.country_idmap

    def get_country_uris(self) -> set[IRI]:
        if not hasattr(self, "country_uris") or self.country_uris is None:
            self.country_uris = {country.uri for country in self.get_countries()}
        return self.country_uris

    def get_state_or_province_idmap(self) -> dict[InternalID, StateOrProvince]:
        if (
            not hasattr(self, "state_or_province_idmap")
            or self.state_or_province_idmap is None
        ):
            self.state_or_province_idmap = {
                state_or_province.id: state_or_province
                for state_or_province in self.get_state_or_provinces()
            }
        return self.state_or_province_idmap

    def get_state_or_province_uris(self) -> set[IRI]:
        if (
            not hasattr(self, "state_or_province_uris")
            or self.state_or_province_uris is None
        ):
            self.state_or_province_uris = {
                state_or_province.uri
                for state_or_province in self.get_state_or_provinces()
            }
        return self.state_or_province_uris

    def get_deposit_type_uris(self) -> set[IRI]:
        if not hasattr(self, "deposit_type_uris") or self.deposit_type_uris is None:
            self.deposit_type_uris = {dt.uri for dt in self.get_deposit_types()}
        return self.deposit_type_uris

    def get_commodity_uris(self) -> set[IRI]:
        if not hasattr(self, "commodity_uris") or self.commodity_uris is None:
            self.commodity_uris = {c.uri for c in self.get_commodities()}
        return self.commodity_uris

    def get_commodity_form_uris(self) -> set[IRI]:
        if not hasattr(self, "commodity_form_uris") or self.commodity_form_uris is None:
            self.commodity_form_uris = {c.uri for c in self.get_commodity_forms()}
        return self.commodity_form_uris

    def get_category_uris(self) -> set[IRI]:
        if not hasattr(self, "category_uris") or self.category_uris is None:
            self.category_uris = {c.uri for c in self.get_categories()}
        return self.category_uris

    def get_unit_uris(self) -> set[IRI]:
        if not hasattr(self, "unit_uris") or self.unit_uris is None:
            self.unit_uris = {u.uri for u in self.get_units()}
        return self.unit_uris

    def get_units(self) -> list[Unit]:
        if self.units is None:
            self.units = self._select_all(Unit)
        return self.units

    def get_commodities(self) -> list[Commodity]:
        if self.commodities is None:
            self.commodities = self._select_all(Commodity)
        return self.commodities

    def get_deposit_types(self) -> list[DepositType]:
        if self.deposit_types is None:
            self.deposit_types = self._select_all(DepositType)
        return self.deposit_types

    def get_categories(self) -> list[Category]:
        if self.categories is None:
            self.categories = self._select_all(Category)
        return self.categories

    def get_countries(self) -> list[Country]:
        if self.countries is None:
            self.countries = self._select_all(Country)
        return self.countries

    def get_state_or_provinces(self) -> list[StateOrProvince]:
        if self.state_or_provinces is None:
            self.state_or_provinces = self._select_all(StateOrProvince)
        return self.state_or_provinces

    def get_commodity_forms(self) -> list[CommodityForm]:
        if self.commodity_forms is None:
            self.commodity_forms = self._select_all(CommodityForm)
        return self.commodity_forms

    def get_data_sources(self, refresh: bool = False) -> dict[IRI, DataSource]:
        if self.data_sources is None or refresh:
            self.data_sources = {s.id: s for s in self._select_all(DataSource)}
        return self.data_sources

    def get_crs(self) -> list[CRS]:
        if self.crs is None:
            self.crs = self._select_all(CRS)
        return self.crs

    def _select_all(self, cls: type[T]) -> list[T]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.execute(select(cls)).scalars())


class FileEntityService(EntityService):
    def __init__(self, entity_dir: Path):
        super().__init__()
        self.entity_dir = entity_dir

    def _select_all(self, cls: type[T]) -> list[T]:
        records = serde.json.deser(self.entity_dir / f"{cls.__tablename__}.json")[
            cls.__name__
        ]
        return [cls.from_dict(record) for record in records]


class RemoteEntityService(EntityService):

    def __init__(self, endpoint: str = "https://minmod.isi.edu/"):
        super().__init__()
        self.endpoint = endpoint

    def _select_all(self, cls: type[T]) -> list[T]:
        if cls is Unit:
            data = self._download_data("/api/v1/units")
            return [
                Unit(
                    id=NS_MR.id(x["uri"]),
                    name=x["name"],
                    aliases=x["aliases"],
                )
                for x in data
            ]  # type: ignore
        if cls is Commodity:
            data = self._download_data("/api/v1/commodities")
            return [
                Commodity(
                    id=x["id"],
                    name=x["name"],
                    aliases=x["aliases"],
                    parent=x["parent"],
                    is_critical=x["is_critical"],
                )
                for x in data
            ]  # type: ignore
        if cls is DepositType:
            data = self._download_data("/api/v1/deposit-types")
            return [
                DepositType(
                    id=NS_MR.id(x["uri"]),
                    name=x["name"],
                    environment=x["environment"],
                    group=x["group"],
                )
                for x in data
            ]  # type: ignore
        if cls is Category:
            data = self._download_data("/api/v1/categories")
            return [
                Category(
                    id=x["id"],
                    name=x["name"],
                )
                for x in data
            ]  # type: ignore
        if cls is Country:
            data = self._download_data("/api/v1/countries")
            return [
                Country(id=NS_MR.id(x["uri"]), name=x["name"], aliases=x["aliases"])
                for x in data
            ]  # type: ignore
        if cls is StateOrProvince:
            data = self._download_data("/api/v1/states-or-provinces")
            return [
                StateOrProvince(
                    id=NS_MR.id(x["uri"]),
                    name=x["name"],
                    country=x["country"],
                )
                for x in data
            ]  # type: ignore
        if cls is CommodityForm:
            data = self._download_data("/api/v1/commodity-forms")
            return [
                CommodityForm(
                    id=x["id"],
                    name=x["name"],
                    formula=x["formula"],
                    conversion=x["conversion"],
                    commodity=x["commodity"],
                )
                for x in data
            ]  # type: ignore
        if cls is DataSource:
            data = self._download_data("/api/v1/data-sources")
            return [
                DataSource(
                    id=x["uri"],
                    name=x["name"],
                    type=x["type"],
                    created_by="",
                    description="",
                    score=x["score"],
                    connection=x["connection"],
                )
                for x in data
            ]  # type: ignore
        raise NotImplementedError(f"Remote fetching not implemented for {cls.__name__}")

    def _download_data(self, url: str) -> list[dict]:
        response = httpx.get(urljoin(self.endpoint, url), verify=False, timeout=30)
        if response.status_code == 200:
            return response.json()

        error_msg = f"Failed to download data from {url}. Status code: {response.status_code}, Response: {response.text}"
        raise Exception(error_msg)
