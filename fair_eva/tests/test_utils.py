import pandas as pd
import pytest

import fair_eva.api.utils as ut

DOI = "10.1234/abcd.5678"
UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.mark.parametrize(
    "points, color",
    [(0, "#E74C3C"), (49, "#E74C3C"), (50, "#F4D03F"), (80, "#F4D03F"), (81, "#2ECC71")],
)
def test_get_color(points, color):
    assert ut.get_color(points) == color


@pytest.mark.parametrize(
    "points, status",
    [
        (0, "fail"),
        (50, "fail"),
        (51, "indeterminate"),
        (74, "indeterminate"),
        (75, "pass"),
        (100, "pass"),
    ],
)
def test_status(points, status):
    assert ut.test_status(points) == status


def test_get_doi_str_extracts_doi_from_url():
    assert ut.get_doi_str("https://doi.org/%s" % DOI) == DOI


def test_get_doi_str_without_doi():
    assert ut.get_doi_str("not a doi") == ""


def test_get_handle_str():
    assert ut.get_handle_str("http://hdl.handle.net/10261/12345") == "10261/12345"
    assert ut.get_handle_str("nothing") == ""


def test_get_orcid_str():
    assert ut.get_orcid_str("https://orcid.org/0000-0002-1825-0097") != ""
    assert ut.get_orcid_str("nothing") == ""


def test_is_uuid():
    assert ut.is_uuid(UUID)
    assert not ut.is_uuid("not-a-uuid")
    assert not ut.is_uuid(None)


def test_is_persistent_id():
    assert ut.is_persistent_id(DOI)
    assert ut.is_persistent_id(UUID)
    assert not ut.is_persistent_id("internal-id")


def test_is_unique_id():
    assert ut.is_unique_id(DOI)
    assert ut.is_unique_id(UUID)
    assert not ut.is_unique_id("internal-id")


def test_get_persistent_id_type():
    assert "doi" in ut.get_persistent_id_type(DOI)
    assert ut.get_persistent_id_type("internal-id") == ["internal"]


def test_pid_to_url():
    assert ut.pid_to_url("internal-id", "internal") == "internal-id"
    # The scheme (http/https) depends on the idutils version
    assert ut.pid_to_url(DOI, "doi").endswith("://doi.org/%s" % DOI)


def test_get_protocol_scheme():
    assert ut.get_protocol_scheme("https://example.org/a") == "https"
    assert ut.get_protocol_scheme("ftp://example.org/a") == "ftp"


@pytest.fixture
def metadata():
    return pd.DataFrame(
        [
            ["dc", "identifier", DOI, "doi"],
            ["dc", "title", "A title", None],
            ["dc", "creator", "", None],
        ],
        columns=["metadata_schema", "element", "text_value", "qualifier"],
        # object dtype keeps None as None (pandas>=3 would turn it into NaN)
        dtype=object,
    )


def test_check_metadata_terms_with_values_finds_term(metadata):
    terms = pd.DataFrame([["identifier", "doi"]], columns=["element", "qualifier"])
    result = ut.check_metadata_terms_with_values(metadata, terms)
    assert result["text_value"].tolist() == [DOI]


def test_check_metadata_terms_with_values_qualifier_none_matches(metadata):
    terms = pd.DataFrame([["title", "any"]], columns=["element", "qualifier"])
    result = ut.check_metadata_terms_with_values(metadata, terms)
    assert result["text_value"].tolist() == ["A title"]


def test_check_metadata_terms_with_values_ignores_empty_values(metadata):
    terms = pd.DataFrame([["creator", ""]], columns=["element", "qualifier"])
    assert ut.check_metadata_terms_with_values(metadata, terms).empty


def test_check_metadata_terms_with_values_not_found(metadata):
    terms = pd.DataFrame([["missing", ""]], columns=["element", "qualifier"])
    assert ut.check_metadata_terms_with_values(metadata, terms).empty


def test_find_ids_in_metadata(metadata):
    elements = pd.DataFrame({"term": ["identifier"]})
    ids = ut.find_ids_in_metadata(metadata, elements)
    assert ids["identifier"].tolist() == [DOI]
    assert "doi" in ids["type"].iloc[0]


def test_check_uri_in_term(metadata):
    uris = ut.check_uri_in_term(metadata, "identifier", "doi")
    assert uris == ["| identifier.doi = %s | " % DOI]
    assert ut.check_uri_in_term(metadata, "title", "doi") == []
