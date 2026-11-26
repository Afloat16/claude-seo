"""CrUX image-subpart percentiles describe separate distributions."""

from __future__ import annotations

import copy
import json
import os
import sys
from unittest.mock import patch

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import lcp_subparts  # noqa: E402

URL = "https://example.com/"
KEYS = lcp_subparts._LCP_SUBPART_METRICS
_ABSENT = object()


def _record(values=(2385, 828, 417, 2088), overall=_ABSENT):
    metrics = {
        key: {"percentiles": {"p75": value}}
        for key, value in zip(KEYS, values, strict=True)
        if value is not _ABSENT
    }
    if overall is not _ABSENT:
        metrics["largest_contentful_paint"] = {"percentiles": {"p75": overall}}
    return {"record": {"metrics": metrics}}


def _analyse(raw, form_factor="PHONE"):
    with patch.object(lcp_subparts, "validate_url_strict", return_value=(URL, None)), \
         patch.object(lcp_subparts, "get_api_key", return_value="test-key"), \
         patch.object(lcp_subparts, "_query_crux", return_value=raw):
        return lcp_subparts.analyse(URL, form_factor)


@pytest.mark.parametrize("overall", [_ABSENT, None, 0, 400, 2000, 10000])
def test_independent_subparts_do_not_depend_on_overall_percentile(overall):
    result = _analyse(_record(overall=overall))
    assert result["ranked_subparts"] == [
        {"metric": KEYS[0], "p75_ms": 2385.0},
        {"metric": KEYS[3], "p75_ms": 2088.0},
        {"metric": KEYS[1], "p75_ms": 828.0},
        {"metric": KEYS[2], "p75_ms": 417.0},
    ]
    assert len(result["recommendations"]) == 4
    assert "dominant_subparts" not in result
    assert all("share" not in item for item in result["ranked_subparts"])


def test_different_populations_do_not_produce_more_than_100_percent_shares():
    result = _analyse(_record((3000, 2000, 1000, 500), overall=100))
    assert result["p75_lcp_ms"] == 100.0
    assert result["subparts_p75_ms"][KEYS[0]] == 3000.0
    assert "dominant_subparts" not in result
    assert "share" not in json.dumps(result)


def test_interpretation_explains_both_independent_aggregation_and_population():
    result = _analyse(_record(overall=4000))
    interpretation = result["interpretation"]
    assert interpretation["aggregation"] == "independent_p75"
    assert interpretation["subparts_population"] == "image_lcp_full_page_loads"
    assert interpretation["additive"] is False
    note = interpretation["note"].lower()
    for word in ("text", "video", "back-forward", "prerender", "trace"):
        assert word in note


@pytest.mark.parametrize("missing", [_ABSENT, None, "invalid"])
def test_missing_subparts_are_not_ranked_or_recommended(missing):
    result = _analyse(_record((missing, "1200.5", missing, 300), overall=3000))
    assert result["ranked_subparts"] == [
        {"metric": KEYS[1], "p75_ms": 1200.5},
        {"metric": KEYS[3], "p75_ms": 300.0},
    ]
    assert len(result["recommendations"]) == 2
    assert result["subparts_p75_ms"][KEYS[0]] is None


def test_observed_zero_is_preserved_without_recommending_remediation():
    result = _analyse(_record((0, 0, 0, 0), overall=3500))
    assert result["ranked_subparts"] == [
        {"metric": key, "p75_ms": 0.0} for key in KEYS
    ]
    assert result["recommendations"] == []


def test_ties_keep_all_observations_without_inventing_contribution_percentages():
    result = _analyse(_record((800, 800, 0, None), overall=1200))
    assert result["ranked_subparts"] == [
        {"metric": KEYS[0], "p75_ms": 800.0},
        {"metric": KEYS[1], "p75_ms": 800.0},
        {"metric": KEYS[2], "p75_ms": 0.0},
    ]
    assert len(result["recommendations"]) == 2


@pytest.mark.parametrize("raw", [{}, {"record": {}}, _record((None,) * 4, 4000)])
def test_no_image_measurements_do_not_turn_overall_lcp_into_an_image_diagnosis(raw):
    result = _analyse(raw)
    assert result["ranked_subparts"] == []
    assert result["recommendations"] == []


def test_recommendations_are_ordered_checks_not_causal_claims():
    result = _analyse(_record((600, 2000, 800, 1000), overall=500))
    checks = result["recommendations"]
    assert checks[0].startswith("Resource load delay:")
    assert checks[1].startswith("Element render delay:")
    assert checks[2].startswith("Resource load duration:")
    assert checks[3].startswith("Time to first byte:")
    assert all("if" in check.lower() and "trace" in check.lower() for check in checks)
    assert all("dominates" not in check.lower() for check in checks)


def test_raw_record_and_existing_measurement_fields_are_preserved():
    raw = _record((100, "200.5", 0, None), overall="3000")
    before = copy.deepcopy(raw)
    result = _analyse(raw, "DESKTOP")
    assert raw == before
    assert result["raw"] == before
    assert result["url"] == URL
    assert result["form_factor"] == "DESKTOP"
    assert result["p75_lcp_ms"] == 3000.0
    assert result["subparts_p75_ms"] == dict(zip(KEYS, [100.0, 200.5, 0.0, None], strict=True))


def test_provider_error_remains_an_error():
    assert _analyse({"error": "CrUX HTTP 404: not found"}) == {
        "error": "CrUX HTTP 404: not found",
    }


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_reports_independent_diagnostics_even_without_overall_lcp(as_json, capsys):
    args = ["lcp_subparts.py", URL, *(["--json"] if as_json else [])]
    with patch.object(sys, "argv", args), \
         patch.object(lcp_subparts, "validate_url_strict", return_value=(URL, None)), \
         patch.object(lcp_subparts, "get_api_key", return_value="test-key"), \
         patch.object(lcp_subparts, "_query_crux", return_value=_record()):
        assert lcp_subparts.main() == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    if as_json:
        result = json.loads(captured.out)
        assert result["p75_lcp_ms"] is None
        assert len(result["ranked_subparts"]) == 4
        assert result["interpretation"]["aggregation"] == "independent_p75"
        assert "raw" not in result
    else:
        assert "independent p75" in captured.out
        assert "image LCP on full page loads" in captured.out
        assert "Overall p75 LCP: n/a" in captured.out
        assert "2385" in captured.out
        assert "Time to first byte:" in captured.out
        assert "40%" not in captured.out
        assert "dominant" not in captured.out.lower()
