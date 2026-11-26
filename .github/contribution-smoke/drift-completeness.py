"""Exercise complete live drift snapshots and their JSON fetch handoff.

Size-capped response rejection is covered by the regression suite. This
driver fetches an actual public documentation page without changing its
HTML, confirms the full output file survives JSON preview truncation,
and stores/compares real snapshots in an isolated temporary database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

SAMPLE_URL = "https://docs.python.org/3/"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    report = {
        "expected_sha": args.expected_sha,
        "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(),
        "requested_url": SAMPLE_URL,
        "scope": "Real complete page, production fetch/parse CLI handoff, isolated baseline and comparison database",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import drift_baseline
        import drift_compare
        import parse_html
        import runtime

        for module in (drift_baseline, drift_compare, parse_html, runtime):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "fetch_page.py", None)
        require(script == root / "scripts/fetch_page.py", "Fetcher is not registered")
        require(drift_compare.fetch_page_data is drift_baseline.fetch_page_data,
                "Comparison does not use the reviewed shared snapshot handoff")
        with tempfile.TemporaryDirectory(prefix="drift-live-smoke-") as temporary:
            work = Path(temporary)
            saved = work / "page.html"
            process = subprocess.run(
                [sys.executable, str(script), SAMPLE_URL, "--output", str(saved), "--json", "--max-text", "1",
                 "--timeout", "20", "--max-time", "30"],
                cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=45,
            )
            metadata = json.loads(process.stdout)
            report["cli"] = {"returncode": process.returncode, "stderr": process.stderr,
                             "fetch_error": metadata.get("error")}
            require(process.returncode == 0, f"Actual fetch CLI failed: {metadata.get('error')}")
            require(metadata.get("status_code") == 200 and not metadata.get("error"), "Live response not HTTP 200")
            require(urlparse(metadata.get("url") or "").hostname == "docs.python.org", "Unexpected final host")
            require(metadata.get("too_large") is False and metadata.get("output_written") is True,
                    "Fetcher did not confirm a complete saved response")
            require(len(metadata.get("content") or "") == 1, "Expected the requested one-character JSON preview")
            html = saved.read_text(encoding="utf-8")
            require(len(html) > 1000, "Saved document was truncated with the JSON preview")
            parsed = parse_html.parse_html(html, metadata["url"])
            require(bool(parsed.get("title")) and "Python" in parsed["title"], "Actual document title missing")
            report["fetch"] = {
                "final_url": metadata["url"], "status": metadata["status_code"],
                "too_large": metadata["too_large"], "output_written": metadata["output_written"],
                "preview_characters": len(metadata["content"]), "saved_characters": len(html),
                "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(), "title": parsed["title"],
            }
            old_dir, old_path = drift_baseline.DB_DIR, drift_baseline.DB_PATH
            try:
                drift_baseline.DB_DIR = str(work / "db")
                drift_baseline.DB_PATH = str(work / "db/baselines.db")
                baseline = drift_baseline.capture_baseline(SAMPLE_URL, skip_cwv=True)
                report["baseline"] = baseline
                require(baseline.get("status") == "ok" and not baseline.get("error"), "Live baseline capture failed")
                require(baseline["summary"]["status_code"] == 200, "Baseline lost actual HTTP status")
                require(baseline["summary"]["title"] == parsed["title"], "Baseline lost actual document title")
                comparison = drift_compare.run_comparison(SAMPLE_URL, skip_cwv=True)
                report["comparison"] = comparison
                require(comparison.get("status") == "ok" and not comparison.get("error"), "Live comparison failed")
                require(comparison["current_status_code"] == 200 and comparison["summary"]["critical"] == 0,
                        "Complete live snapshot unexpectedly reported a critical change")
                conn = drift_baseline.init_db()
                try:
                    counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                              for table in ("baselines", "comparisons")}
                finally:
                    conn.close()
                require(counts == {"baselines": 1, "comparisons": 1}, "Unexpected live snapshot persistence counts")
                report["stored_records"] = counts
            finally:
                drift_baseline.DB_DIR, drift_baseline.DB_PATH = old_dir, old_path
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail every validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
