from __future__ import annotations

import pytest
from minmodkg.misc.elements import element_index, element_symbol


def test_all_elements_listed():
    assert len(set(element_index().values())) == 118


@pytest.mark.parametrize(
    "text,symbol",
    [
        ("Ga", "Ga"),
        ("ga", "Ga"),
        ("GALLIUM", "Ga"),
        (" Selenium ", "Se"),
        ("Aluminium", "Al"),
        ("Aluminum", "Al"),
        ("Sulphur", "S"),
    ],
)
def test_symbol_or_name_any_case(text, symbol):
    assert element_symbol(text) == symbol


@pytest.mark.parametrize("text", ["Gal", "Fe2O3", "", "Q594"])
def test_unknown(text):
    assert element_symbol(text) is None
