"""Generate lodging JSON from the current, live Schema.org documentation example.

This verifies type/time mapping against a published example, not an actual
booking. A network failure or missing example fails; no fixtures are substituted.
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

SAMPLE_URL = "https://schema.org/LodgingReservation"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    report = {
        "expected_sha": args.expected_sha,
        "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(),
        "source": "Live Schema.org documentation example, not a verified booking",
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
        import runtime
        import schema_generate
        from bs4 import BeautifulSoup

        for module in (fetch_page, runtime, schema_generate):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "schema_generate.py", None)
        require(script == root / "scripts/schema_generate.py", "Generator is not registered")

        page = fetch_page.fetch_page(SAMPLE_URL, timeout=20, max_time=50, public_only=True)
        report["fetch"] = {
            "final_url": page.get("url"),
            "status_code": page.get("status_code"),
            "error": page.get("error"),
            "content_bytes": page.get("content_bytes"),
            "fetch_duration_ms": page.get("fetch_duration_ms"),
        }
        require(page.get("error") is None, f"Live documentation fetch failed: {page.get('error')}")
        require(page.get("status_code") == 200, "Live documentation did not return HTTP 200")
        require(not page.get("too_large"), "Live documentation exceeded the body-size limit")
        html = page.get("content")
        require(isinstance(html, str) and len(html) > 1000, "Unexpectedly short sample response")
        report["fetch"]["html_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()

        sample = None
        for block in BeautifulSoup(html, "html.parser").find_all("pre"):
            raw = block.get_text()
            # Schema.org displays JSON-LD in an escaped HTML script element.
            # Read only the example text; no JavaScript is executed.
            examples = [raw]
            examples.extend(tag.string or "" for tag in BeautifulSoup(raw, "html.parser").find_all(
                "script", attrs={"type": "application/ld+json"},
            ))
            for text in examples:
                try:
                    data = json.loads(text)
                except (ValueError, TypeError):
                    continue
                if isinstance(data, dict) and data.get("@type") == "LodgingReservation":
                    sample = data
                    report["example_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    break
            if sample is not None:
                break
        require(sample is not None, "No LodgingReservation JSON example found in the live document")
        name = sample["reservationFor"]["name"]
        start = sample["checkinTime"]
        end = sample["checkoutTime"]
        require(all(isinstance(v, str) and v for v in (name, start, end)), "Incomplete lodging example")

        result = schema_generate.reservation(name, start, end=end, kind="LodgingReservation")
        require(result["reservationFor"]["@type"] == "LodgingBusiness", "Wrong reserved object type")
        require(result["checkinTime"] == start and result["checkoutTime"] == end, "Lodging times changed")
        require("startTime" not in result and "endTime" not in result, "Restaurant fields leaked into lodging")
        process = subprocess.run(
            [sys.executable, str(script), "reservation", "--reservation-kind", "LodgingReservation",
             "--provider", name, "--start", start, "--end", end],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Generator CLI rejected the live example inputs")
        require(json.loads(process.stdout) == result, "CLI and production API results disagree")
        report["generated"] = result
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record evidence and fail on every error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
