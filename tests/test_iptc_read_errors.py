"""A failed metadata read must remain distinct from a missing IPTC label."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import iptc_ai_label  # noqa: E402


def _images_with_failed_read(tmp_path, monkeypatch, failure):
    for name in ("broken.jpg", "tagged.jpg", "unlabeled.jpg"):
        (tmp_path / name).write_bytes(b"image fixture")
    monkeypatch.setattr(iptc_ai_label, "exiftool_available", lambda: True)

    def read(args, **kwargs):
        name = Path(args[-1]).name
        if name == "broken.jpg":
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            if failure == "missing-binary":
                raise FileNotFoundError("exiftool disappeared")
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="Error: invalid image")
        value = iptc_ai_label.IPTC_VOCAB["digitalCapture"] if name == "tagged.jpg" else ""
        return subprocess.CompletedProcess(args, 0, stdout=value, stderr="")

    monkeypatch.setattr(iptc_ai_label.subprocess, "run", read)


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing-binary"])
def test_audit_keeps_read_errors_out_of_missing_labels(tmp_path, monkeypatch, failure):
    _images_with_failed_read(tmp_path, monkeypatch, failure)

    report = iptc_ai_label.audit(tmp_path)

    assert report["summary"]["missing"] == 1
    assert report["summary"]["digitalCapture"] == 1
    assert report["summary"]["read_errors"] == 1
    assert report["summary"]["total"] == 3
    broken = report["images"][0]
    assert broken["label"] == "unreadable"
    assert broken["source_type"] is None
    assert broken["error"]


@pytest.mark.parametrize("json_output", [False, True])
def test_partial_audit_cli_exits_nonzero(tmp_path, monkeypatch, capsys, json_output):
    _images_with_failed_read(tmp_path, monkeypatch, "exit")
    argv = ["iptc_ai_label.py", "audit", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv + (["--json"] if json_output else []))

    with pytest.raises(SystemExit) as exit_info:
        iptc_ai_label._cli()

    assert exit_info.value.code == 2
    output = capsys.readouterr().out
    if json_output:
        report = json.loads(output)
        assert report["summary"]["read_errors"] == 1
        assert report["summary"]["digitalCapture"] == 1
    else:
        assert "read_errors: 1" in output


def test_unknown_metadata_value_does_not_count_as_a_read_error(tmp_path, monkeypatch, capsys):
    image = tmp_path / "unknown.jpg"
    image.write_bytes(b"image fixture")
    monkeypatch.setattr(iptc_ai_label, "exiftool_available", lambda: True)
    monkeypatch.setattr(
        iptc_ai_label.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="https://example.com/read_errors", stderr=""
        ),
    )
    monkeypatch.setattr(sys, "argv", ["iptc_ai_label.py", "audit", str(image), "--json"])

    with pytest.raises(SystemExit) as exit_info:
        iptc_ai_label._cli()

    assert exit_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["other"] == 1
    assert report["summary"].get("read_errors", 0) == 0
    assert report["images"][0]["label"] == "other"
