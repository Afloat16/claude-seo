"""JSON-LD snippets preserve text that resembles HTML script delimiters."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import schema_generate  # noqa: E402
from parse_html import parse_html  # noqa: E402


@pytest.mark.parametrize(
    "description",
    [
        "A tutorial about </script> tags",
        "A tutorial about </ScRiPt> tags",
        "A tutorial about </script > tags",
        "Cafés & <strong>coffee</strong>",
        "Ordinary profile text",
    ],
)
def test_script_tag_output_round_trips_through_html(description, capsys):
    payload = schema_generate.profile(
        "Coffee writer", url="https://example.com/about", description=description,
    )

    assert schema_generate._print(payload, Namespace(indent=2, script_tag=True)) == 0
    html = capsys.readouterr().out
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")

    assert len(scripts) == 1
    assert scripts[0]["type"] == "application/ld+json"
    assert json.loads(scripts[0].string) == payload
    parsed = parse_html(html, "https://example.com/about")
    assert parsed["schema"] == [payload]
    assert parsed["schema_invalid"]["count"] == 0


def test_plain_json_output_keeps_literal_markup(capsys):
    payload = {"description": "A tutorial about </script> tags"}

    schema_generate._print(payload, Namespace(indent=2, script_tag=False))
    output = capsys.readouterr().out

    assert "</script>" in output
    assert json.loads(output) == payload


def test_profile_cli_emits_embeddable_json_ld():
    description = "A tutorial about </script> tags"
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "schema_generate.py"), "--script-tag", "profile",
         "--name", "Coffee writer", "--url", "https://example.com/about",
         "--description", description],
        capture_output=True,
        text=True,
        check=True,
    )

    script = BeautifulSoup(result.stdout, "html.parser").find("script")
    payload = json.loads(script.string)
    assert payload["mainEntity"]["description"] == description
