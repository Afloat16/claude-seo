"""Link discovery must distinguish header delimiters from quoted text."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agentic_check as ac  # noqa: E402


@pytest.mark.parametrize("title", [
    '"Documentation, examples"',
    '"Documentation <guide>"',
    r'"Documentation \"guide\", examples"',
    '"See </fake.json>; rel=ai-catalog"',
])
def test_quoted_title_does_not_hide_or_invent_link_signals(title):
    header = f'</page.md>; title={title}; rel="alternate"; type="text/markdown"'
    assert ac.parse_link_header(header) == [
        {"uri": "/page.md", "rel": ["alternate"], "type": "text/markdown"},
    ]


def test_commas_in_uri_and_title_preserve_separate_links():
    header = ('</pages/a,b.md>; title="A, B"; rel=alternate; type="text/markdown", '
              '</catalog.json>; rel=ai-catalog')
    assert ac.parse_link_header(header) == [
        {"uri": "/pages/a,b.md", "rel": ["alternate"], "type": "text/markdown"},
        {"uri": "/catalog.json", "rel": ["ai-catalog"], "type": ""},
    ]


def test_quoted_pairs_are_unescaped_in_parameter_values():
    assert ac.parse_link_header(r'</page.md>; rel="alter\nate"; type="text\/markdown"') == [
        {"uri": "/page.md", "rel": ["alternate"], "type": "text/markdown"},
    ]


def test_first_relation_and_media_type_parameters_are_effective():
    header = ('</page.md>; rel=alternate; rel=ai-catalog; '
              'type="text/markdown"; type="application/json"')
    assert ac.parse_link_header(header) == [
        {"uri": "/page.md", "rel": ["alternate"], "type": "text/markdown"},
    ]


def test_unterminated_quoted_value_does_not_create_a_fake_catalog():
    assert ac.parse_link_header('</page.md>; title="See </fake.json>; rel=ai-catalog') == []


def test_page_audit_retains_discovery_signals_after_quoted_titles(monkeypatch):
    header = ('</page.md>; title="Read, then act"; rel=alternate; type="text/markdown", '
              '</catalog.json>; title="Tools, services"; rel=ai-catalog')
    monkeypatch.setattr(ac, "fetch", lambda _: {
        "status": 200, "error": None, "text": "<html><body>Example page</body></html>",
        "headers": {"link": header}, "final_url": "https://example.com/path",
    })
    _checks, page = ac.audit_page("https://example.com/path")
    assert page["soup_links"] == {
        "markdown_alternates": ["https://example.com/page.md"],
        "ai_catalog_links": ["https://example.com/catalog.json"],
        "ai_catalog_html": [],
    }
