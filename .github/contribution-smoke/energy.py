"""Fetch Google's live EPREL example and validate its unchanged Product JSON.

This checks a live documentation example, not a merchant's registration. No
fixtures are substituted if fetching or finding the published example fails.
Keep this driver on the fork's validation branch, outside the upstream PR.
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

SAMPLE_URL = "https://developers.google.com/search/docs/appearance/structured-data/merchant-listing"


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
        "source": "Live Google documentation example, not a verified EPREL registry entry",
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
        import schema_ecommerce_validate
        from bs4 import BeautifulSoup

        for module in (fetch_page, runtime, schema_ecommerce_validate):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "schema_ecommerce_validate.py", None)
        require(script == root / "scripts/schema_ecommerce_validate.py", "Validator is not registered")

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
        soup = BeautifulSoup(html, "html.parser")
        for block in soup.find_all("pre"):
            raw = block.get_text()
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict) or payload.get("@type") != "Product":
                continue
            certificate = payload.get("hasCertification")
            if isinstance(certificate, dict) and certificate.get("name") == "EPREL":
                sample = payload
                report["example"] = {
                    "code_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    "product_name": payload.get("name"),
                    "hasCertification": certificate,
                }
                break
        require(sample is not None, "No complete EPREL Product example found in the live document")

        result = schema_ecommerce_validate.validate(sample, require_eu_energy=True)
        require(result["ok"] is True, "Published EPREL Product example failed the validator")
        require(not any(f["rule"] == "missing-eu-energy-class" for f in result["findings"]),
                "Published EPREL Product was falsely reported to lack an energy label")
        process = subprocess.run(
            [sys.executable, str(script), "--eu", "--json"],
            input=json.dumps(sample), cwd=root, text=True, encoding="utf-8",
            capture_output=True, timeout=30,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Validator CLI rejected the live published example")
        require(json.loads(process.stdout) == result, "CLI and production API results disagree")
        report["cli"]["summary"] = result["summary"]
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record evidence and fail on every error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
