"""Device and country CLI reports must render the requested breakdown."""

from __future__ import annotations

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
def client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(ga4_report, "_build_ga4_client", lambda: client)
    return client


def configure(client, monkeypatch, report, empty=False, json_output=False):
    metrics = ["1250", "900", "0.25", "0.75"] if report == "device" else ["1250", "900"]
    rows = [] if empty else [{
        "dimension_values": [{"value": "mobile" if report == "device" else "Germany"}],
        "metric_values": [{"value": value} for value in metrics],
    }]
    client.run_report.return_value = RunReportResponse(rows=rows)
    argv = ["ga4_report.py", "--property", "123456789", "--report", report, "--days", "7"]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))


@pytest.mark.parametrize("report", ["device", "country"])
@pytest.mark.parametrize("empty", [False, True])
def test_text_output_renders_selected_breakdown(client, monkeypatch, capsys, report, empty):
    configure(client, monkeypatch, report, empty=empty)
    ga4_report.main()
    output = capsys.readouterr()
    assert not output.err
    assert f"Organic Traffic by {report.title()}" in output.out
    assert "Property: 123456789" in output.out
    assert "Period:" in output.out
    assert "Pageviews:" not in output.out
    if empty:
        assert "No organic data for this period." in output.out
    else:
        assert ("mobile" if report == "device" else "Germany") in output.out
        assert "Sessions: 1,250 | Users: 900" in output.out
        if report == "device":
            assert "Bounce: 25.0% | Engagement: 75.0%" in output.out
    request = client.run_report.call_args.args[0]
    assert request.dimensions[0].name == ("deviceCategory" if report == "device" else "country")


@pytest.mark.parametrize("report", ["device", "country"])
def test_json_keeps_breakdown_structure(client, monkeypatch, capsys, report):
    configure(client, monkeypatch, report, json_output=True)
    ga4_report.main()
    result = json.loads(capsys.readouterr().out)
    key = "devices" if report == "device" else "countries"
    assert result["report"] == f"{report}_breakdown"
    assert result[key][0]["sessions"] == 1250
    assert result[key][0]["users"] == 900


@pytest.mark.parametrize("report", ["device", "country"])
def test_failed_breakdown_does_not_render_success(client, monkeypatch, capsys, report):
    configure(client, monkeypatch, report)
    client.run_report.side_effect = RuntimeError("report unavailable")
    with pytest.raises(SystemExit) as exc:
        ga4_report.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert "report unavailable" in output.err
    assert not output.out
