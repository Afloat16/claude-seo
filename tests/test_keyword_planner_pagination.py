"""Keyword ideas must follow the SDK pager until the requested limit is met."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("google.ads.googleads")

import keyword_planner  # noqa: E402
from google.ads.googleads.client import GoogleAdsClient  # noqa: E402
from google.auth.credentials import AnonymousCredentials  # noqa: E402


@pytest.fixture
def ads(monkeypatch):
    client = GoogleAdsClient(
        credentials=AnonymousCredentials(), developer_token="fixture", use_proto_plus=True,
    )
    service = client.get_service("KeywordPlanIdeaService")
    rpc = Mock()
    monkeypatch.setitem(service.transport._wrapped_methods, service.transport.generate_keyword_ideas, rpc)
    monkeypatch.setattr(client, "get_service", lambda name: service)
    monkeypatch.setattr(keyword_planner, "_build_ads_client", lambda: (client, "1234567890"))
    return client, rpc


def page(client, ideas, next_token=""):
    response = client.get_type("GenerateKeywordIdeaResponse")
    response.next_page_token = next_token
    for name, volume in ideas:
        response.results.append({"text": name, "keyword_idea_metrics": {"avg_monthly_searches": volume}})
    return response


def test_later_pages_fill_requested_limit_and_keep_volume_sort(ads):
    client, rpc = ads
    rpc.side_effect = [
        page(client, [("first", 10)], "next"),
        page(client, [("second", 30), ("third", 20)], "unused"),
    ]
    result = keyword_planner.generate_keyword_ideas(["seo"], limit=3)
    assert result["error"] is None
    assert [idea["keyword"] for idea in result["ideas"]] == ["second", "third", "first"]
    assert rpc.call_count == 2
    request = rpc.call_args.args[0]
    assert request.page_token == "next"
    assert request.keyword_seed.keywords == ["seo"]


def test_empty_page_with_next_token_is_followed(ads):
    client, rpc = ads
    rpc.side_effect = [page(client, [], "next"), page(client, [("found", 10)])]
    result = keyword_planner.generate_keyword_ideas(["seo"], limit=3)
    assert result["error"] is None
    assert [idea["keyword"] for idea in result["ideas"]] == ["found"]
    assert rpc.call_count == 2


def test_paging_failure_is_reported_with_existing_partial_results(ads):
    client, rpc = ads
    rpc.side_effect = [page(client, [("first", 10)], "next"), RuntimeError("next page unavailable")]
    result = keyword_planner.generate_keyword_ideas(["seo"], limit=3)
    assert [idea["keyword"] for idea in result["ideas"]] == ["first"]
    assert result["error"] == "Keyword Planner error: next page unavailable"


def test_limit_does_not_fetch_unneeded_page(ads):
    client, rpc = ads
    rpc.return_value = page(client, [("first", 10), ("second", 20)], "unused")
    result = keyword_planner.generate_keyword_ideas(["seo"], limit=1)
    assert result["error"] is None
    assert [idea["keyword"] for idea in result["ideas"]] == ["first"]
    assert rpc.call_count == 1


def test_single_page_keeps_existing_sort(ads):
    client, rpc = ads
    rpc.return_value = page(client, [("first", 10), ("second", 20)])
    result = keyword_planner.generate_keyword_ideas(["seo"], limit=3)
    assert result["error"] is None
    assert [idea["keyword"] for idea in result["ideas"]] == ["second", "first"]
    assert rpc.call_count == 1
