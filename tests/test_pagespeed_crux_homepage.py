"""Combined page audits must query page-level CrUX data before origin fallback."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("requests")
import pagespeed_check  # noqa: E402


def _response(status=200, p75=1800):
    response = Mock(status_code=status)
    response.json.return_value = {
        "record": {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": p75}}}}
    }
    return response


@pytest.mark.parametrize("url", [
    "https://example.com",
    "https://example.com/",
    "https://example.com/#section",
    "https://example.com/?campaign=search",
    "https://example.com/article",
])
def test_combined_audit_returns_the_page_metric_even_for_homepages(monkeypatch, url):
    calls = []

    def post(_endpoint, **kwargs):
        calls.append(kwargs["json"])
        # The home page is fast even though the aggregate site is slow.
        return _response(p75=1800 if "url" in kwargs["json"] else 4000)

    monkeypatch.setattr(pagespeed_check.requests, "post", post)
    monkeypatch.setattr(pagespeed_check, "run_pagespeed", lambda *args, **kwargs: {"error": None})

    result = pagespeed_check.combined_check(url, api_key="test", strategy="mobile")

    assert calls == [{"url": url}]
    metric = result["crux"]["metrics"]["largest_contentful_paint"]
    assert metric["p75"] == 1800
    assert metric["rating"] == "good"
    assert "note" not in result["crux"]


@pytest.mark.parametrize("url", ["https://example.com/", "https://example.com/article"])
def test_origin_fallback_is_labeled_and_only_used_after_missing_page_data(monkeypatch, url):
    calls = []

    def post(_endpoint, **kwargs):
        calls.append(kwargs["json"])
        return _response(status=404) if "url" in kwargs["json"] else _response(p75=4000)

    monkeypatch.setattr(pagespeed_check.requests, "post", post)
    monkeypatch.setattr(pagespeed_check, "run_pagespeed", lambda *args, **kwargs: {"error": None})

    result = pagespeed_check.combined_check(url, api_key="test", strategy="mobile")

    assert calls == [{"url": url}, {"origin": "https://example.com"}]
    assert result["crux"]["metrics"]["largest_contentful_paint"]["p75"] == 4000
    assert result["crux"]["note"] == "URL-level data unavailable; showing origin-level data"


@pytest.mark.parametrize("target,body", [
    ("https://example.com", {"origin": "https://example.com"}),
    ("https://example.com/", {"origin": "https://example.com"}),
    ("https://example.com/article", {"url": "https://example.com/article"}),
])
def test_direct_query_keeps_existing_scope_inference(monkeypatch, target, body):
    post = Mock(return_value=_response())
    monkeypatch.setattr(pagespeed_check.requests, "post", post)

    result = pagespeed_check.query_crux(target, "test", form_factor="PHONE")

    assert result["error"] is None
    assert post.call_args.kwargs["json"] == {**body, "formFactor": "PHONE"}
