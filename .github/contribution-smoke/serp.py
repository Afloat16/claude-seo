"""Validate the exact SERP-overlap commit with observed URLs and real HTTPS fetches.

Keep this driver and its saved web-search input on the fork's validation branch.
The saved export is explicitly incomplete as evidence of Google organic rankings.
The candidate CLI consumes it unchanged; live checks fetch two URLs that actually
occur in that export through the production URL safety implementation.
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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    source = args.input.resolve()
    report = {
        "expected_sha": args.expected_sha,
        "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(),
        "source": "Observed public web-search URL export; complete Google organic ranks unverified",
        "live_fetches": [],
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match the expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import fetch_page
        import runtime
        import serp_overlap

        for module in (fetch_page, runtime, serp_overlap):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "serp_overlap.py", None)
        require(script == root / "scripts/serp_overlap.py", "Calculator is not registered with the launcher")

        raw = source.read_bytes()
        payload = json.loads(raw)
        require(len(payload["serps"]) == 2, "Expected the two saved public-search inputs")
        require(all(row.get("error") for row in payload["serps"]),
                "Unverified ranking coverage must remain explicit in the input")
        process = subprocess.run(
            [sys.executable, str(script), str(source)],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=60,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Calculator CLI failed")
        result = json.loads(process.stdout)
        require(result["complete"] is False, "Unverified rankings were presented as complete")
        require(result["serp_matrix"]["scores"] == [[None, None], [None, None]],
                "Incomplete rank coverage produced a numeric matrix score")
        require(len(result["pairs"]) == 1, "Unexpected number of comparisons")
        pair = result["pairs"][0]
        require(pair["relationship"] == "inconclusive" and pair["score"] is None,
                "The CLI invented a relationship for unverified rank coverage")
        require(pair["observed_shared_count"] == 4, "Shared URL evidence differs from the saved sample")
        report["cli"].update({
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "keywords": result["serp_matrix"]["keywords"],
            "provided_results": [row["provided_results"] for row in result["coverage"]],
            "observed_shared_count": pair["observed_shared_count"],
            "shared_urls": pair["shared_urls"],
            "score": pair["score"],
            "relationship": pair["relationship"],
        })

        # These exact URLs came from the collected search export. No synthetic
        # URLs or fabricated rank positions are added to make this test pass.
        urls = list(dict.fromkeys(row["urls"][0] for row in payload["serps"]))
        for url in urls:
            page = fetch_page.fetch_page(url, timeout=20, max_time=50, public_only=True)
            fetched = {
                "requested_url": url,
                "final_url": page.get("url"),
                "status_code": page.get("status_code"),
                "error": page.get("error"),
                "content_bytes": page.get("content_bytes"),
                "fetch_duration_ms": page.get("fetch_duration_ms"),
            }
            report["live_fetches"].append(fetched)
            require(page.get("error") is None, f"Real sample URL fetch failed: {page.get('error')}")
            require(page.get("status_code") == 200, "Real sample URL did not return HTTP 200")
            require(not page.get("too_large"), "Real sample URL exceeded the body-size limit")
            html = page.get("content")
            require(isinstance(html, str) and len(html) > 1000, "Unexpectedly short sample response")
            fetched["html_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
            fetched["normalized_input_url"] = serp_overlap.normalise_url(url)
            require(fetched["normalized_input_url"].startswith("https://docs.python.org/"),
                    "Unexpected normalized URL from the Python documentation sample")

        json.dumps(report, allow_nan=False)
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - preserve evidence and fail the job on every error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
