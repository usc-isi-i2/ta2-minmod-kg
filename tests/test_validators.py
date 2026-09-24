from __future__ import annotations

import pytest
from minmodkg.models.kg.sample import Sample
from minmodkg.validators import mineral_site_deser, validate_sample_shacl


class TestMineralSiteParser:

    def test_empty_record_id(self):
        raw = {
            "source_id": "https://minmod.isi.edu/users/a/bvu",
            "record_id": "",
            "created_by": "https://minmod.isi.edu/users/a/bvu",
            "dedup_site_uri": "https://minmod.isi.edu/derived/dedup_site__api-cdr-land-v1-docs-documents__02005ed95f9c1202261006876bc4b7cd8be3ead60226f6f0f22ccf558beafeb64d__inferlink",
            "name": "THE SELKIRK PROJECT",
            "aliases": [],
            "mineral_form": [],
            "deposit_type_candidate": [],
            "mineral_inventory": [],
            "reference": [
                {
                    "document": {
                        "uri": "https://minmod.isi.edu/users/a/bvu",
                        "title": "Unpublished document by Binh Vu for THE SELKIRK PROJECT (Nickel)",
                    },
                    "pageInfo": [],
                }
            ],
        }

        with pytest.raises(ValueError):
            ms = mineral_site_deser(raw)

    def test_missing_required_fields(self):
        raw = {
            "source_id": "https://minmod.isi.edu/users/a/bvu",
            # missing record_id
            "created_by": "https://minmod.isi.edu/users/a/bvu",
        }

        with pytest.raises(ValueError):
            mineral_site_deser(raw)

    def test_empty_reference_list(self):
        raw = {
            "source_id": "https://minmod.isi.edu/users/a/bvu",
            "record_id": "test_record_123",
            "created_by": "https://minmod.isi.edu/users/a/bvu",
            "name": "Test Site",
            "reference": [],
        }

        with pytest.raises(ValueError):
            mineral_site_deser(raw)

    def test_valid_minimal_site(self):
        raw = {
            "source_id": "https://minmod.isi.edu/users/a/bvu",
            "record_id": "test_record_123",
            "created_by": "https://minmod.isi.edu/users/a/bvu",
            "name": "Test Site",
            "reference": [
                {
                    "document": {
                        "uri": "https://minmod.isi.edu/users/a/bvu",
                        "title": "Test Document",
                    },
                    "pageInfo": [],
                }
            ],
        }

        ms = mineral_site_deser(raw)
        assert ms.record_id == "test_record_123"
        assert ms.name == "Test Site"

    def test_none_values_for_optional_fields(self):
        raw = {
            "source_id": "https://minmod.isi.edu/users/a/bvu",
            "record_id": "test_record_123",
            "created_by": "https://minmod.isi.edu/users/a/bvu",
            "name": None,
            "aliases": None,
            "reference": [
                {
                    "document": {
                        "uri": "https://minmod.isi.edu/users/a/bvu",
                        "title": "Test Document",
                    },
                    "pageInfo": [],
                }
            ],
        }

        with pytest.raises(ValueError):
            mineral_site_deser(raw)


class TestValidateSampleShacl:
    """validate_sample_shacl() (ta2-table-understanding issue #18's SHACL gate)
    against the real, GeoChem ontology/shapes from the ta2-table-understanding submodule and the real
    pyshacl pipeline -- not mocked, unlike test_sample.py's TestSHACLValidation,
    which isolates SampleService.publish()'s wiring instead. Docker-free: this
    only needs the KG-layer Sample dataclass, no DB."""

    def test_minimal_sample_conforms(self):
        sample = Sample.from_dict(
            {"sample_id": "SM-1", "mineral_site_id": "site__test__1__tester"}
        )
        assert validate_sample_shacl(sample) == []

    def test_grade_without_unit_is_advisory_not_blocking(self):
        """Regression guard: the shape declares this sh:severity sh:Warning, but
        pyshacl's SPARQLConstraintComponent results always come back as
        sh:Violation regardless (see _shacl_advisory_messages' docstring) --
        this pins that validate_sample_shacl still treats it as non-blocking."""
        sample = Sample.from_dict(
            {
                "sample_id": "SM-2",
                "mineral_site_id": "site__test__1__tester",
                "analyses": [
                    {
                        "analysis_id": "A-1",
                        "elements": [{"label": "Au", "grade": 2.5}],
                    }
                ],
            }
        )
        assert validate_sample_shacl(sample) == []

    def test_decimal_and_wkt_literals_do_not_spuriously_fail(self):
        """Regression guard for _normalize_graph_for_shacl: RDFModel.to_graph()
        builds decimal literals from Python floats in a way that keeps rdflib's
        cached value a `float` rather than a `decimal.Decimal`, and WKT strings
        typed xsd:string rather than geo:wktLiteral -- both would otherwise fail
        every sample with a grade or a location, regardless of real content."""
        sample = Sample.from_dict(
            {
                "sample_id": "SM-3",
                "mineral_site_id": "site__test__1__tester",
                "location": {"coordinates": "POINT(-84.6 35.6)"},
                "analyses": [
                    {
                        "analysis_id": "A-1",
                        "elements": [
                            {
                                "label": "Au",
                                "grade": 2.5,
                                "grade_unit": {
                                    "observed_name": "ppm",
                                    "confidence": 1.0,
                                    "source": "test",
                                },
                            }
                        ],
                    }
                ],
            }
        )
        assert validate_sample_shacl(sample) == []

    def test_edit_history_datatypes_do_not_spuriously_fail(self):
        """Regression guard: :EditEvent's SHACL shape (added along with
        :EditEvent itself, ta2-table-understanding PR #16) requires
        :updated_by as xsd:anyURI and :updated_at as xsd:dateTime, but
        RDFModel.to_graph() only knows these fields are Python strs and types
        them xsd:string -- would otherwise fail every sample with any edit
        history at all, i.e. every sample that's ever been saved once."""
        sample = Sample.from_dict(
            {
                "sample_id": "SM-4",
                "mineral_site_id": "site__test__1__tester",
                "edit_history": [
                    {
                        "updated_by": "https://minmod.isi.edu/users/u/tester",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "changed_properties": [
                            "https://geochemistry.isi.edu/ontology/sample_id"
                        ],
                    }
                ],
            }
        )
        assert validate_sample_shacl(sample) == []
