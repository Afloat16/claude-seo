"""Preserve complete RGI flag emoji while still stripping unrelated tag text."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "content_humanize.py"
sys.path.insert(0, str(SCRIPT.parent))
from content_humanize import humanize, strip_invisible  # noqa: E402


def tagged(text):
    return "".join(chr(0xE0000 + ord(char)) for char in text)


def flag(subdivision):
    return "\U0001f3f4" + tagged(subdivision) + "\U000e007f"


@pytest.mark.parametrize("subdivision", ["gbeng", "gbsct", "gbwls"])
def test_rgi_flag_is_preserved_without_false_removal_counts(subdivision):
    text = f"Home team {flag(subdivision)} wins."
    assert strip_invisible(text) == (text, {})


def test_adjacent_flags_and_other_emoji_survive_together():
    text = flag("gbeng") + flag("gbsct") + flag("gbwls") + "⚽️"
    assert strip_invisible(text) == (text, {})


@pytest.mark.parametrize(
    "text,expected,count",
    [
        (tagged("gbeng") + "\U000e007f", "", 6),
        ("\U0001f3f4" + tagged("gbeng"), "\U0001f3f4", 5),
        (flag("hello"), "\U0001f3f4", 6),
        ("\U0001f3f4" + tagged("gbengx") + "\U000e007f", "\U0001f3f4", 7),
    ],
)
def test_unrecognized_or_incomplete_tag_text_is_still_removed(text, expected, count):
    assert strip_invisible(text) == (expected, {"unicode-tag-character": count})


def test_valid_flag_does_not_preserve_following_hidden_text():
    text = flag("gbwls") + tagged("hidden") + "\u200b"
    assert strip_invisible(text) == (
        flag("gbwls"),
        {"unicode-tag-character": 6, "zero-width-space": 1},
    )


def test_humanize_keeps_the_flag_and_counts_only_actual_cleanup():
    result = humanize(f"Home {flag('gbsct')} team\u200b wins.")
    assert result["cleaned"] == f"Home {flag('gbsct')} team wins."
    assert result["invisible_count"] == 1
    assert result["invisible_removed"] == {"zero-width-space": 1}
    assert result["change_count"] == 0


@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_preserves_utf8_flag_in_json_and_output_files(tmp_path, json_mode):
    source = tmp_path / "draft.txt"
    destination = tmp_path / "cleaned.txt"
    text = f"Home {flag('gbeng')} team wins."
    source.write_text(text, encoding="utf-8")
    args = [sys.executable, str(SCRIPT), str(source)]
    args.extend(["--json"] if json_mode else ["-o", str(destination)])
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=True)
    if json_mode:
        result = json.loads(completed.stdout)
        assert result["cleaned"] == text
        assert result["invisible_count"] == 0
    else:
        assert destination.read_text(encoding="utf-8") == text
        assert "invisible characters removed" not in completed.stderr
