"""Validate a report built from a live public page with no backlink measurements.

The page fetch, parser, report validator and CLI are production functions. No
provider API credentials are used, and this does not verify Moz/Bing/DataForSEO
measurements. Empty-provider-response regressions run separately as unit tests.
Keep this driver on the fork's validation branch, outside the contribution PR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

SAMPLE_URL = "https://www.python.org/"


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
        "source": "Live Python homepage; no measured backlink profile or provider API calls",
        "requested_url": SAMPLE_URL,
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match the expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import fetch_page
        import parse_html
        import runtime
        import validate_backlink_report

        for module in (fetch_page, parse_html, runtime, validate_backlink_report):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "validate_backlink_report.py", None)
        require(script == root / "scripts/validate_backlink_report.py", "Validator is not registered")

        page = fetch_page.fetch_page(SAMPLE_URL, timeout=20, max_time=30, public_only=True)
        report["fetch"] = {
            "final_url": page.get("url"),
            "status_code": page.get("status_code"),
            "error": page.get("error"),
            "content_bytes": page.get("content_bytes"),
            "fetch_duration_ms": page.get("fetch_duration_ms"),
        }
        require(page.get("error") is None, f"Live page fetch failed: {page.get('error')}")
        require(page.get("status_code") == 200, "Live page did not return HTTP 200")
        require(not page.get("too_large"), "Live page exceeded the body-size limit")
        require(urlparse(page.get("url", "")).hostname in {"www.python.org", "python.org"},
                "Unexpected final host")
        html = page.get("content")
        require(isinstance(html, str) and len(html) > 1000, "Unexpectedly short sample response")
        report["fetch"]["html_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
        parsed = parse_html.parse_html(html, page["url"])
        require("python" in str(parsed.get("title", "")).lower(), "Unexpected page title")
        input_report = {
            "parsed_data": parsed,
            "scoring_factors": {"score": None, "factors_with_data": 0},
            "findings": [{"title": "Backlink profile", "source": "not-assessed", "score": None}],
        }
        result = validate_backlink_report.validate_report(input_report)
        require(result["data"]["errors"] == 0, "Report without unmeasured scores did not validate")
        process = subprocess.run(
            [sys.executable, str(script), "--report", "-", "--json"],
            input=json.dumps(input_report), cwd=root, text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Validator CLI failed")
        require(json.loads(process.stdout) == result, "CLI and production API results disagree")
        report["validation"] = result
        report["backlink_score"] = None
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - fail and record every validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
