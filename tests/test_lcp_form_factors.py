"""Exercise the LCP CLI against the CrUX form-factor request contract."""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import lcp_subparts  # noqa: E402


@pytest.mark.parametrize("form_factor", ["ALL_FORM_FACTORS", "PHONE", "DESKTOP", "TABLET"])
def test_cli_requests_aggregate_or_selected_form_factor(monkeypatch, capsys, form_factor):
    url = "https://93.184.216.34/"
    requests = []

    def query_record(request, timeout):
        payload = json.loads(request.data)
        requests.append(payload)
        # CrUX accepts three explicit enums; omitting the field aggregates them.
        if "formFactor" in payload and payload["formFactor"] not in {
            "PHONE", "DESKTOP", "TABLET",
        }:
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {}, io.BytesIO(b"Invalid formFactor")
            )
        metrics = {
            "largest_contentful_paint": {"percentiles": {"p75": 2500}},
            "largest_contentful_paint_image_time_to_first_byte": {"percentiles": {"p75": 500}},
        }
        return io.BytesIO(json.dumps({"record": {"metrics": metrics}}).encode())

    monkeypatch.setattr(lcp_subparts, "get_api_key", lambda: "test-api-key")
    monkeypatch.setattr(lcp_subparts.urllib.request, "urlopen", query_record)
    monkeypatch.setattr(sys, "argv", [
        "lcp_subparts.py", url, "--form-factor", form_factor, "--json",
    ])

    assert lcp_subparts.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["form_factor"] == form_factor
    assert report["p75_lcp_ms"] == 2500
    assert report["subparts_p75_ms"]["largest_contentful_paint_image_time_to_first_byte"] == 500
    assert len(requests) == 1
    assert requests[0]["url"] == url
    if form_factor == "ALL_FORM_FACTORS":
        assert "formFactor" not in requests[0]
    else:
        assert requests[0]["formFactor"] == form_factor
