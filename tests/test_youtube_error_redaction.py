"""YouTube SDK error URLs must not disclose the API key in report output."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("googleapiclient")

import httplib2  # noqa: E402
import youtube_search  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

SECRET = "AIza" + "x" * 35
OPERATIONS = [
    ("search", "search_videos", "search"),
    ("video", "get_video_details", "videos"),
    ("channel", "get_channel_info", "channels"),
]


def api_error():
    return HttpError(
        httplib2.Response({"status": "500", "reason": "Internal Server Error"}),
        b'{"error":{"message":"backend unavailable","code":500}}',
        uri=f"https://youtube.googleapis.com/youtube/v3/videos?key={SECRET}&part=snippet",
    )


def failing_service(monkeypatch, endpoint, error):
    service = Mock()
    getattr(service, endpoint).return_value.list.return_value.execute.side_effect = error
    monkeypatch.setattr(youtube_search, "_build_youtube_service", lambda key: service)


@pytest.mark.parametrize("command,function,endpoint", OPERATIONS)
def test_report_errors_redact_sdk_request_url(monkeypatch, command, function, endpoint):
    error = api_error()
    assert SECRET in str(error)  # Exercise the SDK's actual error representation.
    failing_service(monkeypatch, endpoint, error)
    result = getattr(youtube_search, function)("fixture", api_key=SECRET)
    assert SECRET not in json.dumps(result)
    assert "backend unavailable" in result["error"]
    assert "500" in result["error"]


@pytest.mark.parametrize("command,function,endpoint", OPERATIONS)
@pytest.mark.parametrize("json_output", [False, True])
def test_cli_stdout_and_stderr_redact_keys(monkeypatch, capsys, command, function, endpoint, json_output):
    failing_service(monkeypatch, endpoint, api_error())
    argv = ["youtube_search.py", command, "fixture", "--api-key", SECRET]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))
    if json_output:
        youtube_search.main()
    else:
        with pytest.raises(SystemExit) as exc:
            youtube_search.main()
        assert exc.value.code == 1
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert "backend unavailable" in output.err
    if json_output:
        assert json.loads(output.out)["error"]


@pytest.mark.parametrize("configured", [False, True])
def test_service_construction_errors_redact_keys(monkeypatch, capsys, configured):
    monkeypatch.setattr(youtube_search, "get_api_key", lambda: SECRET)
    monkeypatch.setattr(youtube_search, "build", Mock(side_effect=RuntimeError(f"bad key={SECRET}")))
    assert youtube_search._build_youtube_service(None if configured else SECRET) is None
    output = capsys.readouterr()
    assert SECRET not in output.err
    assert "Error building YouTube service" in output.err


@pytest.mark.parametrize("command,function,endpoint", OPERATIONS)
def test_non_secret_errors_remain_useful(monkeypatch, command, function, endpoint):
    failing_service(monkeypatch, endpoint, RuntimeError("backend unavailable"))
    result = getattr(youtube_search, function)("fixture", api_key=SECRET)
    assert result["error"] == "YouTube API error: backend unavailable"
