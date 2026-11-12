"""Period users must come from GA4's distinct count, not a sum of daily users."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("google.analytics.data_v1beta")

import ga4_report  # noqa: E402
from google.analytics.data_v1beta.types import RunReportResponse  # noqa: E402


@pytest.fixture
def reports(monkeypatch):
    def install(users_by_day, fail_users=False):
        client = Mock()
        daily_request = None

        def run_report(request):
            nonlocal daily_request
            dimensions = [item.name for item in request.dimensions]
            if dimensions == ["date"]:
                daily_request = request
                return RunReportResponse(rows=[{
                    "dimension_values": [{"value": f"202611{10 + i:02d}"}],
                    "metric_values": [{"value": str(value)} for value in
                                      (len(users) * 2, len(users), len(users) * 6,
                                       0.2, 15, 0.8)],
                } for i, users in enumerate(users_by_day)])
            if dimensions == ["landingPage"]:
                return RunReportResponse()
            assert dimensions == []
            assert [metric.name for metric in request.metrics] == ["totalUsers"]
            assert request.property == daily_request.property
            assert request.date_ranges == daily_request.date_ranges
            assert request.dimension_filter == daily_request.dimension_filter
            assert request.dimension_filter.filter.field_name == "sessionDefaultChannelGroup"
            assert request.dimension_filter.filter.string_filter.value == "Organic Search"
            if fail_users:
                raise RuntimeError("period users unavailable")
            unique_users = len(set().union(*users_by_day))
            return RunReportResponse(rows=[{
                "metric_values": [{"value": str(unique_users)}],
            }])

        client.run_report.side_effect = run_report
        monkeypatch.setattr(ga4_report, "_build_ga4_client", lambda: client)
        return client

    return install


@pytest.mark.parametrize("users_by_day", [
    [{"returning"}, {"returning"}],
    [{"first", "returning"}, {"returning", "last"}],
    [{"first"}, {"last"}],
])
def test_users_are_distinct_across_the_whole_period(reports, users_by_day):
    reports(users_by_day)
    result = ga4_report.organic_traffic_report("123456789", days=2)

    assert result["error"] is None
    assert result["totals"]["users"] == len(set().union(*users_by_day))
    assert [row["users"] for row in result["daily_data"]] == [len(u) for u in users_by_day]
    assert result["totals"]["sessions"] == sum(len(u) * 2 for u in users_by_day)
    assert result["totals"]["pageviews"] == sum(len(u) * 6 for u in users_by_day)


def test_missing_period_users_does_not_fall_back_to_daily_sum(reports):
    reports([{"returning"}, {"returning"}], fail_users=True)
    result = ga4_report.organic_traffic_report("123456789", days=2)

    assert result["totals"]["users"] is None
    assert "period users unavailable" in result["totals_error"]
    assert result["totals"]["sessions"] == 4
    assert len(result["daily_data"]) == 2
    assert result["error"] is None


def test_empty_daily_report_does_not_request_period_users(reports):
    client = reports([])
    result = ga4_report.organic_traffic_report("123456789", days=2)

    assert result["totals"] == {}
    assert result["error"] is None
    assert client.run_report.call_count == 2


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_reports_unavailable_users_without_inventing_a_total(
    reports, monkeypatch, capsys, json_output,
):
    reports([{"returning"}, {"returning"}], fail_users=True)
    argv = ["ga4_report.py", "--property", "123456789", "--days", "2"]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))

    ga4_report.main()

    output = capsys.readouterr()
    if json_output:
        result = json.loads(output.out)
        assert result["totals"]["users"] is None
        assert "period users unavailable" in result["totals_error"]
    else:
        assert "Users: unavailable" in output.out
        assert "period users unavailable" in output.err
