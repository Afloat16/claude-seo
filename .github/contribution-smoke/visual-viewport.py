"""Run the reviewed visual audit on an unmodified public documentation page."""

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
        "scope": "Actual public HTML fetch and guarded desktop/mobile visual audit through API and JSON CLI",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import analyze_visual
        import fetch_page
        import parse_html
        import runtime

        for module in (analyze_visual, fetch_page, parse_html, runtime):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "analyze_visual.py", None)
        require(script == root / "scripts/analyze_visual.py", "Visual auditor is not registered")
        fetched = fetch_page.fetch_page(SAMPLE_URL, timeout=20, max_time=30, public_only=True)
        report["fetch"] = {"status": fetched.get("status_code"), "error": fetched.get("error"),
                           "final_url": fetched.get("url")}
        require(not fetched.get("error") and fetched.get("status_code") == 200, "Actual HTML fetch failed")
        require(urlparse(fetched.get("url") or "").hostname == "docs.python.org", "Unexpected final host")
        require(fetched.get("too_large") is False, "Actual page was not completely read")
        html = fetched.get("content")
        require(isinstance(html, str) and len(html) > 1000, "Unexpected public document")
        parsed = parse_html.parse_html(html, fetched["url"])
        require("Python" in (parsed.get("title") or ""), "Expected actual Python documentation title")
        report["fetch"].update(characters=len(html), title=parsed["title"],
                               html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest())
        result = analyze_visual.analyze_visual(SAMPLE_URL, timeout=30000)
        report["visual"] = result
        require(result.get("error") is None, f"Production visual audit failed: {result.get('error')}")
        require(result["above_fold"]["h1_visible"] is True, "Public documentation H1 was not in the first viewport")
        require(isinstance(result["fonts"]["base_size"], (int, float)), "Mobile analysis did not run")
        process = subprocess.run(
            [sys.executable, str(script), SAMPLE_URL, "--json", "--timeout", "30000"],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=90,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Production visual JSON CLI failed")
        cli_result = json.loads(process.stdout)
        require(cli_result.get("error") is None, f"Visual CLI reported an error: {cli_result.get('error')}")
        require(cli_result["above_fold"] == result["above_fold"], "API and CLI first-viewport findings disagree")
        report["cli"]["above_fold"] = cli_result["above_fold"]
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail every validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
