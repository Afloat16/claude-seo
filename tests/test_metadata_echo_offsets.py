"""A normalized title match must not consume characters from the CTA tail."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import metadata_template as metadata  # noqa: E402

PAIRS = [
    ("Technical SEO (crawl, index, render) audit guide",
     "Technical SEO crawl index render audit guide. Get started."),
    ("Technical SEO   crawl    optimization    checklist",
     "Technical SEO crawl optimization checklist. Try it free now."),
    ("The complete technical search optimization handbook for large online stores and publishers",
     "The complete technical search optimization handbook for large. Get started."),
]


@pytest.mark.parametrize(("title", "description"), PAIRS)
def test_cta_survives_normalized_or_truncated_title_echo(title, description):
    result = metadata.analyse(title, description)
    assert result["templated"] is True
    assert result["severity"] == "high"
    assert result["cta_phrase"] in ("get started", "try it free now")


def test_a_cta_inside_a_fully_repeated_title_is_not_an_appended_tail():
    title = "The complete technical search optimization handbook for large online stores: Get started"
    result = metadata.analyse(title, title)
    assert result["templated"] is False
    assert "description-duplicates-title" in result["flags"]


def test_normalized_echo_without_cta_remains_medium():
    result = metadata.analyse(PAIRS[0][0], "Technical SEO crawl index render audit guide with examples.")
    assert result["templated"] is False
    assert result["severity"] == "medium"


def test_pairs_cli_retains_site_level_template_detection(tmp_path):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([
        {"url": f"https://example.com/{index}", "title": title, "description": description}
        for index, (title, description) in enumerate(PAIRS)
    ]), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "metadata_template.py"), "--pairs-file", str(path),
         "--json", "--fail-on", "site"],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["templated_count"] == 3
    assert result["site_risk"] == "high"
