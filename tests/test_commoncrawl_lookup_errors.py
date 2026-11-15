"""An incomplete graph read cannot establish that a domain is absent."""

import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import commoncrawl_graph as ccg  # noqa: E402

RELEASE = "cc-main-2026-jan-feb-mar"
OTHER = b"1\t0.9\t1\t0.1\torg.other\t2\n"
TARGET = b"2\t0.8\t3\t0.05\tcom.example\t4\n"


class Response:
    def __init__(self, data=b"", status=200, interrupted=None):
        self.data = data
        self.status_code = status
        self.interrupted = interrupted
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ccg.requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        if isinstance(self.data, list):
            yield from self.data
        else:
            yield self.data
        if self.interrupted:
            raise self.interrupted

    def close(self):
        self.closed = True


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ccg, "get_cache_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.mark.parametrize("failure", ["404", "503", "timeout", "corrupt", "truncated", "budget"])
def test_failed_graph_read_does_not_claim_absence_or_write_cache(cache_dir, monkeypatch, failure):
    data = gzip.compress(OTHER)
    response = Response(data)
    if failure in {"404", "503"}:
        response.status_code = int(failure)
    elif failure == "timeout":
        response = Response(data[:10], interrupted=ccg.requests.Timeout("stream stalled"))
    elif failure == "corrupt":
        response.data = b"not a gzip archive"
    elif failure == "truncated":
        response.data = data[:-5]
    elif failure == "budget":
        monkeypatch.setattr(ccg, "MAX_GRAPH_DOWNLOAD_BYTES", len(data) - 1, raising=False)
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)

    report = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["status"] == "error"
    assert report["data"] is None
    assert report["error"]
    assert list(cache_dir.glob("*.json")) == []
    assert response.closed


def test_failed_vertices_lookup_does_not_turn_missing_rank_into_absence(cache_dir, monkeypatch):
    responses = [Response(gzip.compress(OTHER)), Response(status=503)]
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return responses[len(requests) - 1]

    monkeypatch.setattr(ccg.requests, "get", get)

    report = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["status"] == "error"
    assert report["data"] is None
    assert "vertices" in report["error"]
    assert len(requests) == 2
    assert list(cache_dir.glob("*.json")) == []


@pytest.mark.parametrize("data", [b"", OTHER])
def test_complete_no_match_is_cached_and_reused(cache_dir, monkeypatch, data):
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return Response(gzip.compress(data))

    monkeypatch.setattr(ccg.requests, "get", get)

    report = ccg.get_domain_metrics("example.com", release=RELEASE)
    cached = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["status"] == "success"
    assert report["data"]["in_crawl"] is False
    assert cached["metadata"]["from_cache"] is True
    assert len(requests) == 2


@pytest.mark.parametrize("score", ["0.05", "0.0"])
def test_exact_rank_match_finishes_without_reading_the_remaining_archive(cache_dir, monkeypatch, score):
    lookalike = b"1\t0.9\t1\t0.1\torg.com.example\t2\n"
    target = f"2\t0.8\t3\t{score}\tcom.example\t4\n".encode()
    response = Response(
        gzip.compress(lookalike + target),
        interrupted=RuntimeError("the remainder must not be consumed after an exact match"),
    )
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return response

    monkeypatch.setattr(ccg.requests, "get", get)

    report = ccg.get_domain_metrics("example.com", release=RELEASE)

    cached = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["status"] == "success"
    assert report["data"]["in_rankings"] is True
    assert report["data"]["pagerank"] == float(score)
    assert report["data"]["pagerank_rank"] == 3
    assert report["data"]["n_hosts"] == 4
    assert len(requests) == 1
    assert cached["metadata"]["from_cache"] is True
    assert response.closed
    assert len(list(cache_dir.glob("*.json"))) == 1


@pytest.mark.parametrize("separate_chunks", [False, True])
def test_rank_in_later_gzip_member_is_not_reported_absent(cache_dir, monkeypatch, separate_chunks):
    members = [gzip.compress(OTHER), gzip.compress(TARGET)]
    response = Response(members if separate_chunks else b"".join(members))
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)

    report = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["data"]["in_rankings"] is True
    assert report["data"]["pagerank_rank"] == 3


def test_final_record_without_newline_is_not_lost(cache_dir, monkeypatch):
    response = Response(gzip.compress(TARGET.rstrip(b"\n")))
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)

    report = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["data"]["in_rankings"] is True


def test_legacy_negative_cache_is_rechecked(cache_dir, monkeypatch):
    old = {
        "status": "success",
        "data": {"domain": "example.com", "in_crawl": False, "in_rankings": False},
        "error": None,
        "metadata": {"source": "commoncrawl", "release": RELEASE},
    }
    ccg._save_cache("example.com", RELEASE, old)
    cache_path = next(cache_dir.glob("*.json"))
    prior = cache_path.read_bytes()
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: Response(status=503))

    report = ccg.get_domain_metrics("example.com", release=RELEASE)

    assert report["status"] == "error"
    assert report["data"] is None
    assert cache_path.read_bytes() == prior


@pytest.mark.parametrize("json_output", [False, True])
def test_failed_cli_lookup_exits_nonzero(cache_dir, monkeypatch, capsys, json_output):
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: Response(status=503))
    args = ["commoncrawl_graph.py", "example.com", "--release", RELEASE]
    monkeypatch.setattr(sys, "argv", args + (["--json"] if json_output else []))

    with pytest.raises(SystemExit) as exit_info:
        ccg.main()

    assert exit_info.value.code == 1
    output = capsys.readouterr()
    if json_output:
        assert json.loads(output.out)["status"] == "error"
    else:
        assert "HTTP 503" in output.err
