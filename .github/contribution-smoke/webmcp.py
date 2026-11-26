"""Draft WebMCP tools from a real public page without submitting any site form.

Actual DOM submission behavior is covered separately by controlled Chromium
fixtures. This driver validates production fetching, drafting and the JSON CLI
against the live Python homepage; it does not submit or mutate that website.
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
        "requested_url": SAMPLE_URL,
        "scope": "Draft from actual public forms; no live form submission or deployment",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match the expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import agentic_check
        import agentic_fix
        import runtime

        for module in (agentic_check, agentic_fix, runtime):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "agentic_fix.py", None)
        require(script == root / "scripts/agentic_fix.py", "Drafter is not registered")
        page = agentic_check.fetch(SAMPLE_URL)
        report["fetch"] = {
            "final_url": page.get("final_url"), "status": page.get("status"),
            "error": page.get("error"),
        }
        require(page.get("error") is None, f"Live page fetch failed: {page.get('error')}")
        require(page.get("status") == 200, "Live page did not return HTTP 200")
        require(urlparse(page.get("final_url") or SAMPLE_URL).hostname in {"www.python.org", "python.org"},
                "Unexpected final host")
        html = page.get("text")
        require(isinstance(html, str) and len(html) > 1000, "Unexpectedly short sample response")
        report["fetch"].update(characters=len(html), html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest())
        drafts = agentic_fix.draft_webmcp(page.get("final_url") or SAMPLE_URL, html)
        require(bool(drafts), "Live page has no forms to draft")
        require(any("q" in draft["declarative_attributes"]["fields"] for draft in drafts),
                "Python search form was not found in the live page")
        require(all("form.requestSubmit()" in draft["imperative_js"] for draft in drafts),
                "Draft no longer uses the page's form submission path")
        process = subprocess.run(
            [sys.executable, str(script), "webmcp", SAMPLE_URL, "--json"],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=45,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Live drafting CLI failed")
        cli_result = json.loads(process.stdout)
        require(cli_result.get("tools") == drafts, "CLI and production API drafts disagree")
        report["drafts"] = [{"tool_name": d["tool_name"], "method": d["method"],
                             "fields": list(d["declarative_attributes"]["fields"])} for d in drafts]
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - fail and record every validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
