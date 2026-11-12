"""A failed statistics enrichment must not discard successful search results."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import youtube_search  # noqa: E402


@pytest.fixture
def service(monkeypatch):
    service = MagicMock()
    service.search.return_value.list.return_value.execute.return_value = {
        "pageInfo": {"totalResults": 20},
        "items": [{"id": {"videoId": "video-one"}, "snippet": {
            "title": "Found video", "channelTitle": "Author", "description": "Useful description",
        }}],
    }
    monkeypatch.setattr(youtube_search, "_build_youtube_service", lambda key: service)
    return service


def http_error():
    return HttpError(
        httplib2.Response({"status": "503"}),
        json.dumps({"error": {"message": "Backend unavailable"}}).encode(),
        uri="https://www.googleapis.com/youtube/v3/videos?key=synthetic-key",
    )


@pytest.mark.parametrize("error", [TimeoutError("timed out"), http_error()])
def test_statistics_failure_preserves_search_snippets(service, error):
    service.videos.return_value.list.return_value.execute.side_effect = error
    result = youtube_search.search_videos("query")
    assert result["error"] is None
    assert result["statistics_error"]
    assert "synthetic-key" not in result["statistics_error"]
    assert result["total_results"] == 20
    assert len(result["videos"]) == 1
    video = result["videos"][0]
    assert video["video_id"] == "video-one"
    assert video["title"] == "Found video"
    assert video["url"] == "https://www.youtube.com/watch?v=video-one"
    assert video["views"] is video["likes"] is video["comments"] is None


def test_omitted_video_is_not_reported_as_zero_views(service):
    service.videos.return_value.list.return_value.execute.return_value = {"items": []}
    result = youtube_search.search_videos("query")
    assert result["error"] is None
    assert result["videos"][0]["views"] is None


def test_missing_count_is_distinct_from_explicit_zero(service):
    service.videos.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "video-one", "statistics": {"viewCount": "0"}}]
    }
    video = youtube_search.search_videos("query")["videos"][0]
    assert video["views"] == 0
    assert video["likes"] is None
    assert video["comments"] is None


def test_text_output_displays_partial_results_with_warning(service, monkeypatch, capsys):
    service.videos.return_value.list.return_value.execute.side_effect = TimeoutError("timed out")
    monkeypatch.setattr(sys, "argv", ["youtube_search.py", "search", "query"])
    youtube_search.main()
    captured = capsys.readouterr()
    assert "Found video" in captured.out
    assert "unavailable views" in captured.out
    assert "Warning:" in captured.err
    assert "timed out" in captured.err


def test_successful_statistics_remain_numeric(service):
    service.videos.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "video-one", "statistics": {
            "viewCount": "1000", "likeCount": "0", "commentCount": "2",
        }, "contentDetails": {"duration": "PT2M"}}]
    }
    result = youtube_search.search_videos("query")
    video = result["videos"][0]
    assert result["error"] is None
    assert (video["views"], video["likes"], video["comments"]) == (1000, 0, 2)
    assert video["duration"] == "PT2M"


def test_empty_search_does_not_request_statistics(service):
    service.search.return_value.list.return_value.execute.return_value = {"items": []}
    result = youtube_search.search_videos("query")
    assert result["error"] is None
    assert result["videos"] == []
    service.videos.assert_not_called()


def test_search_failure_remains_an_error(service):
    service.search.return_value.list.return_value.execute.side_effect = TimeoutError("search failed")
    result = youtube_search.search_videos("query")
    assert "search failed" in result["error"]
    assert result["videos"] == []
    service.videos.assert_not_called()
