from __future__ import annotations

import pytest
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.sample_element import SampleElement


def make_sample(
    *elements: dict, is_deleted: bool = False, analysis_deleted: bool = False
) -> Sample:
    sample = Sample.from_raw_sample(
        {
            "sample_id": "S-1",
            "mineral_site_id": "site__x",
            "is_deleted": is_deleted,
            "analyses": [{"is_deleted": analysis_deleted, "elements": list(elements)}],
        }
    )
    sample.id = 7
    return sample


def rows(sample: Sample) -> list[tuple[str, bool]]:
    return [(r.element, r.is_bdl) for r in SampleElement.from_sample(sample)]


@pytest.mark.parametrize(
    "element,expected",
    [
        ({"label": "Ga", "grade": 1.2}, [("Ga", False)]),
        ({"label": "Ga", "grade": -0.6}, [("Ga", True)]),
        ({"label": "Ga", "grade": None, "detection_limit": 0.5}, []),
        ({"label": "Ga", "grade": 0.0, "detection_limit": 0.5}, []),
        ({"label": "Ga", "grade": 2.0, "detection_limit": 0.5}, [("Ga", False)]),
        ({"label": "Ga", "grade": None}, []),
        ({"label": "Ga", "grade": 0.0}, []),
    ],
)
def test_grade_decides_detected_or_below_detection(element, expected):
    assert rows(make_sample(element)) == expected


def test_row_carries_sample_and_site():
    (row,) = SampleElement.from_sample(make_sample({"label": "In", "grade": 3.0}))
    assert (row.sample_id, row.site_id) == (7, "site__x")


def test_label_is_normalized_and_unknown_labels_skipped():
    sample = make_sample(
        {"label": "ga", "grade": 1.0},
        {"label": "Gallium", "grade": 2.0},
        {"label": "Fe2O3", "grade": 5.0},
    )
    assert rows(sample) == [("Ga", False)]


def test_detected_and_below_detection_both_kept():
    sample = make_sample({"label": "Se", "grade": 1.0}, {"label": "Se", "grade": -0.2})
    assert rows(sample) == [("Se", False), ("Se", True)]


def test_deleted_sample_analysis_and_element_excluded():
    assert rows(make_sample({"label": "Co", "grade": 1.0}, is_deleted=True)) == []
    assert rows(make_sample({"label": "Co", "grade": 1.0}, analysis_deleted=True)) == []
    assert rows(make_sample({"label": "Co", "grade": 1.0, "is_deleted": True})) == []
