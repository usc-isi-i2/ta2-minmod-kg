"""Repair state_or_province candidates whose entity sits in a different country
than the one recorded on the same record.

The upstream matcher (ProcMine ``identify_entity_id``) keys its state lookup on
the bare lowercased name, keeps the first row in file order, falls back to the
best fuzzy score over every state in the world, and never sees the record's
country. So ``Florida`` resolves to Q5307 (Puerto Rico), and ``Potosi`` to Porto
(Portugal).

MinMod loads the same table keyed on id, so every state *is* reachable here,
each carrying its country. That is enough to repair the choice at merge time
without re-running extraction.

The rule is deliberately narrow:

* it engages only when the chosen state's country contradicts a country
  already recorded on the record -- a correct assignment is never touched;
* it re-resolves the record's own ``observed_name`` against only the states
  of the recorded country, in three tiers, stopping at the first that hits:
  the exact folded name, then the name with administrative words and
  stopwords dropped ("La Paz" -> "La Paz Department"), then the exact
  ``state_code`` ("CO" -> Colorado). Codes come last so a real name always
  beats a coincidental code;
* only when none of those tiers hits anything, it tries the state's aliases
  (the ``alt names`` column: "Orissa" -> Odisha), exact folded name only.
  Real names and codes always win, so adding an alias can turn a drop into a
  repoint but never changes or blocks a match the table already makes;
* a record with no ``observed_name`` is re-resolved by the chosen state's own
  name instead (Florida, Uruguay -> Florida, United States);
* a unique hit replaces the normalized_uri; no hit, or more than one, drops
  it and leaves ``observed_name`` in place for a curator;
* a state that would be dropped, and whose ``observed_name`` is a listed
  dependency of a recorded country (``DEPENDENCIES``: "Greenland" under
  Denmark), moves the record to the dependency's own country instead, with no
  state. Only an explicit list, and only for drops: a state that resolves is
  never touched;
* a dropped "Katanga" on a DR Congo record takes the 2015 successor province
  that contains the record's point (``katanga_successor``), unless the point is
  within 2 km of a border between two of them.

It never guesses: there is no fuzzy matching.
"""

from __future__ import annotations

import itertools
import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

import shapely
import shapely.wkt
from minmodkg.misc.geo import reproject_geometry
from minmodkg.typing import InternalID
from shapely.geometry import MultiPoint, Point, shape
from shapely.ops import unary_union

# Administrative words that sources drop and the reference list keeps,
# counted from the 5,084 names in state_or_province.csv.
ADMIN_WORDS = frozenset(
    {
        "district",
        "municipality",
        "province",
        "region",
        "prefecture",
        "county",
        "department",
        "oblast",
        "parish",
        "governorate",
        "state",
        "territory",
        "division",
        "council",
        "autonomous",
        "city",
        "area",
        "zone",
        "krai",
        "raion",
        "voivodeship",
        "canton",
        "emirate",
        "special",
        "metropolitan",
        "administrative",
        # the same words in the languages the reference list uses
        # ("Provincia de Cartago", "Estado de México", "Região Norte")
        "provincia",
        "departamento",
        "departement",
        "estado",
        "regiao",
        "regione",
        "provincie",
        "bundesland",
    }
)

# Function words dropped with them, so "El Beni" meets "Beni Department".
STOPWORDS = frozenset(
    {"of", "de", "del", "la", "el", "los", "las", "the", "du", "da", "dos", "das", "do"}
)

# Dependencies that have their own entry in country.csv and no state row under
# their sovereign: (sovereign, dependency, the names a state field uses for it).
# A record whose recorded country is the sovereign and whose state would be
# dropped, but names the dependency exactly (folded), is the dependency's
# record. Pairs are added only after review.
DEPENDENCIES: tuple[tuple[InternalID, InternalID, tuple[str, ...]], ...] = (
    # France -> New Caledonia
    (
        "Q1075",
        "Q1154",
        ("New Caledonia", "Territory of New Caledonia and Dependencies"),
    ),
    # Denmark -> Greenland
    ("Q1059", "Q1086", ("Greenland",)),
    # Australia -> Christmas Island
    ("Q1013", "Q1045", ("Christmas Island", "Territory of Christmas Island")),
    # United Kingdom -> Montserrat
    ("Q1234", "Q1146", ("Montserrat",)),
    # United Kingdom -> Virgin Islands (British)
    ("Q1234", "Q1243", ("Virgin Islands (British)", "British Virgin Islands")),
    # United Kingdom -> Cayman Islands
    ("Q1234", "Q1040", ("Cayman Islands",)),
)

# Never dependencies, whatever the list says: a state that shares a country's
# name is that state (Georgia is a US state), and China/Taiwan is not this
# repair's to decide.
NOT_DEPENDENCIES: frozenset[tuple[InternalID, InternalID]] = frozenset(
    {
        ("Q1044", "Q1216"),  # China / Taiwan
        ("Q1235", "Q1081"),  # United States / Georgia
        ("Q1158", "Q1157"),  # Nigeria / Niger
        ("Q1020", "Q1126"),  # Belgium / Luxembourg
        ("Q1092", "Q1132"),  # Guinea / Mali (Mali Prefecture)
    }
)

