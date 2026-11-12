"""Keyword Planner monthly data must use calendar months and preserve missing data."""

from __future__ import annotations

import json
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
    service = Mock()
    monkeypatch.setattr(client, "get_service", lambda name: service)
    monkeypatch.setattr(keyword_planner, "_build_ads_client", lambda: (client, "1234567890"))
    return client, service


def monthly_result(ads, records):
    client, service = ads
    response = client.get_type("GenerateKeywordIdeaResponse")
    response.results.append({
        "text": "seo tools",
        "keyword_idea_metrics": {"monthly_search_volumes": records},
    })
    service.generate_keyword_ideas.return_value = response
    result = keyword_planner.generate_keyword_ideas(["seo tools"])
    assert result["error"] is None
    return result["ideas"][0]["monthly_volumes"]


@pytest.mark.parametrize("month_name,calendar_month", [
    ("JANUARY", 1), ("JUNE", 6), ("DECEMBER", 12),
])
def test_month_enum_is_converted_to_calendar_month(ads, month_name, calendar_month):
    client, _ = ads
    result = monthly_result(ads, [{
        "year": 2026,
        "month": getattr(client.enums.MonthOfYearEnum, month_name),
        "monthly_searches": 120,
    }])
    assert result == [{"year": 2026, "month": calendar_month, "volume": 120}]


@pytest.mark.parametrize("month_name", ["UNSPECIFIED", "UNKNOWN"])
def test_unknown_month_is_not_reported_as_a_calendar_month(ads, month_name):
    client, _ = ads
    result = monthly_result(ads, [{
        "year": 2026,
        "month": getattr(client.enums.MonthOfYearEnum, month_name),
        "monthly_searches": 120,
    }])
    assert result[0]["month"] is None


def test_missing_monthly_volume_survives_json_as_null(ads):
    client, _ = ads
    result = monthly_result(ads, [{"year": 2026, "month": client.enums.MonthOfYearEnum.JANUARY}])
    assert json.loads(json.dumps(result))[0]["volume"] is None


def test_explicit_zero_monthly_volume_is_preserved(ads):
    client, _ = ads
    result = monthly_result(ads, [{
        "year": 2026, "month": client.enums.MonthOfYearEnum.JANUARY, "monthly_searches": 0,
    }])
    assert result[0]["volume"] == 0


def test_last_twelve_records_and_years_are_preserved(ads):
    client, _ = ads
    records = [{"year": 2025, "month": client.enums.MonthOfYearEnum.DECEMBER, "monthly_searches": 99}]
    for calendar_month in range(1, 13):
        records.append({"year": 2026, "month": calendar_month + 1, "monthly_searches": calendar_month})
    result = monthly_result(ads, records)
    assert len(result) == 12
    assert [row["year"] for row in result] == [2026] * 12
    assert [row["volume"] for row in result] == list(range(1, 13))


def test_empty_monthly_history_is_preserved(ads):
    assert monthly_result(ads, []) == []
