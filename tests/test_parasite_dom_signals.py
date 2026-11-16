"""Risk signals come from page text and outbound links, not source literals."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import parasite_risk as risk  # noqa: E402


@pytest.mark.parametrize("wrapper", [
    "<script>{}</script>", "<style>{}</style>", "<template>{}</template>", "<!-- {} -->",
    '<div data-example="{}">Ordinary reporting</div>',
])
def test_source_only_phrases_do_not_become_content_signals(wrapper):
    row = risk._audit_page("https://news.example/story", wrapper.format(
        "Sponsored Content Buy Now Shop Now https://shop.example/?tag=x&affid=y&ref_=z",
    ))
    assert row["third_party_hits"] == 0
    assert row["commerce_hits"] == 0
    assert row["affiliate_link_hits"] == 0
    assert risk._classify([row])["/story/"]["risk"] == "low"


def test_inline_elements_do_not_hide_visible_disclosure_and_commerce():
    row = risk._audit_page("https://news.example/", "<p>Sponsored <b>Content</b></p><p>Buy <em>Now</em></p>")
    assert row["third_party_hits"] == 1
    assert row["commerce_hits"] == 1


@pytest.mark.parametrize("mode", ["open", "closed"])
def test_declarative_shadow_content_keeps_its_signals(mode):
    html = f'<div><template shadowrootmode="{mode}">Sponsored Content</template></div>'
    assert risk._audit_page("https://news.example/", html)["third_party_hits"] == 1


def test_nested_inert_templates_do_not_contribute_anchors():
    html = ('<template><template><a href="https://shop.example/?tag=example">'
            'Buy Now</a></template></template>')
    row = risk._audit_page("https://news.example/", html)
    assert row["commerce_hits"] == 0
    assert row["affiliate_link_hits"] == 0


@pytest.mark.parametrize("href", [
    "/story?utm_source=nav", "https://NEWS.EXAMPLE./story?tag=local",
    "mailto:author@example.com?tag=email", "javascript:track('tag=code')",
    "https://shop.example/path/tag=not-a-query", "https://shop.example/?next=tag%3Dnested",
    "https://[invalid/?tag=broken",
])
def test_non_outbound_or_non_affiliate_targets_do_not_count(href):
    row = risk._audit_page("https://news.example/", f'<a href="{href}">Read more</a>')
    assert row["affiliate_link_hits"] == 0


def test_each_outbound_anchor_counts_once_even_with_multiple_markers():
    html = ('<a href="https://shop.example/?tag=one&amp;aff_id=two&amp;utm_source=three">Shop</a>'
            '<a href="//store.example/?TAG=other">More</a>')
    row = risk._audit_page("https://news.example/", html)
    assert row["affiliate_link_hits"] == 2


def test_document_base_is_used_for_relative_outbound_links():
    html = '<base href="https://shop.example/"><a href="product?tag=source">Product</a>'
    row = risk._audit_page("https://news.example/", html)
    assert row["affiliate_link_hits"] == 1


def test_scan_does_not_report_high_risk_from_script_literals(monkeypatch):
    class Response:
        url = "https://news.example/story"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(risk, "safe_requests_get", lambda *a, **k: Response())
    monkeypatch.setattr(risk, "decode_response_text", lambda _: (
        '<script>const banner = "Sponsored Content Buy Now Shop Now";</script>'
        "<article>Independent reporting from the newsroom.</article>"
    ))
    report = risk.scan([Response.url])
    assert report["pages_audited"] == 1
    assert report["errors"] == []
    assert report["overall_risk"] == "low"
