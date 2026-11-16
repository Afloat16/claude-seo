"""NLP reports identify the document sample actually used by both endpoints."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nlp_analyze as nlp  # noqa: E402


@pytest.fixture
def requests_sent(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

        def json(self):
            return {"entities": [
                {"name": name, "salience": 1.0}
                for name in ("IncludedEntity", "ExcludedEntity") if name in self.content
            ]}

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return Response(kwargs["json"]["document"]["content"])

    monkeypatch.setattr(nlp.requests, "post", post)
    return calls


@pytest.mark.parametrize("length", [60, 99_999, 100_000, 100_001])
def test_reported_sample_length_matches_every_request(requests_sent, length):
    text = "a" * length
    report = nlp.analyze_text(text, api_key="fixture-key")
    assert report["text_length"] == length
    assert report["analysis_text_length"] == min(length, 100_000)
    assert report["text_truncated"] is (length > 100_000)
    assert len(requests_sent) == 2
    assert all(body["document"]["content"] == text[:100_000] for _, body in requests_sent)


def test_unicode_sample_lengths_are_characters_not_encoded_bytes(requests_sent):
    report = nlp.analyze_text("界" * 100_001, features=["entities"], api_key="fixture-key")
    assert report["analysis_text_length"] == 100_000
    assert report["text_truncated"] is True
    assert len(requests_sent[0][1]["document"]["content"].encode("utf-8")) == 300_000


def test_url_entity_report_identifies_the_unexamined_tail(monkeypatch, requests_sent):
    class Page:
        def raise_for_status(self):
            pass

    text = "IncludedEntity " + "a" * 100_000 + " ExcludedEntity"
    monkeypatch.setattr(nlp, "validate_url", lambda _: True)
    monkeypatch.setattr(nlp, "safe_requests_get", lambda *a, **k: Page())
    monkeypatch.setattr(nlp, "decode_response_text", lambda _: f"<article>{text}</article>")
    report = nlp.analyze_url("https://example.com/long-article", features=["entities"], api_key="fixture-key")
    assert [entity["name"] for entity in report["entities"]] == ["IncludedEntity"]
    assert report["extracted_text_length"] == len(text)
    assert report["analysis_text_length"] == 100_000
    assert report["text_truncated"] is True


@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_exposes_truncation_without_changing_the_request_cap(monkeypatch, capsys, requests_sent, json_mode):
    text = "a" * 100_001
    argv = ["nlp_analyze.py", "--text", text, "--api-key", "fixture-key"]
    if json_mode:
        argv.append("--json")
    monkeypatch.setattr(sys, "argv", argv)
    nlp.main()
    captured = capsys.readouterr()
    assert "100,000 of 100,001" in captured.err
    assert "truncated" in captured.err.lower()
    if json_mode:
        payload = json.loads(captured.out)
        assert payload["text_length"] == 100_001
        assert payload["analysis_text_length"] == 100_000
        assert payload["text_truncated"] is True
    assert all(len(body["document"]["content"]) == 100_000 for _, body in requests_sent)


def test_short_cli_input_has_no_truncation_warning(monkeypatch, capsys, requests_sent):
    monkeypatch.setattr(sys, "argv", ["nlp_analyze.py", "--text", "Example text", "--api-key", "fixture-key"])
    nlp.main()
    assert "truncated" not in capsys.readouterr().err.lower()
