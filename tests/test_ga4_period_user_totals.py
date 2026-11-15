"""GA4 period users come from the API aggregate, not daily user sums."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("google.analytics.data_v1beta")

import ga4_report  # noqa: E402
from google.analytics.data_v1beta.types import MetricAggregation, RunReportResponse  # noqa: E402


def _daily_response(with_totals=True):
    rows = [{
        "dimension_values": [{"value": date}],
        "metric_values": [{"value": value} for value in values],
    } for date, values in [
        ("20261101", ("100", "80", "150", "0.2", "15", "0.8")),
        ("20261102", ("60", "50", "100", "0.2", "15", "0.8")),
    ]]
    totals = [{
        "dimension_values": [{"value": "RESERVED_TOTAL"}],
        # Some users return the next day: 100 distinct users, not 80 + 50.
        "metric_values": [{"value": value} for value in
                          ("160", "100", "250", "0.2", "15", "0.8")],
    }] if with_totals else []
    return RunReportResponse(rows=rows, totals=totals)


def _client(monkeypatch, *, with_totals=True):
    client = Mock()
    client.run_report.side_effect = [_daily_response(with_totals), RunReportResponse()]
    monkeypatch.setattr(ga4_report, "_build_ga4_client", lambda: client)
    return client


def test_period_users_use_requested_api_total(monkeypatch):
    client = _client(monkeypatch)

    report = ga4_report.organic_traffic_report("123", days=2)

    assert report["error"] is None
    assert report["totals"]["users"] == 100
    assert report["totals"]["sessions"] == 160
    assert report["totals"]["pageviews"] == 250
    assert [day["users"] for day in report["daily_data"]] == [80, 50]
    request = client.run_report.call_args_list[0].args[0]
    assert request.metric_aggregations == [MetricAggregation.TOTAL]
    assert client.run_report.call_count == 2


def test_missing_aggregate_does_not_report_daily_sum_as_distinct_users(monkeypatch):
    _client(monkeypatch, with_totals=False)

    report = ga4_report.organic_traffic_report("123", days=2)

    assert report["error"] is None
    assert report["totals"]["users"] is None
    assert report["totals"]["sessions"] == 160
    assert "users" in report["totals_warning"].lower()


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_handles_unavailable_user_total(monkeypatch, capsys, json_output):
    _client(monkeypatch, with_totals=False)
    argv = ["ga4_report.py", "--property", "123", "--days", "2"]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))

    ga4_report.main()

    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["totals"]["users"] is None
    else:
        assert "Users: n/a" in output.out
    assert "users" in output.err.lower()
