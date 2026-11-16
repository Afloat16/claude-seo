"""Unavailable input must not be reported as a clean GBP audit."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import requests

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gbp_deprecation_lint.py"
sys.path.insert(0, str(SCRIPT.parent))
import gbp_deprecation_lint as gbp  # noqa: E402


def response(status, html="<p>Page unavailable</p>"):
    result = requests.Response()
    result.status_code = status
    result.url = "https://example.com/?token=private-fixture-value"
    result._content = html.encode("utf-8")
    result.headers["Content-Type"] = "text/html; charset=utf-8"
    return result


@pytest.mark.parametrize("status", [404, 429, 500])
@pytest.mark.parametrize("json_mode", [False, True])
def test_http_error_is_not_scanned_or_reported_as_pass(monkeypatch, capsys, status, json_mode):
    decoded = []
    monkeypatch.setattr(gbp, "safe_requests_get", lambda *a, **k: response(status))
    monkeypatch.setattr(gbp, "decode_response_text", lambda r: decoded.append(r) or "<p>Page unavailable</p>")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "https://example.com/"] + (["--json"] if json_mode else []))
    assert gbp.main() == 2
    captured = capsys.readouterr()
    assert decoded == []
    assert "PASS" not in captured.out
    assert "private-fixture-value" not in captured.out + captured.err
    if json_mode:
        result = json.loads(captured.out)
        assert result["ok"] is False
        assert str(status) in result["error"]
        assert "summary" not in result
        assert "findings" not in result
    else:
        assert captured.out == ""
        assert str(status) in captured.err


@pytest.mark.parametrize("error", [requests.Timeout, requests.ConnectionError])
def test_transport_failures_emit_json_error_without_traceback(monkeypatch, capsys, error):
    def fail(*args, **kwargs):
        raise error("https://example.com/?token=private-fixture-value")

    monkeypatch.setattr(gbp, "safe_requests_get", fail)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "https://example.com/", "--json"])
    assert gbp.main() == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is False
    assert error.__name__ in result["error"]
    assert "private-fixture-value" not in captured.out + captured.err


@pytest.mark.parametrize("json_mode", [False, True])
def test_safety_refusal_uses_the_same_input_error_status(monkeypatch, capsys, json_mode):
    def fail(*args, **kwargs):
        raise gbp.URLSafetyError("Private address blocked")

    monkeypatch.setattr(gbp, "safe_requests_get", fail)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "http://127.0.0.1/"] + (["--json"] if json_mode else []))
    assert gbp.main() == 2
    captured = capsys.readouterr()
    if json_mode:
        result = json.loads(captured.out)
        assert result["ok"] is False
        assert "url_safety" in result["error"]
    else:
        assert captured.out == ""
        assert "url_safety" in captured.err


def test_missing_file_cli_emits_json_error_and_exit_two(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "missing.html"), "--file", "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["ok"] is False
    assert "file" in result["error"].lower()
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("html,exit_code", [("<p>Contact us by phone</p>", 0), ("<p>Message us via Google</p>", 1)])
def test_successful_page_retains_normal_findings_contract(monkeypatch, capsys, html, exit_code):
    monkeypatch.setattr(gbp, "safe_requests_get", lambda *a, **k: response(200, html))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "https://example.com/", "--json"])
    assert gbp.main() == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result == gbp.scan(html)
    assert "error" not in result
