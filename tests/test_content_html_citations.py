"""Citation coverage should follow HTML anchor attributes, not attribute substrings."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import content_verify  # noqa: E402


@pytest.mark.parametrize("link", [
    '<a href="https://example.com/study">Study</a>',
    "<A HREF='HTTPS://example.com/study'>Study</A>",
    '<a href = "https://example.com/study">Study</a>',
    '<a href=https://example.com/study>Study</a>',
    '<a title="an angle > bracket" href="https://example.com/study">Study</a>',
    '<a href=" https://example.com/study ">Study</a>',
])
def test_real_http_anchors_count_as_citation_markers(link):
    result = content_verify.verify(f"Conversions rose 30%. {link}")
    assert result["claim_count"] == 1
    assert result["uncited_count"] == 0
    assert result["claims"][0]["nearby_citation"].startswith(("<a", "<A"))


@pytest.mark.parametrize("markup", [
    '<a data-href="https://example.com/study">Study</a>',
    '<a xhref="https://example.com/study">Study</a>',
    "<a title='href=\"https://example.com/study\"'>Study</a>",
    '<!-- <a href="https://example.com/study">Study</a> -->',
    '<script>const example = \'<a href="https://example.com/study">Study</a>\';</script>',
    '<a href="javascript:void(0)">Study</a>',
    '<a href="/study">Study</a>',
])
def test_markup_without_a_real_http_anchor_does_not_cover_a_claim(markup):
    result = content_verify.verify(f"Conversions rose 30%. {markup}")
    assert result["claim_count"] == 1
    assert result["uncited_count"] == 1
    assert result["claims"][0]["nearby_citation"] is None


def test_distant_anchor_stays_outside_the_citation_window():
    text = 'Conversions rose 30%. ' + ('padding ' * 60) + '<a href="https://example.com">Study</a>'
    assert content_verify.verify(text)["uncited_count"] == 1


@pytest.mark.parametrize("markup,exit_code", [
    ('<a data-href="https://example.com/study">Study</a>', 1),
    ('<a href = "https://example.com/study">Study</a>', 0),
])
def test_cli_threshold_uses_actual_anchor_citation_coverage(markup, exit_code):
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/content_verify.py"), "--json"],
        input=f"Conversions rose 30%. {markup}", text=True, capture_output=True, check=False,
    )
    assert completed.returncode == exit_code, completed.stderr
    assert json.loads(completed.stdout)["uncited_count"] == exit_code
