from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Optional

import shapely.wkt
from minmodkg.misc.geo import reproject_geometry
from minmodkg.misc.state_repair import StateCountryIndex
from minmodkg.misc.utils import extend_unique, makedict
from minmodkg.models.kg.base import NS_MR
from minmodkg.models.kg.candidate_entity import CandidateEntity
from minmodkg.models.kg.location_info import LocationInfo
from minmodkg.typing import InternalID


@dataclass
class Location:
    country: list[CandidateEntity] = field(default_factory=list)
    state_or_province: list[CandidateEntity] = field(default_factory=list)
    crs: Optional[CandidateEntity] = None
    coordinates: Annotated[Optional[str], "WKT string"] = None

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("country", [ent.to_dict() for ent in self.country]),
                (
                    "state_or_province",
                    [ent.to_dict() for ent in self.state_or_province],
                ),
                ("crs", self.crs.to_dict() if self.crs else None),
                ("coordinates", self.coordinates),
            )
        )

    @classmethod
    def from_dict(cls, d):
        return cls(
            country=[CandidateEntity.from_dict(ent) for ent in d.get("country", [])],
            state_or_province=[
                CandidateEntity.from_dict(ent) for ent in d.get("state_or_province", [])
            ],
            crs=CandidateEntity.from_dict(d["crs"]) if d.get("crs") else None,
            coordinates=d.get("coordinates"),
        )

    def to_kg(self) -> LocationInfo:
        return LocationInfo(
            country=self.country,
            state_or_province=self.state_or_province,
            crs=self.crs,
            location=self.coordinates,
        )


@dataclass
class GeoCoordinate:
    lat: Optional[Annotated[float, "Latitude"]] = None
    lon: Optional[Annotated[float, "Longitude"]] = None

    def to_dict(self):
        return makedict.without_none((("lat", self.lat), ("lon", self.lon)))

    @classmethod
    def from_dict(cls, d: dict):
        return GeoCoordinate(lat=d.get("lat"), lon=d.get("lon"))


@dataclass
class LocationView(GeoCoordinate):
    country: list[InternalID] = field(default_factory=list)
    state_or_province: list[InternalID] = field(default_factory=list)

    def is_empty(self):
        return (
            self.lat is None
            and self.lon is None
            and len(self.country) == 0
            and len(self.state_or_province) == 0
        )

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("lat", self.lat),
                ("lon", self.lon),
                ("country", self.country),
                ("state_or_province", self.state_or_province),
            )
        )

    @classmethod
    def from_dict(cls, d):
        return cls(
            lat=d.get("lat"),
            lon=d.get("lon"),
            country=d.get("country", []),
            state_or_province=d.get("state_or_province", []),
        )

    def combine(self, other: Optional[LocationView]):
        if other is None:
            return self

        if self.lat is None and other.lat is not None:
            self.lat = other.lat
        if self.lon is None and other.lon is not None:
            self.lon = other.lon

        self.country = extend_unique(self.country, other.country)
        self.state_or_province = extend_unique(
            self.state_or_province, other.state_or_province
        )
        return self

    @staticmethod
    def from_location(
        location: Location,
        crss: dict[str, str],
        state_index: Optional[StateCountryIndex] = None,
    ) -> LocationView:
        view = LocationView()
        crs = None
        if location.coordinates is not None:
            if location.crs is None or location.crs.normalized_uri is None:
                crs = "EPSG:4326"
            else:
                crs = crss[location.crs.normalized_uri]

            # TODO: fix this nan
            if "nan" in location.coordinates.lower():
                centroid = None
            else:
                try:
                    assert crs.startswith("EPSG:")
                    geometry = shapely.wkt.loads(location.coordinates)
                    centroid = shapely.centroid(geometry)
                    centroid = reproject_geometry(centroid, crs, "EPSG:4326")
                except shapely.errors.GEOSException:
                    centroid = None

            if centroid is not None:
                view.lat = centroid.y
                view.lon = centroid.x

        view.country = [
            NS_MR.id(ent.normalized_uri)
            for ent in location.country
            if ent.normalized_uri is not None
        ]
        view.state_or_province = [
            NS_MR.id(ent.normalized_uri)
            for ent in location.state_or_province
            if ent.normalized_uri is not None
        ]
        if state_index is not None and len(view.country) > 0:
            # A state that sits in none of the recorded countries is re-resolved
            # inside them, or dropped. Only the view changes: `location` is what
            # goes to the KG, and keeps the source's observed_name for curators.
            # A dropped state that names a listed dependency of a recorded
            # country ("Greenland" under Denmark) moves the record to that
            # dependency's country instead; all decided against the recorded
            # countries, then applied. A dropped "Katanga" in DR Congo takes
            # the successor province that contains the record's point.
            states = []
            moves = {}
            for ent in location.state_or_province:
                if ent.normalized_uri is None:
                    continue
                state_id = NS_MR.id(ent.normalized_uri)
                _, state = state_index.repair(state_id, ent.observed_name, view.country)
                if state is not None:
                    states.append(state)
                    continue
                move = state_index.dependency(state_id, ent.observed_name, view.country)
                if move is not None:
                    moves[move[0]] = move[1]
                    continue
                state = state_index.katanga(
                    state_id,
                    ent.observed_name,
                    view.country,
                    location.coordinates,
                    crs,
                )
                if state is not None and state not in states:
                    states.append(state)
            view.state_or_province = states
            if moves:
                view.country = list(
                    dict.fromkeys(moves.get(c, c) for c in view.country)
                )
        return view
