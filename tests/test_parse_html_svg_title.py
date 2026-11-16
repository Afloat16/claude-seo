"""SVG accessibility titles must not become the document's SEO title."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import page_checks  # noqa: E402
from parse_html import parse_html  # noqa: E402

PAGE = "https://example.com/products/coffee"


@pytest.mark.parametrize(
    "body",
    [
        '<svg><title>Company logo</title><path d="M0 0"/></svg>',
        '<header><svg><title>Home</title></svg></header><main>Coffee products</main>',
        '<svg><title>Logo</title></svg><svg><title>Shopping cart</title></svg>',
    ],
)
def test_svg_titles_do_not_fill_a_missing_document_title(body):
    html = f"<!doctype html><html><head></head><body>{body}</body></html>"

    assert parse_html(html, PAGE)["title"] is None
    assert any(finding["id"] == "missing-title"
               for finding in page_checks.run_checks(html, PAGE)["findings"])


@pytest.mark.parametrize("title", ["Coffee products and brewing supplies", ""])
def test_document_title_keeps_precedence_over_svg_title(title):
    html = (f"<!doctype html><html><head><title>{title}</title></head><body>"
            '<svg><title>Company logo</title></svg></body></html>')

    assert parse_html(html, PAGE)["title"] == title


def test_file_cli_preserves_a_missing_document_title(tmp_path):
    source = tmp_path / "page.html"
    source.write_text(
        '<!doctype html><html><head></head><body><svg><title>Company logo</title>'
        '</svg><h1>Coffee products</h1></body></html>',
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "parse_html.py"), str(source), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout)["title"] is None