# Katanga was split in 2015, and state_or_province.csv has only its four
# successors. A dropped "Katanga" on a DR Congo record takes the successor that
# contains the record's point. Boundaries: katanga_successors.geojson, from
# OCHA / Référentiel Géographique Commun, via HDX and geoBoundaries
# (gbHumanitarian), CC BY 3.0 IGO.
KATANGA_COUNTRY: InternalID = "Q1058"  # Democratic Republic of the Congo
KATANGA_NAMES = frozenset({"katanga", "katanga province"})
KATANGA_SUCCESSORS = Path(__file__).parent / "katanga_successors.geojson"
# A point this close to a border between two successors is left empty. Metres
# in UTM 35S, whose central meridian (27°E) runs through the four.
KATANGA_BORDER_METRES = 2000
KATANGA_UTM = "EPSG:32735"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# NFKD does not decompose the Turkish dotless i, so "Elazığ" would fold to
# "elaz g"; map it (and the dotted capital) before normalising.
_TURKISH_I = str.maketrans({"ı": "i", "İ": "i"})


def fold_name(name: str) -> str:
    """Lowercase, strip diacritics, reduce punctuation to single spaces."""
    decomposed = unicodedata.normalize("NFKD", name.translate(_TURKISH_I))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", stripped.lower()).strip()


def drop_admin_words(folded: str) -> str:
    """Remove administrative words and stopwords. Falls back to the input if
    nothing is left."""
    words = [w for w in folded.split() if w not in ADMIN_WORDS and w not in STOPWORDS]
    return " ".join(words) if words else folded


@dataclass
class StateCountryIndex:
    """State -> country, plus per-country name, code and alias indexes for
    re-resolution."""

    state_country: dict[InternalID, Optional[InternalID]] = field(default_factory=dict)
    state_name: dict[InternalID, str] = field(default_factory=dict)
    _exact: dict[InternalID, dict[str, list[InternalID]]] = field(default_factory=dict)
    _folded: dict[InternalID, dict[str, list[InternalID]]] = field(default_factory=dict)
    _code: dict[InternalID, dict[str, list[InternalID]]] = field(default_factory=dict)
    _alias: dict[InternalID, dict[str, list[InternalID]]] = field(default_factory=dict)
    _dependency: dict[InternalID, dict[str, InternalID]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        states: Iterable,
        aliases: Optional[Mapping[InternalID, Sequence[str]]] = None,
        dependencies: Iterable[
            tuple[InternalID, InternalID, Sequence[str]]
        ] = DEPENDENCIES,
    ) -> StateCountryIndex:
        """``states`` is any iterable of objects with .id, .name, .country and,
        optionally, .state_code (without it, the code tier never hits).
        ``aliases`` maps a state id to other names sources use for it; without
        it, the alias tier never hits. ``dependencies`` is the reviewed list of
        (sovereign, dependency, names)."""
        aliases = aliases or {}
        self = cls()
        for sovereign, dependency, dep_names in dependencies:
            if (sovereign, dependency) in NOT_DEPENDENCIES:
                raise ValueError(f"{sovereign} -> {dependency} is not a dependency")
            for name in dep_names:
                self._dependency.setdefault(sovereign, {})[fold_name(name)] = dependency
        for s in states:
            self.state_country[s.id] = s.country
            self.state_name[s.id] = s.name
            if s.country is None:
                continue
            folded = fold_name(s.name)
            self._exact.setdefault(s.country, {}).setdefault(folded, []).append(s.id)
            self._folded.setdefault(s.country, {}).setdefault(
                drop_admin_words(folded), []
            ).append(s.id)
            code = getattr(s, "state_code", None)
            if code and code.strip():
                self._code.setdefault(s.country, {}).setdefault(
                    code.strip().upper(), []
                ).append(s.id)
            for alias in aliases.get(s.id, ()):
                key = fold_name(alias)
                if key:
                    self._alias.setdefault(s.country, {}).setdefault(key, []).append(
                        s.id
                    )
        return self

    def resolve(
        self, observed_name: Optional[str], country_ids: Sequence[InternalID]
    ) -> Optional[InternalID]:
        """The one state in ``country_ids`` matching ``observed_name``, or None.

        Exact folded name first so that a real "Berat County" is not confused
        with "Berat District" in the same country; only then the admin-word-free
        form, which is what lets "La Paz" reach "La Paz Department"; and only
        then the state code, case-insensitive, so a real name always beats a
        coincidental code. Aliases come last and are reached only when every
        tier before found nothing: an ambiguous real name stays ambiguous, and
        an alias can never take a name the table already resolves. They match
        exactly, never with administrative words dropped.
        """
        if not observed_name or not country_ids:
            return None
        folded = fold_name(observed_name)
        for index, key in (
            (self._exact, folded),
            (self._folded, drop_admin_words(folded)),
            (self._code, observed_name.strip().upper()),
            (self._alias, folded),
        ):
            hits: list[InternalID] = []
            for cid in country_ids:
                hits.extend(index.get(cid, {}).get(key, ()))
            hits = list(dict.fromkeys(hits))
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return None  # ambiguous inside the country; do not guess
        return None

    def conflicts(
        self, state_id: InternalID, country_ids: Sequence[InternalID]
    ) -> bool:
        """True when this state belongs to none of the recorded countries."""
        if not country_ids:
            return False
        owner = self.state_country.get(state_id)
        return owner is not None and owner not in country_ids

    def repair(
        self,
        state_id: InternalID,
        observed_name: Optional[str],
        country_ids: Sequence[InternalID],
    ) -> tuple[str, Optional[InternalID]]:
        """What to do with one state candidate: ("keep", id), ("repoint", new id)
        or ("drop", None).

        Untouched unless the state contradicts the recorded countries. A record
        without an ``observed_name`` is re-resolved by the chosen state's own
        name, which is what lets Florida (Uruguay) reach Florida (United States).
        """
        if not self.conflicts(state_id, country_ids):
            return ("keep", state_id)
        if observed_name and observed_name.strip():
            key = observed_name
        else:
            key = self.state_name.get(state_id)
        fixed = self.resolve(key, country_ids)
        return ("repoint", fixed) if fixed else ("drop", None)

    def dependency(
        self,
        state_id: InternalID,
        observed_name: Optional[str],
        country_ids: Sequence[InternalID],
    ) -> Optional[tuple[InternalID, InternalID]]:
        """(sovereign, dependency) when this state candidate would be dropped and
        its ``observed_name`` is a listed name of a dependency of one of the
        recorded countries; None otherwise, and always None for a candidate the
        repair keeps or repoints. The caller replaces the sovereign with the
        dependency in the record's countries; the state stays dropped."""
        if (
            not observed_name
            or self.repair(state_id, observed_name, country_ids)[0] != "drop"
        ):
            return None
        folded = fold_name(observed_name)
        hits = [
            (cid, self._dependency[cid][folded])
            for cid in dict.fromkeys(country_ids)
            if folded in self._dependency.get(cid, {})
        ]
        return hits[0] if len(hits) == 1 else None

    def katanga(
        self,
        state_id: InternalID,
        observed_name: Optional[str],
        country_ids: Sequence[InternalID],
        coordinates: Optional[str],
        crs: Optional[str],
    ) -> Optional[InternalID]:
        """The successor province for this state candidate when the record's
        country is DR Congo, its ``observed_name`` is Katanga and the repair
        would drop it: see ``katanga_successor``. None otherwise, and always
        None for a candidate the repair keeps or repoints."""
        if (
            set(country_ids) != {KATANGA_COUNTRY}
            or not observed_name
            or fold_name(observed_name) not in KATANGA_NAMES
            or self.repair(state_id, observed_name, country_ids)[0] != "drop"
        ):
            return None
        return katanga_successor(coordinates, crs)


