from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import serde.json
from minmodkg.etl.kgrel_entity import EntityDeserFn
from minmodkg.misc.state_repair import StateCountryIndex, drop_admin_words, fold_name
from minmodkg.models.kgrel.entities.state_or_province import StateOrProvince
from minmodkg.services.kgrel_entity import FileEntityService
from statickg.models.file_and_path import BaseType, InputFile, RelPath

ENTITY_DIR = Path(__file__).parent.parent / "resources/kgdata/entities"


@dataclass
class State:
    id: str
    name: str
    country: Optional[str]
    state_code: Optional[str] = None


IN, PK, AL, BO, CO = "IN", "PK", "AL", "BO", "CO"
STATES = [
    State("odisha", "Odisha", IN, "OR"),
    State("punjab_in", "Punjab", IN, "PB"),
    State("punjab_pk", "Punjab", PK, "PB"),
    State("berat_county", "Berat County", AL, "01"),
    State("berat_district", "Berat District", AL, "BR"),
    State("la_paz", "La Paz Department", BO, "L"),
    State("oruro", "Oruro Department", BO, "O"),
    State("colorado", "Colorado", CO, "CO"),
    State("boyaca", "Boyacá", CO, "BOY"),
]


def test_alias_resolves():
    idx = StateCountryIndex.build(STATES, {"odisha": ["Orissa"]})
    assert idx.resolve("Orissa", [IN]) == "odisha"
    # folded like real names: case, accents and punctuation do not matter
    assert idx.resolve("ORISSA", [IN]) == "odisha"
    assert idx.resolve("Orissá", [IN]) == "odisha"
    # and through the repair of a state that contradicts the recorded country
    assert idx.repair("punjab_pk", "Orissa", [IN]) == ("repoint", "odisha")


def test_alias_stays_in_its_country():
    idx = StateCountryIndex.build(STATES, {"odisha": ["Orissa"]})
    assert idx.resolve("Orissa", [PK]) is None


def test_alias_is_exact_only():
    # aliases never go through the admin-word-free tier
    idx = StateCountryIndex.build(STATES, {"odisha": ["Orissa"]})
    assert idx.resolve("Orissa State", [IN]) is None


def test_real_name_beats_an_alias():
    # "Punjab" is a real name in India; the same string as an alias of another
    # Indian state must not take it
    idx = StateCountryIndex.build(STATES, {"odisha": ["Punjab"]})
    assert idx.resolve("Punjab", [IN]) == "punjab_in"


def test_same_alias_on_two_states_of_one_country_is_ambiguous():
    idx = StateCountryIndex.build(
        STATES, {"odisha": ["Kalinga"], "punjab_in": ["Kalinga"]}
    )
    assert idx.resolve("Kalinga", [IN]) is None
    assert idx.repair("punjab_pk", "Kalinga", [IN]) == ("drop", None)


def test_same_alias_in_two_countries_is_not_ambiguous():
    idx = StateCountryIndex.build(
        STATES, {"odisha": ["Kalinga"], "punjab_pk": ["Kalinga"]}
    )
    assert idx.resolve("Kalinga", [IN]) == "odisha"
    assert idx.resolve("Kalinga", [PK]) == "punjab_pk"


def test_alias_never_changes_a_match_made_without_it():
    aliases = {
        # "La Paz" resolves today through the admin-word-free tier
        "oruro": ["La Paz"],
        # "CO" resolves today through the code tier
        "boyaca": ["CO"],
        # "Berat" is ambiguous today (Berat County / Berat District): it must
        # stay ambiguous, not fall through to an alias
        "berat_district": ["Berat"],
    }
    idx = StateCountryIndex.build(STATES, aliases)
    assert idx.resolve("La Paz", [BO]) == "la_paz"
    assert idx.resolve("CO", [CO]) == "colorado"
    assert idx.resolve("Berat", [AL]) is None


def test_no_aliases_behaves_exactly_as_before():
    states = _states(ENTITY_DIR)
    before = StateCountryIndex.build(states)
    for aliases in (None, {}, {s.id: [] for s in states}, {states[0].id: [" "]}):
        assert StateCountryIndex.build(states, aliases) == before


def test_aliases_cannot_break_any_existing_match():
    """On the real 5,084 states: every real name, admin-word-free name and code
    that resolves today becomes an alias of a different state in its country.
    Each must still resolve to what it resolves to without aliases."""
    states = _states(ENTITY_DIR)
    by_country: dict[str, list[StateOrProvince]] = {}
    for s in states:
        by_country.setdefault(s.country, []).append(s)

    probes: set[tuple[str, str]] = set()
    for s in states:
        probes.add((s.name, s.country))
        probes.add((drop_admin_words(fold_name(s.name)), s.country))
        if s.state_code:
            probes.add((s.state_code, s.country))

    plain = StateCountryIndex.build(states)
    expected = {}
    aliases: dict[str, list[str]] = {}
    for name, country in sorted(probes):
        target = plain.resolve(name, [country])
        if target is None:
            continue
        decoy = next((s for s in by_country[country] if s.id != target), None)
        if decoy is None:
            continue
        expected[(name, country)] = target
        aliases.setdefault(decoy.id, []).append(name)

    noisy = StateCountryIndex.build(states, aliases)
    assert len(expected) > 9000
    for (name, country), target in expected.items():
        assert noisy.resolve(name, [country]) == target, (name, country)


