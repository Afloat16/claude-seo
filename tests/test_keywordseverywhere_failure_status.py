"""An unavailable PageRank lookup must not become a successful empty report."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import keywordseverywhere_api as api  # noqa: E402


def _response(body, status=200):
    response = Mock(status_code=status)
    response.json.return_value = body
    return response


@pytest.mark.parametrize("body", [
    {}, {"error": {"message": "unavailable"}}, {"results": None}, {"results": {}},
    {"results": [None]}, {"results": [{"domain": "example.com"}, "bad row"]},
    {"results": [{}]}, {"results": [{"domain": None}]},
])
def test_missing_or_malformed_results_are_errors(monkeypatch, body):
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: _response(body))
    result = api.get_rank(["example.com"], "test-key")
    assert result["status"] == "error"
    assert result["data"] is None
    assert "response shape" in result["error"]


@pytest.mark.parametrize("status", [401, 429, 500])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_exits_nonzero_after_a_failed_lookup(monkeypatch, capsys, status, as_json):
    monkeypatch.setattr(api, "normalize_domain", lambda value: value)
    monkeypatch.setattr(api, "get_keywordseverywhere_api_key", lambda: "test-key")
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: _response({"error": "unavailable"}, status))
    monkeypatch.setattr(sys, "argv", ["keywordseverywhere_api.py", "rank", "example.com"]
                        + (["--json"] if as_json else []))
    with pytest.raises(SystemExit) as error:
        api.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    if as_json:
        assert json.loads(output.out)["status"] != "success"
    else:
        assert "Error:" in output.err


@pytest.mark.parametrize("rows", [[], [{"domain": "example.com", "found": False}]])
def test_valid_empty_and_not_found_responses_remain_successful(monkeypatch, rows):
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: _response({"results": rows, "invalid": []}))
    result = api.get_rank(["example.com"], "test-key")
    assert result["status"] == "success"
    assert result["error"] is None
    assert len(result["data"]["domains"]) == len(rows)


def test_successful_json_cli_keeps_its_zero_exit(monkeypatch, capsys):
    monkeypatch.setattr(api, "normalize_domain", lambda value: value)
    monkeypatch.setattr(api, "get_keywordseverywhere_api_key", lambda: "test-key")
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: _response({"results": []}))
    monkeypatch.setattr(sys, "argv", ["keywordseverywhere_api.py", "rank", "example.com", "--json"])
    assert api.main() is None
    assert json.loads(capsys.readouterr().out)["status"] == "success"


@pytest.mark.parametrize("kind,message", [
    ("address_error", "IP address"),
    ("authentication_error", "Invalid Keywords Everywhere API key"),
])
def test_403_distinguishes_account_address_restrictions_from_bad_keys(monkeypatch, kind, message):
    response = _response({"error": {"type": kind, "message": "test-key rejected"}}, status=403)
    monkeypatch.setattr(api, "_post", lambda *args, **kwargs: response)
    result = api.get_rank(["example.com"], "test-key")
    assert result["status"] == "error"
    assert message in result["error"]
    assert "test-key" not in result["error"]