def katanga_successor(
    coordinates: Optional[str], crs: Optional[str]
) -> Optional[InternalID]:
    """The successor province containing a POINT, or every point of a
    MULTIPOINT, given as WKT in ``crs``. None when there is no usable point (0,0
    or a round placeholder, as in the Problem 2 coverage check), a point lies
    outside all four, the points fall in different provinces, or a point is
    within KATANGA_BORDER_METRES of a border between two of them. Coordinates
    are taken as given: a sign-flipped point lands outside and stays empty."""
    if not coordinates or not crs or not crs.startswith("EPSG:"):
        return None
    try:
        geometry = shapely.wkt.loads(coordinates)
    except shapely.errors.GEOSException:
        return None
    if geometry.is_empty or not isinstance(geometry, (Point, MultiPoint)):
        return None
    points = [geometry] if isinstance(geometry, Point) else list(geometry.geoms)
    if all(p.x == 0 and p.y == 0 for p in points) or all(
        (p.x * 2).is_integer() and (p.y * 2).is_integer() for p in points
    ):
        return None
    provinces, border = _katanga_provinces()
    found = set()
    for point in points:
        point = reproject_geometry(point, crs, "EPSG:4326")
        hits = [qid for qid, polygon in provinces if polygon.contains(point)]
        if len(hits) != 1:
            return None
        utm = reproject_geometry(point, "EPSG:4326", KATANGA_UTM)
        if border.distance(utm) <= KATANGA_BORDER_METRES:
            return None
        found.add(hits[0])
    return found.pop() if len(found) == 1 else None


@cache
def _katanga_provinces():
    """[(id, polygon)] in WGS84, and the borders the four share with each other
    in KATANGA_UTM. Read once per process."""
    with open(KATANGA_SUCCESSORS, encoding="utf-8") as f:
        features = json.load(f)["features"]
    provinces = [(f["properties"]["minmod_id"], shape(f["geometry"])) for f in features]
    border = unary_union(
        [
            a.boundary.intersection(b.boundary)
            for (_, a), (_, b) in itertools.combinations(provinces, 2)
        ]
    )
    for _, polygon in provinces:
        shapely.prepare(polygon)
    return provinces, reproject_geometry(border, "EPSG:4326", KATANGA_UTM)
