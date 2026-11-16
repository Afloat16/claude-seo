"""Equivalent IDN spellings route to one site without merging distinct domains."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from google_properties import normalize_site_domain, resolve_site_property  # noqa: E402


@pytest.mark.parametrize(("value", "expected"), [
    ("https://Bücher.Example./page", "xn--bcher-kva.example"),
    ("例え.テスト", "xn--r8jz45g.xn--zckzah"),
    ("faß.de", "xn--fa-hia.de"),
    ("bücher。example", "xn--bcher-kva.example"),
    ("xn--bcher-kva.example", "xn--bcher-kva.example"),
    ("EXAMPLE.COM.", "example.com"),
    ("https://192.0.2.1/", "192.0.2.1"),
    ("https://[2001:db8::1]/", "2001:db8::1"),
])
def test_site_identity_uses_ascii_idna(value, expected):
    assert normalize_site_domain(value) == expected


@pytest.mark.parametrize(("key", "domain"), [
    ("bücher.example", "xn--bcher-kva.example"),
    ("xn--bcher-kva.example", "https://bücher.example/page"),
])
@pytest.mark.parametrize(("service", "expected"), [
    ("gsc", "sc-domain:xn--bcher-kva.example"),
    ("ga4", "properties/200"),
])
def test_unicode_and_ascii_site_keys_route_to_the_same_properties(key, domain, service, expected):
    config = {"sites": {key: {
        "gsc_property": "sc-domain:xn--bcher-kva.example", "ga4_property_id": "properties/200",
    }}}
    assert resolve_site_property(config, service, domain) == expected


def test_idn_domain_property_covers_its_subdomain():
    config = {"sites": {"bücher.example": {
        "gsc_property": "sc-domain:bücher.example", "ga4_property_id": "properties/200",
    }}}
    assert resolve_site_property(config, "ga4", "shop.xn--bcher-kva.example") == "properties/200"


def test_legacy_property_keeps_its_original_identifier():
    config = {"default_property": "https://bücher.example/", "ga4_property_id": "properties/123"}
    assert resolve_site_property(config, "gsc", "xn--bcher-kva.example") == "https://bücher.example/"
    assert resolve_site_property(config, "ga4", "xn--bcher-kva.example") == "properties/123"


def test_equivalent_idn_mapping_keys_are_ambiguous():
    config = {"sites": {
        "bücher.example": {"ga4_property_id": "properties/1"},
        "xn--bcher-kva.example": {"ga4_property_id": "properties/2"},
    }}
    with pytest.raises(ValueError, match="Multiple sites entries"):
        resolve_site_property(config, "ga4", "bücher.example")


@pytest.mark.parametrize("domain", ["fass.de", "faß.de.evil.example", "notfaß.de"])
def test_idna_does_not_merge_distinct_sites(domain):
    config = {"default_property": "sc-domain:faß.de", "ga4_property_id": "properties/123"}
    with pytest.raises(ValueError, match="refusing to use a different site's default"):
        resolve_site_property(config, "ga4", domain)


def test_invalid_alabel_fails_before_property_selection():
    with pytest.raises(ValueError, match="Invalid site domain"):
        normalize_site_domain("xn--.example")
