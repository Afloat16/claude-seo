"""Native Google Reviews fields must survive the reviews normalizer and CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dataforseo_normalize import normalize_reviews  # noqa: E402


def _native_review():
    # Field shape documented by business_data/google/reviews/task_get.
    return {
        "profile_name": "Example Reviewer",
        "review_text": "The order arrived on time.",
        "timestamp": "2026-11-01 12:30:00 +00:00",
        "time_ago": "two weeks ago",
        "rating": {"rating_type": "Max5", "value": 5, "votes_count": 7, "rating_max": 5},
    }


def test_native_review_keeps_rating_votes_and_absolute_date():
    review = normalize_reviews([_native_review()])[0]
    assert review["rating"] == 5.0
    assert review["helpful_count"] == 7
    assert review["date"] == "2026-11-01 12:30:00 +00:00"
    assert review["author"] == "Example Reviewer"
    assert review["text"] == "The order arrived on time."


@pytest.mark.parametrize("rating,expected", [(4, 4.0), ("3.5", 3.5), (0, 0.0), (None, 0.0)])
def test_scalar_rating_contract_is_preserved(rating, expected):
    review = normalize_reviews([{"rating": rating, "date": "2026-10-01", "likes": 2}])[0]
    assert review["rating"] == expected
    assert review["date"] == "2026-10-01"
    assert review["helpful_count"] == 2


@pytest.mark.parametrize("field", ["helpful_count", "likes"])
def test_explicit_zero_votes_and_legacy_date_take_precedence(field):
    item = {**_native_review(), field: 0, "date": "2026-10-01"}
    item["rating"]["value"] = 0
    review = normalize_reviews([item])[0]
    assert review["rating"] == 0.0
    assert review["helpful_count"] == 0
    assert review["date"] == "2026-10-01"


def test_missing_nested_values_keep_existing_defaults():
    review = normalize_reviews([{"rating": {}, "time_ago": "a week ago"}])[0]
    assert review["rating"] == 0.0
    assert review["helpful_count"] == 0
    assert review["date"] == "a week ago"


@pytest.mark.parametrize("table", [False, True])
def test_reviews_cli_consumes_the_native_api_envelope(table):
    payload = {"tasks": [{"status_code": 20000, "result": [{"items": [_native_review()]}]}]}
    command = [sys.executable, str(ROOT / "scripts/dataforseo_normalize.py"), "-", "--module", "reviews"]
    if table:
        command.append("--table")
    result = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    if table:
        assert "| Example Reviewer | 5 |" in result.stdout
        assert "2026-11-01 12:30:00 +00:00" in result.stdout
    else:
        review = json.loads(result.stdout)["items"][0]
        assert (review["rating"], review["helpful_count"], review["date"]) == (
            5.0, 7, "2026-11-01 12:30:00 +00:00",
        )
