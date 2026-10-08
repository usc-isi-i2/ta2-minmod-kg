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
  it and leaves ``observed_name`` in place for a curator.

It never guesses: there is no fuzzy matching.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

from minmodkg.typing import InternalID

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

    @classmethod
    def build(
        cls,
        states: Iterable,
        aliases: Optional[Mapping[InternalID, Sequence[str]]] = None,
    ) -> StateCountryIndex:
        """``states`` is any iterable of objects with .id, .name, .country and,
        optionally, .state_code (without it, the code tier never hits).
        ``aliases`` maps a state id to other names sources use for it; without
        it, the alias tier never hits."""
        aliases = aliases or {}
        self = cls()
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
