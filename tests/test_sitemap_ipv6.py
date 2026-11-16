"""IPv6 sitemap requests and displayed URLs must keep bracketed authorities."""

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("lxml")
import sitemap_discovery as discovery  # noqa: E402

HOST = "2606:4700:4700::1111"


@pytest.mark.parametrize("port", ["", ":443", ":8443"])
def test_origin_keeps_ipv6_brackets_and_ports(port):
    origin = f"https://[{HOST}]{port}"
    assert discovery._origin(origin + "/page?query=1") == origin
    assert urlparse(discovery._origin(origin)).hostname == HOST


@pytest.mark.parametrize("port", ["", ":8443"])
def test_redacted_display_url_remains_a_usable_ipv6_url(port):
    origin = f"https://[{HOST}]{port}"
    displayed, redacted = discovery._display_url(origin + "/sitemap.xml?token=EXAMPLE#fragment")
    assert displayed == origin + "/sitemap.xml"
    assert redacted is True
    assert urlparse(displayed).hostname == HOST


def test_discovery_fetches_ipv6_origin_and_preserves_its_found_urls(monkeypatch):
    origin = f"https://[{HOST}]:8443"
    declared = origin + "/catalog.xml?token=EXAMPLE"
    calls = []

    def fetch(url, _limit):
        calls.append(url)
        if url == origin + "/robots.txt":
            status, content = 200, f"Sitemap: {declared}\n".encode()
        elif url == declared:
            status, content = 200, f"<urlset><url><loc>{origin}/product</loc></url></urlset>".encode()
        else:
            status, content = 404, b""
        return {"status_code": status, "content": content, "content_type": "application/xml",
                "final_url": url, "too_large": False, "error": None}

    monkeypatch.setattr(discovery, "_bounded_fetch", fetch)
    report = discovery.discover_sitemaps(origin + "/page", expand=True)
    assert report["error"] is None
    assert calls[0] == origin + "/robots.txt"
    assert declared in calls
    assert report["found"][0]["url"] == origin + "/catalog.xml"
    assert report["found"][0]["query_redacted"] is True
    assert report["expanded"]["totals"]["total_urls"] == 1
    assert "EXAMPLE" not in str(report)


def test_private_ipv6_targets_are_still_refused_before_fetch(monkeypatch):
    def unexpected_fetch(*args):
        pytest.fail("private IPv6 target reached the fetcher")

    monkeypatch.setattr(discovery, "_bounded_fetch", unexpected_fetch)
    assert discovery.discover_sitemaps("http://[::1]/")["error"]
