"""Ordinary template contents are not active document metadata or resources."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import page_checks  # noqa: E402
import parse_html  # noqa: E402

URL = "https://example.com/guides/page"
INERT = (
    '<base href="http://unused.example/">'
    '<link rel="canonical" href="http://unused.example/copy">'
    '<meta name="robots" content="noindex,nofollow">'
    '<meta name="description" content="Unused description">'
    '<meta property="og:title" content="Unused title">'
    '<img src="http://unused.example/image.png">'
    '<a href="http://unused.example/link"></a>'
    '<script type="application/ld+json">{"@type":"Product"}</script>'
    '<p>Unused template text</p>'
)


@pytest.fixture(params=["html.parser", "lxml"])
def parser(request, monkeypatch):
    if request.param == "lxml":
        pytest.importorskip("lxml")
    monkeypatch.setattr(page_checks, "_HTML_PARSER", request.param)
    monkeypatch.setattr(parse_html, "_HTML_PARSER", request.param)


@pytest.mark.parametrize("attribute", ["", ' shadowrootmode="invalid"'])
def test_template_metadata_does_not_override_the_document(parser, attribute):
    html = (
        '<html><head><meta name="robots" content="index">'
        '<meta name="description" content="Live description"></head><body>'
        f"<template{attribute}>{INERT}</template>"
        '<link rel="canonical" href="https://example.com/guides/page">'
        '<img src="image.png" alt="Live" width="10" height="20">'
        '<a href="next">Live link</a></body></html>'
    )
    data = parse_html.parse_html(html, URL)
    assert data["meta_robots"] == "index"
    assert data["meta_description"] == "Live description"
    assert data["canonical"] == URL
    assert [image["src"] for image in data["images"]] == [
        "https://example.com/guides/image.png"
    ]
    assert len(data["links"]["internal"]) == 1
    assert data["links"]["external"] == []
    assert data["schema"] == []
    assert data["open_graph"] == {}
    assert data["hidden_text"]["by_source"]["template"] > 0


def test_template_does_not_generate_false_findings(parser):
    html = f'<html><body><template>{INERT}</template><h1>Live page</h1></body></html>'
    result = page_checks.run_checks(html, URL)
    ids = {finding["id"] for finding in result["findings"]}
    assert not ids & {
        "noindex", "nofollow", "canonical-points-elsewhere", "images-missing-alt",
        "images-missing-dimensions", "links-without-text", "mixed-content", "links-to-http",
    }
    assert {"missing-canonical", "missing-meta-description"} <= ids


def test_nested_template_does_not_activate_a_shadow_template(parser):
    html = f'<template><div><template shadowrootmode="open">{INERT}</template></div></template>'
    data = parse_html.parse_html(html, URL)
    assert data["images"] == []
    assert data["meta_robots"] is None
    assert data["hidden_text"]["by_source"]["template"] > 0


@pytest.mark.parametrize("mode", ["open", "closed", "OPEN"])
def test_declarative_shadow_content_keeps_existing_resource_checks(parser, mode):
    html = f'<div><template shadowrootmode="{mode}"><img src="/live.png"></template></div>'
    data = parse_html.parse_html(html, URL)
    assert [image["src"] for image in data["images"]] == ["https://example.com/live.png"]
    ids = {finding["id"] for finding in page_checks.run_checks(html, URL)["findings"]}
    assert "images-missing-alt" in ids


def test_live_metadata_and_resources_still_generate_findings(parser):
    html = f"<html><body>{INERT}</body></html>"
    data = parse_html.parse_html(html, URL)
    assert data["meta_robots"] == "noindex,nofollow"
    assert len(data["images"]) == 1
    ids = {finding["id"] for finding in page_checks.run_checks(html, URL)["findings"]}
    assert {"noindex", "nofollow", "images-missing-alt", "mixed-content"} <= ids
