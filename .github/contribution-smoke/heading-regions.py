"""Check actual public documentation headings through production API and CLI."""

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

SAMPLE_URL = "https://docs.python.org/3/tutorial/"


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
        "expected_sha": args.expected_sha, "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(), "requested_url": SAMPLE_URL,
        "scope": "Actual unmodified documentation HTML through production fetch, page-check API and URL CLI",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match expected SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))
        import fetch_page
        import page_checks
        import runtime

        for module in (fetch_page, page_checks, runtime):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "page_checks.py", None)
        require(script == root / "scripts/page_checks.py", "Page checker is not registered")
        fetched = fetch_page.fetch_page(SAMPLE_URL, timeout=20, max_time=30, public_only=True)
        report["fetch"] = {"status": fetched.get("status_code"), "error": fetched.get("error"),
                           "final_url": fetched.get("url")}
        require(not fetched.get("error") and fetched.get("status_code") == 200, "Actual HTML fetch failed")
        require(urlparse(fetched.get("url") or "").hostname == "docs.python.org", "Unexpected final host")
        require(fetched.get("too_large") is False, "Actual page was not completely read")
        html = fetched.get("content")
        require(isinstance(html, str) and len(html) > 1000, "Unexpected public document")
        result = page_checks.run_checks(html, fetched["url"], fetched.get("headers") or {},
                                       html_bytes=fetched.get("content_bytes"))
        facts = result["facts"]
        require("Python" in facts["title"] and facts["h1_count"] >= 1, "Actual tutorial heading missing")
        keys = ("h1_count", "h1", "heading_level_skips", "template_heading_level_skips")
        report["fetch"].update(characters=len(html), title=facts["title"],
                               html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest())
        report["heading_facts"] = {key: facts[key] for key in keys}
        report["heading_findings"] = [item for item in result["findings"] if item["id"] == "heading-level-skip"]
        process = subprocess.run([sys.executable, str(script), "--url", SAMPLE_URL, "--timeout", "20", "--json"],
                                 cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=60)
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        cli = json.loads(process.stdout)
        require(process.returncode == 0 and not cli.get("error"), f"Live page-check CLI failed: {cli.get('error')}")
        require(cli.get("source") == "url" and cli.get("status_code") == 200, "CLI did not fetch the actual URL")
        actual = {key: cli["facts"][key] for key in keys}
        require(actual == report["heading_facts"], "Production API and CLI heading facts disagree")
        report["cli"]["heading_facts"] = actual
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail every validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