def test_alt_names_column_is_read_into_the_entity_json(tmp_path: Path):
    entity_dir = tmp_path / "entities"
    entity_dir.mkdir()
    shutil.copy(ENTITY_DIR / "country.csv", entity_dir)
    (entity_dir / "state_or_province.csv").write_text(
        "minmod_id,id,name,country_id,country_code,country_name,state_code,type,latitude,longitude,alt names\r\n"
        "Q3700,4013,Odisha,101,IN,India,OR,,20.9516658,85.0985236,Orissa\r\n"
        "Q3701,4015,Punjab,101,IN,India,PB,,31.1471305,75.3412179, Panjab |  | Pañjāb \r\n"
        "Q3702,4014,Puducherry,101,IN,India,PY,,11.9415915,79.8083133,\r\n"
        "Q3703,4012,Nagaland,101,IN,India,NL,,26.1584354,94.5624426,|",
        encoding="utf-8",
    )
    outdir = tmp_path / "out"
    outdir.mkdir()
    (tmp_path / "work").mkdir()
    EntityDeserFn(tmp_path / "work").invoke(
        infile=InputFile.from_relpath(
            RelPath(BaseType.REPO, tmp_path, "entities/state_or_province.csv")
        ),
        outdir=outdir,
    )
    records = serde.json.deser(outdir / "state_or_province.json")["StateOrProvince"]
    assert [r.get("aliases") for r in records] == [
        ["Orissa"],
        ["Panjab", "Pañjāb"],
        None,
        None,
    ]
    # Postgres loads these through from_dict, which leaves aliases out
    assert StateOrProvince.from_dict(records[0]).to_dict() == {
        "id": "Q3700",
        "name": "Odisha",
        "country": "Q1101",
        "state_code": "OR",
    }

    entser = FileEntityService(outdir)
    assert entser.get_state_or_province_aliases() == {
        "Q3700": ["Orissa"],
        "Q3701": ["Panjab", "Pañjāb"],
    }
    assert entser.get_state_or_province_index().resolve("Orissa", ["Q1101"]) == "Q3700"


def test_entity_json_is_unchanged_without_alt_names(tmp_path: Path):
    """No `alt names` column, or an empty one, writes exactly the JSON it did
    before the column existed."""
    header = "minmod_id,id,name,country_id,country_code,country_name,state_code,type,latitude,longitude"
    rows = [
        "Q3700,4013,Odisha,101,IN,India,OR,,20.9516658,85.0985236",
        "Q3701,4015,Punjab,101,IN,India,PB,,31.1471305,75.3412179",
    ]
    outputs = []
    for i, text in enumerate(
        [
            "\r\n".join([header] + rows),
            "\r\n".join([header + ",alt names"] + [r + "," for r in rows]),
        ]
    ):
        entity_dir = tmp_path / f"entities{i}"
        entity_dir.mkdir()
        shutil.copy(ENTITY_DIR / "country.csv", entity_dir)
        (entity_dir / "state_or_province.csv").write_text(text, encoding="utf-8")
        outdir = tmp_path / f"out{i}"
        outdir.mkdir()
        (tmp_path / f"work{i}").mkdir()
        EntityDeserFn(tmp_path / f"work{i}").invoke(
            infile=InputFile.from_relpath(
                RelPath(BaseType.REPO, entity_dir, "state_or_province.csv")
            ),
            outdir=outdir,
        )
        outputs.append((outdir / "state_or_province.json").read_bytes())
    assert outputs[0] == outputs[1]
    assert b"aliases" not in outputs[0]


# The checks below run on an entity directory: this repo's copy by default, or
# a ta2-minmod-data checkout with MINMOD_ENTITY_DIR=<checkout>/data/entities.
DATA_ENTITY_DIR = Path(os.environ.get("MINMOD_ENTITY_DIR", ENTITY_DIR))


def test_no_alias_equals_a_real_name_in_its_country():
    """Real names win over aliases, so such an alias would be dead vocabulary
    that silently resolves to the other state. Catch it instead."""
    states = _states(DATA_ENTITY_DIR)
    by_id = {s.id: s for s in states}
    real = {(s.country, fold_name(s.name)): s for s in states}
    clashes = [
        (sid, alias, real[(by_id[sid].country, fold_name(alias))].id)
        for sid, names in _aliases(DATA_ENTITY_DIR).items()
        for alias in names
        if (by_id[sid].country, fold_name(alias)) in real
    ]
    assert clashes == []


def test_every_alias_resolves_to_its_own_state():
    """Also catches an alias two states of one country share, and one that an
    admin-word-free name or a state code of the same country already takes."""
    states = _states(DATA_ENTITY_DIR)
    by_id = {s.id: s for s in states}
    aliases = _aliases(DATA_ENTITY_DIR)
    idx = StateCountryIndex.build(states, aliases)
    dead = [
        (sid, alias, idx.resolve(alias, [by_id[sid].country]))
        for sid, names in aliases.items()
        for alias in names
        if idx.resolve(alias, [by_id[sid].country]) != sid
    ]
    assert dead == []


def _states(entity_dir: Path) -> list[StateOrProvince]:
    return EntityDeserFn.read_state_or_province(entity_dir / "state_or_province.csv")


def _aliases(entity_dir: Path) -> dict[str, list[str]]:
    return EntityDeserFn.read_state_or_province_aliases(
        entity_dir / "state_or_province.csv"
    )
