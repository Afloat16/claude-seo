"""A failed or partial graph scan cannot establish that a domain is absent."""

import gzip
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import commoncrawl_graph as ccg  # noqa: E402


class StreamResponse:
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield from self.chunks
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


@pytest.mark.parametrize("stage", ["rankings", "vertices"])
@pytest.mark.parametrize("error", [
    ccg.requests.exceptions.Timeout("timed out"),
    ccg.requests.exceptions.ConnectionError("connection lost"),
    RuntimeError("incomplete gzip stream"),
])
def test_incomplete_lookups_are_errors_and_never_cached(monkeypatch, stage, error):
    saved = []
    monkeypatch.setattr(ccg, "_is_cached", lambda *args: None)
    monkeypatch.setattr(ccg, "_save_cache", lambda *args: saved.append(args))

    def lookup(url, *args, **kwargs):
        if stage == "rankings" or url.endswith(ccg.VERTICES_SUFFIX):
            raise error
        return []

    monkeypatch.setattr(ccg, "_stream_gz_chunked", lookup)
    result = ccg.get_domain_metrics("example.com", release="test-release")

    assert result["status"] == "error"
    assert result["data"] is None
    assert result["error"]
    assert saved == []


@pytest.mark.parametrize("chunks, error", [
    ([gzip.compress(b"1\tcom.other\n")[:-5]], None),
    ([b"not a gzip stream"], None),
    ([], ccg.requests.exceptions.Timeout("timed out")),
])
def test_partial_or_invalid_stream_raises_and_closes(monkeypatch, chunks, error):
    response = StreamResponse(chunks, error)
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)

    with pytest.raises(RuntimeError):
        ccg._stream_gz_chunked("https://data.commoncrawl.org/test.gz", "com.example")
    assert response.closed


@pytest.mark.parametrize("split_bytes", [False, True])
def test_stream_matches_exact_domain_and_final_line_across_gzip_members(monkeypatch, split_bytes):
    first = gzip.compress(b"1\tother.com.example\n")
    second = gzip.compress("2\tcom.exämple".encode())
    # Byte-sized chunks split both gzip headers and UTF-8 characters.
    compressed = first + b"\x00\x00" + second + b"\x00"
    response = StreamResponse([bytes([byte]) for byte in compressed] if split_bytes else [compressed])
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)

    result = ccg._stream_gz_chunked("https://data.commoncrawl.org/test.gz", "com.exämple")

    assert result == [["2", "com.exämple"]]
    assert response.closed


def test_compressed_limit_cannot_report_absence(monkeypatch):
    response = StreamResponse([gzip.compress(b"1\tcom.other\n")])
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(ccg, "MAX_GRAPH_COMPRESSED_BYTES", 1)
    with pytest.raises(RuntimeError, match="download limit"):
        ccg._stream_gz_chunked("https://data.commoncrawl.org/test.gz", "com.example")
    assert response.closed


def test_other_reversed_domains_are_not_matches(monkeypatch):
    response = StreamResponse([gzip.compress(b"1\tother.com.example\n")])
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)
    assert ccg._stream_gz_chunked("https://data.commoncrawl.org/test.gz", "com.example") == []
    assert response.closed


def test_zero_pagerank_is_present_and_stops_after_its_record(monkeypatch):
    response = StreamResponse(
        [gzip.compress(b"1\t0.0\t2\t0.0\tcom.example\t3\n")],
        ccg.requests.exceptions.Timeout("unneeded remainder"),
    )
    saved = []
    monkeypatch.setattr(ccg.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(ccg, "_is_cached", lambda *args: None)
    monkeypatch.setattr(ccg, "_save_cache", lambda *args: saved.append(args))

    result = ccg.get_domain_metrics("example.com", release="test-release")

    assert result["status"] == "success"
    assert result["data"]["in_rankings"] is True
    assert result["data"]["in_crawl"] is True
    assert result["data"]["pagerank"] == 0.0
    assert len(saved) == 1
    assert response.closed


def test_completed_absence_is_still_cacheable(monkeypatch):
    saved = []
    monkeypatch.setattr(ccg, "_is_cached", lambda *args: None)
    monkeypatch.setattr(ccg, "_stream_gz_chunked", lambda *args, **kwargs: [])
    monkeypatch.setattr(ccg, "_save_cache", lambda *args: saved.append(args))

    result = ccg.get_domain_metrics("example.com", release="test-release")

    assert result["status"] == "success"
    assert result["data"]["in_crawl"] is False
    assert len(saved) == 1
