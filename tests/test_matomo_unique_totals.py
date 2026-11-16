"""Daily distinct counts cannot be summed into a range-level distinct count."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import matomo_report  # noqa: E402


def _stub_reports(monkeypatch, daily, *, days=2):
    calls = []
    monkeypatch.setattr(matomo_report, "_date_range", lambda _days: (
        "2026-01-01", "2026-01-01" if days == 1 else "2026-01-02",
    ))

    def request(_url, _token, params, **kwargs):
        calls.append(params)
        data = daily if params["method"] == "VisitsSummary.get" else []
        return matomo_report._envelope("success", data)

    monkeypatch.setattr(matomo_report, "_request", request)
    return calls


def test_repeated_visitors_are_not_reported_as_a_deduplicated_total(monkeypatch):
    # Alice appears on both days: daily counts sum to four, but the union is three.
    visitors = ({"alice", "bob"}, {"alice", "carol"})
    daily = {f"2026-01-0{i}": {"nb_visits": 3, "nb_uniq_visitors": len(users)}
             for i, users in enumerate(visitors, 1)}
    calls = _stub_reports(monkeypatch, daily)
    result = matomo_report.organic_traffic_report("1", "https://example.com", "test", days=2)
    totals = result["data"]["totals"]
    assert totals["unique_visitors"] is None
    assert totals["daily_unique_visitors_sum"] == 4
    assert [row["unique_visitors"] for row in result["data"]["daily_data"]] == [2, 2]
    assert totals["visits"] == 6
    assert [call["method"] for call in calls] == ["VisitsSummary.get", "Actions.getEntryPageUrls"]
    assert calls[0]["period"] == "day"


@pytest.mark.parametrize("count", [0, 2])
def test_a_single_day_can_report_its_observed_unique_count(monkeypatch, count):
    daily = {"2026-01-01": {"nb_visits": count, "nb_uniq_visitors": count}}
    _stub_reports(monkeypatch, daily, days=1)
    result = matomo_report.organic_traffic_report("1", "https://example.com", "test", days=1)
    assert result["data"]["totals"]["unique_visitors"] == count
    assert result["data"]["totals"]["daily_unique_visitors_sum"] == count


def test_one_returned_day_does_not_prove_uniques_for_a_longer_range(monkeypatch):
    _stub_reports(monkeypatch, {"2026-01-01": {"nb_visits": 3, "nb_uniq_visitors": 2}})
    result = matomo_report.organic_traffic_report("1", "https://example.com", "test", days=2)
    assert result["data"]["totals"]["unique_visitors"] is None


def test_missing_single_day_metric_is_not_reported_as_zero_uniques(monkeypatch):
    _stub_reports(monkeypatch, {"2026-01-01": {"nb_visits": 3}}, days=1)
    result = matomo_report.organic_traffic_report("1", "https://example.com", "test", days=1)
    assert result["data"]["totals"]["unique_visitors"] is None


def test_human_report_labels_the_daily_sum(monkeypatch, capsys):
    daily = {f"2026-01-0{i}": {"nb_visits": 3, "nb_uniq_visitors": 2} for i in (1, 2)}
    _stub_reports(monkeypatch, daily)
    report = matomo_report.organic_traffic_report("1", "https://example.com", "test", days=2)
    matomo_report._print_human(report, "organic")
    output = capsys.readouterr().out
    assert "Unique Visitors: unavailable" in output
    assert "Daily Unique Visitor Sum: 4" in output
    assert "counted on multiple days" in output


def test_json_cli_keeps_the_unknown_range_count_explicit(monkeypatch, capsys):
    daily = {f"2026-01-0{i}": {"nb_visits": 3, "nb_uniq_visitors": 2} for i in (1, 2)}
    _stub_reports(monkeypatch, daily)
    monkeypatch.setattr(matomo_report, "get_matomo_url", lambda: "https://example.com")
    monkeypatch.setattr(matomo_report, "get_matomo_token", lambda: "test")
    monkeypatch.setattr(matomo_report, "get_matomo_site_id", lambda: "1")
    monkeypatch.setattr(sys, "argv", ["matomo_report.py", "organic", "--days", "2", "--json"])
    assert matomo_report.main() == 0
    totals = json.loads(capsys.readouterr().out)["data"]["totals"]
    assert totals["unique_visitors"] is None
    assert totals["daily_unique_visitors_sum"] == 4
