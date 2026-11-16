"""Report failures remain machine-detectable while preserving partial files."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import google_report as report  # noqa: E402


@pytest.mark.parametrize("output_format", ["pdf", "xlsx", "all"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_cli_failure_status_without_optional_renderers(tmp_path, output_format, json_mode):
    # -S disables site packages, reproducing a missing optional renderer in
    # an actual CLI subprocess without uninstalling anything from the runner.
    argv = [sys.executable, "-S", str(SCRIPTS / "google_report.py"),
            "--type", "indexation", "--domain", "example.com",
            "--format", output_format, "--output-dir", str(tmp_path)]
    if json_mode:
        argv.append("--json")
    result = subprocess.run(argv, input="{}", text=True, capture_output=True, check=False)
    assert result.returncode == 1
    assert "Error:" in result.stderr
    if json_mode:
        metadata = json.loads(result.stdout)
        assert metadata["error"]
        assert all(Path(path).is_file() for path in metadata["files"])
        if output_format == "all":
            assert {Path(path).suffix for path in metadata["files"]} == {".html"}
    elif output_format == "all":
        assert "Generated:" in result.stdout
        assert ".html" in result.stdout


def test_cli_html_success_still_exits_zero(tmp_path):
    result = subprocess.run(
        [sys.executable, "-S", str(SCRIPTS / "google_report.py"),
         "--type", "indexation", "--domain", "example.com", "--format", "html",
         "--output-dir", str(tmp_path), "--json"],
        input="{}", text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    metadata = json.loads(result.stdout)
    assert metadata["error"] is None
    assert len(metadata["files"]) == 1
    assert Path(metadata["files"][0]).is_file()


@pytest.mark.parametrize("pdf_fails", [False, True])
def test_all_reports_missing_workbook_alongside_other_outputs(tmp_path, monkeypatch, pdf_fails):
    class PDF:
        def __init__(self, **_kwargs):
            pass

        def write_pdf(self, path):
            Path(path).write_bytes(b"controlled PDF")

    monkeypatch.setattr(report, "HTML", None if pdf_fails else PDF)
    monkeypatch.setattr(report, "_review_pdf", lambda *_: {"status": "PASS"})
    monkeypatch.setattr(report, "generate_xlsx", lambda *_: None)
    result = report.generate_report("indexation", {}, "example.com", tmp_path, "all")
    assert "openpyxl" in result["error"]
    expected = {".html"} if pdf_fails else {".html", ".pdf"}
    assert {Path(path).suffix for path in result["files"]} == expected
    if pdf_fails:
        assert "weasyprint" in result["error"]
