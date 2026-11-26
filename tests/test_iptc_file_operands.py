"""Image operands must not become directory batches or ExifTool options."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import iptc_ai_label  # noqa: E402

SOURCE_TYPE = "digitalCapture"
URI = iptc_ai_label.IPTC_VOCAB[SOURCE_TYPE]
MARKER = b"\nsource-type:"


@pytest.fixture
def exiftool_boundary(monkeypatch):
    """Model ExifTool's option and directory operand rules, not image encoding."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        operands = [arg for arg in argv[1:] if not arg.startswith("-")]
        if len(operands) != 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="No file specified")
        operand = Path(operands[0])
        files = list(operand.iterdir()) if operand.is_dir() else [operand]
        if "=" in argv[1]:
            value = argv[1].split("=", 1)[1].encode()
            for file in files:
                file.write_bytes(file.read_bytes().split(MARKER)[0] + MARKER + value)
            return SimpleNamespace(returncode=0, stdout="updated", stderr="")
        content = files[0].read_bytes()
        value = content.split(MARKER, 1)[1].decode() if MARKER in content else ""
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    monkeypatch.setattr(iptc_ai_label, "exiftool_available", lambda: True)
    monkeypatch.setattr(iptc_ai_label.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("directory", [".", "images", "image.jpg"])
def test_inject_rejects_directory_without_mutating_any_child(tmp_path, monkeypatch,
                                                          exiftool_boundary, directory):
    monkeypatch.chdir(tmp_path)
    target = Path(directory)
    target.mkdir(exist_ok=True)
    for name in ("first.jpg", "second.png"):
        (target / name).write_bytes(b"original image bytes")
    before = {p: p.read_bytes() for p in target.iterdir()}
    result = iptc_ai_label.inject(target, SOURCE_TYPE)
    assert result["ok"] is False
    assert result["error"] == "image-not-a-file"
    assert {p: p.read_bytes() for p in target.iterdir()} == before
    assert exiftool_boundary == []


@pytest.mark.parametrize("name", ["-photo.jpg", "--photo.png"])
def test_single_image_injection_treats_leading_dash_as_filename(tmp_path, monkeypatch,
                                                              exiftool_boundary, name):
    monkeypatch.chdir(tmp_path)
    image = Path("./" + name)  # Path discards the protective './'.
    image.write_bytes(b"original")
    sibling = Path("other.jpg")
    sibling.write_bytes(b"untouched")
    result = iptc_ai_label.inject(image, SOURCE_TYPE)
    assert result["ok"] is True
    assert result["image"] == name
    assert image.read_bytes() == b"original" + MARKER + URI.encode()
    assert sibling.read_bytes() == b"untouched"
    assert len(exiftool_boundary) == 1


@pytest.mark.parametrize("name", ["-photo.jpg", "--photo.png"])
def test_audit_reads_leading_dash_file_instead_of_reporting_a_missing_tag(tmp_path, monkeypatch,
                                                                      exiftool_boundary, name):
    monkeypatch.chdir(tmp_path)
    image = Path("./" + name)
    image.write_bytes(b"original" + MARKER + URI.encode())
    result = iptc_ai_label.audit(image)
    assert result["summary"][SOURCE_TYPE] == 1
    assert result["summary"]["missing"] == 0
    assert result["images"][0]["source_type"] == URI
    assert result["images"][0]["path"] == name
    assert len(exiftool_boundary) == 1


def test_directory_audit_remains_supported_with_option_like_directory_name(tmp_path, monkeypatch,
                                                                        exiftool_boundary):
    monkeypatch.chdir(tmp_path)
    directory = Path("-images")
    directory.mkdir()
    (directory / "known.jpg").write_bytes(b"original" + MARKER + URI.encode())
    (directory / "missing.png").write_bytes(b"original")
    before = {p: p.read_bytes() for p in directory.iterdir()}
    result = iptc_ai_label.audit(directory)
    assert result["summary"]["total"] == 2
    assert result["summary"][SOURCE_TYPE] == 1
    assert result["summary"]["missing"] == 1
    assert {p: p.read_bytes() for p in directory.iterdir()} == before
    assert len(exiftool_boundary) == 2


@pytest.mark.parametrize("absolute", [False, True])
def test_regular_single_image_roundtrip_preserves_caller_path(tmp_path, monkeypatch,
                                                            exiftool_boundary, absolute):
    monkeypatch.chdir(tmp_path)
    image = (tmp_path if absolute else Path()) / "image with spaces.png"
    image.write_bytes(b"original")
    result = iptc_ai_label.inject(image, SOURCE_TYPE)
    assert result["ok"] is True
    assert result["image"] == str(image)
    assert iptc_ai_label._read_source_type(image) == URI
    assert len(exiftool_boundary) == 2


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_directory_injection_fails_without_modifying_images(tmp_path, monkeypatch,
                                                              exiftool_boundary, as_json, capsys):
    image = tmp_path / "sample.jpg"
    image.write_bytes(b"original")
    args = ["iptc_ai_label.py", "inject", str(tmp_path), *(["--json"] if as_json else [])]
    with patch.object(sys, "argv", args), pytest.raises(SystemExit) as caught:
        iptc_ai_label._cli()
    assert caught.value.code == 2
    output = capsys.readouterr()
    if as_json:
        assert json.loads(output.out)["error"] == "image-not-a-file"
    else:
        assert "image-not-a-file" in output.err
    assert image.read_bytes() == b"original"
    assert exiftool_boundary == []


@pytest.mark.parametrize("command", ["audit", "inject"])
def test_cli_accepts_explicit_relative_option_like_filename(tmp_path, monkeypatch,
                                                           exiftool_boundary, command, capsys):
    monkeypatch.chdir(tmp_path)
    Path("-sample.jpg").write_bytes(b"original" + MARKER + URI.encode())
    args = ["iptc_ai_label.py", command, "./-sample.jpg", "--json"]
    if command == "inject":
        args += ["--source-type", SOURCE_TYPE]
    with patch.object(sys, "argv", args), pytest.raises(SystemExit) as caught:
        iptc_ai_label._cli()
    assert caught.value.code == 0
    result = json.loads(capsys.readouterr().out)
    if command == "audit":
        assert result["images"][0]["source_type"] == URI
    else:
        assert result["ok"] is True


def test_missing_image_keeps_existing_error_and_never_runs_exiftool(tmp_path, exiftool_boundary):
    result = iptc_ai_label.inject(tmp_path / "missing.jpg", SOURCE_TYPE)
    assert result["error"] == "image-not-found"
    assert result["ok"] is False
    assert exiftool_boundary == []


def _symlink(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("Creating symbolic links is not supported in this environment")


def test_symlink_to_directory_cannot_turn_injection_into_a_batch(tmp_path, exiftool_boundary):
    directory = tmp_path / "images"
    directory.mkdir()
    image = directory / "original.png"
    image.write_bytes(b"original")
    link = tmp_path / "link.png"
    _symlink(link, directory, directory=True)
    result = iptc_ai_label.inject(link, SOURCE_TYPE)
    assert result["error"] == "image-not-a-file"
    assert result["ok"] is False
    assert image.read_bytes() == b"original"
    assert exiftool_boundary == []


def test_symlink_to_file_is_passed_to_exiftool_without_resolving_it(tmp_path, monkeypatch,
                                                                 exiftool_boundary):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target.png"
    target.write_bytes(b"original")
    link = Path("link.png")
    _symlink(link, target)
    result = iptc_ai_label.inject(link, SOURCE_TYPE)
    assert result["ok"] is True
    assert result["image"] == "link.png"
    assert len(exiftool_boundary) == 1
    operand = Path(exiftool_boundary[0][-1])
    assert operand.name == "link.png" and operand.is_symlink()


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_single_file_write_failure_is_not_converted_to_success(tmp_path, monkeypatch, failure):
    image = tmp_path / "sample.png"
    image.write_bytes(b"original")
    monkeypatch.setattr(iptc_ai_label, "exiftool_available", lambda: True)
    kwargs = {"side_effect": subprocess.TimeoutExpired("exiftool", 30)} if failure == "timeout" else {
        "return_value": SimpleNamespace(returncode=1, stdout="", stderr="unsupported image"),
    }
    with patch.object(iptc_ai_label.subprocess, "run", **kwargs):
        result = iptc_ai_label.inject(image, SOURCE_TYPE)
    assert result["ok"] is False
    assert result["error"] == ("exiftool-timeout" if failure == "timeout" else "unsupported image")
    assert image.read_bytes() == b"original"
