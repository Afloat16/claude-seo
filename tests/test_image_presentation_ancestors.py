"""Presentation removes container semantics without hiding contained images."""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import page_checks  # noqa: E402
from parse_html import parse_html  # noqa: E402

PAGE = "https://example.com/products/roaster"
IMAGE = '<img src="/roaster.webp" width="800" height="600">'

PRESENTATIONAL_CONTAINERS = [
    '<div role="presentation">{image}</div>',
    '<div role="none">{image}</div>',
    '<div role="Presentation"><span>{image}</span></div>',
    '<table role="presentation"><tr><td>{image}</td></tr></table>',
    '<ul role="none"><li>{image}</li></ul>',
    '<div role="none"><div role="presentation">{image}</div></div>',
]


@pytest.mark.parametrize("container", PRESENTATIONAL_CONTAINERS)
def test_parser_keeps_images_inside_presentational_containers(container):
    result = parse_html(container.format(image=IMAGE), PAGE)

    assert result["images"][0]["decorative"] is False


@pytest.mark.parametrize("container", PRESENTATIONAL_CONTAINERS)
def test_page_check_reports_missing_alt_inside_presentational_containers(container):
    result = page_checks.run_checks(container.format(image=IMAGE), PAGE)

    findings = [finding for finding in result["findings"]
                if finding["id"] == "images-missing-alt"]
    assert len(findings) == 1
    assert findings[0]["count"] == 1
    assert "/roaster.webp" in findings[0]["evidence"]


@pytest.mark.parametrize(
    "html",
    [
        '<div role="presentation"><img src="/roaster.webp" role="presentation"></div>',
        '<div role="none"><img src="/roaster.webp" role="none"></div>',
        '<div role="none" aria-hidden="true"><span>' + IMAGE + "</span></div>",
        '<div aria-hidden="TRUE"><div role="presentation">' + IMAGE + "</div></div>",
        '<div role="presentation"><img src="/roaster.webp" aria-hidden="true"></div>',
    ],
)
def test_explicitly_decorative_and_aria_hidden_images_remain_excluded(html):
    assert parse_html(html, PAGE)["images"][0]["decorative"] is True

    result = page_checks.run_checks(html, PAGE)
    assert all(finding["id"] != "images-missing-alt" for finding in result["findings"])


def test_empty_alt_inside_presentational_container_is_still_not_a_missing_alt():
    html = '<div role="presentation"><img src="/roaster.webp" alt=""></div>'

    assert parse_html(html, PAGE)["images"][0]["decorative"] is False
    result = page_checks.run_checks(html, PAGE)
    assert result["facts"]["images"]["empty_alt"] == 1
    assert all(finding["id"] != "images-missing-alt" for finding in result["findings"])


def test_named_image_inside_presentational_container_retains_its_alt_text():
    html = '<div role="none"><img src="/roaster.webp" alt="Coffee roaster"></div>'

    image = parse_html(html, PAGE)["images"][0]
    assert image["alt"] == "Coffee roaster"
    assert image["decorative"] is False
    result = page_checks.run_checks(html, PAGE)
    assert all(finding["id"] != "images-missing-alt" for finding in result["findings"])
