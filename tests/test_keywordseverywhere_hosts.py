"""Preserve the API's subdomain results separately from root-domain ranks."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import keywordseverywhere_api as api  # noqa: E402


def _lookup(monkeypatch, row):
    response = Mock(status_code=200)
    response.json.return_value = {"results": [row], "as_of": "2026-11-01", "invalid": []}
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: response)
    return api.get_rank(["blog.example.com"], "test-key")["data"]["domains"][0]


@pytest.mark.parametrize("score", [0, 4.5, None])
def test_subdomain_metrics_keep_their_own_scope(monkeypatch, score):
    host = {"host": "blog.example.com", "found": score is not None,
            "open_page_rank": score, "rank": 250, "referring_hosts": 42}
    result = _lookup(monkeypatch, {
        "domain": "example.com", "found": True, "open_page_rank": 9.0,
        "rank": 10, "referring_domains": 2000, "hosts": [host],
    })
    assert result["open_page_rank"] == 9.0
    assert result["referring_domains"] == 2000
    assert result["hosts"] == [{**host, "page_rank_decimal": score}]


def test_hosted_site_data_survives_a_not_found_domain_record(monkeypatch):
    result = _lookup(monkeypatch, {
        "domain": "project.github.io", "found": False, "open_page_rank": None,
        "hosts": [{"host": "project.github.io", "found": True, "open_page_rank": 2.5,
                   "rank": 10000, "referring_hosts": 20}],
    })
    assert result["found"] is False
    assert result["hosts"][0]["found"] is True
    assert result["hosts"][0]["open_page_rank"] == 2.5


def test_domain_only_response_keeps_its_existing_shape(monkeypatch):
    result = _lookup(monkeypatch, {"domain": "example.com", "found": True, "open_page_rank": 4.5})
    assert "hosts" not in result
    assert result["page_rank_decimal"] == 4.5


@pytest.mark.parametrize("hosts", ["unavailable", [{"host": None}]])
def test_malformed_host_results_are_not_silently_dropped(monkeypatch, hosts):
    response = Mock(status_code=200)
    response.json.return_value = {"results": [{"domain": "example.com", "hosts": hosts}]}
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: response)
    result = api.get_rank(["blog.example.com"], "test-key")
    assert result["status"] == "error"
    assert result["data"] is None


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_exposes_the_requested_host_score(monkeypatch, capsys, as_json):
    _lookup(monkeypatch, {
        "domain": "example.com", "found": True, "open_page_rank": 9.0,
        "hosts": [{"host": "blog.example.com", "found": True, "open_page_rank": 2.5,
                   "rank": 10000, "referring_hosts": 20}],
    })
    monkeypatch.setattr(api, "normalize_domain", lambda value: value)
    monkeypatch.setattr(api, "get_keywordseverywhere_api_key", lambda: "test-key")
    monkeypatch.setattr(sys, "argv", ["keywordseverywhere_api.py", "rank", "blog.example.com"]
                        + (["--json"] if as_json else []))
    api.main()
    output = capsys.readouterr().out
    if as_json:
        host = json.loads(output)["data"]["domains"][0]["hosts"][0]
        assert host["host"] == "blog.example.com"
        assert host["open_page_rank"] == 2.5
    else:
        line = next(line for line in output.splitlines() if "blog.example.com" in line)
        assert "rank=2.5/10" in line
