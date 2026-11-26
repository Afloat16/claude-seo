"""URL-only references in Google's multi-page variant markup are not offers."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from schema_ecommerce_validate import validate  # noqa: E402


def full_product():
    return {
        "@type": "Product", "name": "Small blue coat", "image": "https://example.com/blue.jpg",
        "description": "Blue winter coat", "isVariantOf": {"@id": "#coat"},
        "offers": {"@type": "Offer", "price": 39.99, "priceCurrency": "USD", "availability": "InStock"},
    }


def group():
    return {"@type": "ProductGroup", "@id": "#coat", "name": "Winter coat", "productGroupID": "coat"}


def reference():
    return {"@type": "Product", "isVariantOf": {"@id": "#coat"},
            "url": "https://example.com/coat/green?size=small"}


def rules(result, severity=None):
    return [entry["rule"] for entry in result["findings"]
            if severity is None or entry["severity"] == severity]


def test_google_unnested_multi_page_shape_does_not_fail_on_external_variant():
    # The official product-variants guide has this shape on both colour pages:
    # a full local Product and URL-only Products for the other page's variants.
    payload = [group(), full_product(), reference()]
    before = copy.deepcopy(payload)
    result = validate(payload)
    assert result["ok"]
    assert not rules(result, "High")
    assert rules(result).count("linked-variant-reference") == 1
    notice = next(f for f in result["findings"] if f["rule"] == "linked-variant-reference")
    assert notice["severity"] == "Info"
    assert reference()["url"] in notice["message"]
    assert "not checked here" in notice["message"]
    assert payload == before


@pytest.mark.parametrize("relationship", ["nested", "linked", "inline-group"])
def test_group_membership_is_identified_from_both_variant_relationships(relationship):
    parent, local, remote = group(), full_product(), reference()
    if relationship == "nested":
        local.pop("isVariantOf")
        remote.pop("isVariantOf")
        parent["hasVariant"] = [local, remote]
        payload = parent
    elif relationship == "linked":
        local.pop("isVariantOf")
        local["@id"] = "#local"
        parent["hasVariant"] = [{"@id": "#local"}, remote]
        payload = {"@context": "https://schema.org", "@graph": [local, parent]}
    else:
        local["isVariantOf"] = parent
        payload = [remote, local]
    result = validate(payload)
    assert result["ok"]
    assert rules(result).count("linked-variant-reference") == 1


@pytest.mark.parametrize("prefix", ["https://schema.org/", "http://schema.org/"])
def test_expanded_schema_types_keep_the_variant_reference_behavior(prefix):
    parent, local, remote = group(), full_product(), reference()
    parent["@type"] = prefix + "ProductGroup"
    local["@type"] = [prefix + "Product", "Thing"]
    remote["@type"] = prefix + "Product"
    result = validate([remote, local, parent])
    assert result["ok"]
    assert "linked-variant-reference" in rules(result)


@pytest.mark.parametrize("url", [None, 42, [], "", "/other-variant", "mailto:coat@example.com", "https://", "http://[", "https://exa\nmple.com/coat", "https://example.com/a b"])
def test_missing_or_non_absolute_reference_url_is_not_a_product_exemption(url):
    remote = reference()
    remote["url"] = url
    result = validate([group(), full_product(), remote])
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)
    assert {"missing-product-name", "missing-product-image", "missing-product-offers"} <= set(rules(result))


@pytest.mark.parametrize("field,value", [("name", ""), ("image", []), ("offers", None), ("sku", "green-small")])
def test_partial_product_details_keep_normal_required_field_validation(field, value):
    remote = reference()
    remote[field] = value
    result = validate([group(), full_product(), remote])
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)


def test_standalone_url_only_product_is_still_incomplete():
    assert not validate(reference())["ok"]


def test_group_containing_only_references_is_still_incomplete():
    result = validate([group(), reference()])
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)


def test_unrelated_full_product_cannot_validate_another_groups_references():
    local = full_product()
    local["isVariantOf"] = {"@type": "ProductGroup", "@id": "#unrelated"}
    result = validate([group(), local, reference()])
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)


def test_unresolved_group_reference_is_not_assumed_valid():
    result = validate([full_product(), reference()])
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)


def test_duplicate_group_identifiers_do_not_collapse_ambiguous_definitions():
    other = group()
    other["name"] = "Conflicting coat group"
    result = validate([group(), other, full_product(), reference()])
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)


def test_group_identity_does_not_cross_separate_named_graphs():
    payload = [
        {"@id": "https://example.com/graph/one", "@graph": [group(), full_product()]},
        {"@id": "https://example.com/graph/two", "@graph": [group(), reference()]},
    ]
    result = validate(payload)
    assert not result["ok"]
    assert "linked-variant-reference" not in rules(result)


def test_actual_local_offer_failures_are_not_hidden_by_valid_variant_links():
    local = full_product()
    local["offers"] = {"@type": "AggregateOffer", "lowPrice": 10}
    result = validate([group(), local, reference()])
    assert not result["ok"]
    assert "merchant-listing-offer-type" in rules(result, "High")
    assert "linked-variant-reference" in rules(result)


def test_eu_checks_apply_to_full_product_instead_of_url_only_reference():
    local = full_product()
    local["energyEfficiencyClass"] = "A"
    result = validate([group(), local, reference()], require_eu_energy=True)
    assert result["ok"]
    assert "missing-eu-energy-class" not in rules(result)
    local.pop("energyEfficiencyClass")
    result = validate([group(), local, reference()], require_eu_energy=True)
    assert not result["ok"]
    assert rules(result).count("missing-eu-energy-class") == 1


def test_cli_preserves_json_contract_and_returns_success_for_variant_references():
    payload = [group(), full_product(), reference()]
    process = subprocess.run(
        [sys.executable, str(SCRIPTS / "schema_ecommerce_validate.py"), "--json"],
        input=json.dumps(payload), text=True, capture_output=True,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == validate(payload)
