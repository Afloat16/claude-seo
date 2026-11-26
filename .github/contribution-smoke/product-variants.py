"""Validate unchanged multi-page variant examples from live Google documentation.

This reads the actual published JSON-LD examples and compares baseline and
candidate CLI output. It does not crawl the example.com merchant URLs or claim
those linked variants were checked.
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

SAMPLE_URL = "https://developers.google.com/search/docs/appearance/structured-data/product-variants"
BASE_SHA = "5d7d85ef32ade08dd4265cb678af8357ca6e6098"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    root, base = args.repo_root.resolve(), args.baseline_root.resolve()
    report = {
        "expected_sha": args.expected_sha, "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(), "source_url": SAMPLE_URL,
        "scope": "Live official JSON-LD examples; linked merchant URLs are not crawled or verified",
        "examples": [], "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        base_sha = subprocess.check_output(["git", "-C", str(base), "rev-parse", "HEAD"], text=True).strip()
        report.update({"tested_sha": sha, "baseline_sha": base_sha})
        require(sha == args.expected_sha and base_sha == BASE_SHA, "Checkout differs from expected commit")
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
            "status": page.get("status_code"), "final_url": page.get("url"),
            "bytes": page.get("content_bytes"), "error": page.get("error"),
        }
        require(not page.get("error") and page.get("status_code") == 200, "Actual documentation fetch failed")
        require(not page.get("too_large"), "Documentation body is incomplete")
        html = page.get("content")
        require(isinstance(html, str) and len(html) > 1000, "Unexpected documentation body")
        report["fetch"]["sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
        seen = set()
        for block in BeautifulSoup(html, "html.parser").find_all("pre"):
            raw = block.get_text()
            scripts = BeautifulSoup(raw, "html.parser").find_all("script", type="application/ld+json")
            for raw_json in [raw, *(tag.get_text() for tag in scripts)]:
                try:
                    sample = json.loads(raw_json)
                except (TypeError, ValueError):
                    continue  # Incomplete illustrative excerpts are not full JSON-LD examples.
                digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
                if digest in seen:
                    continue
                seen.add(digest)
                products = [node for node in nodes(sample) if node.get("@type") == "Product"]
                refs = [node for node in products if "url" in node and "isVariantOf" in node
                        and set(node) <= {"@context", "@type", "@id", "url", "isVariantOf"}]
                if not refs:
                    continue
                require(len(products) > len(refs), "Published example lacks a full local product")
                result = schema_ecommerce_validate.validate(sample)
                require(result["ok"] and not result["summary"]["high"] and not result["summary"]["critical"],
                        "Published multi-page variant example was rejected")
                notices = [f for f in result["findings"] if f["rule"] == "linked-variant-reference"]
                require(len(notices) == len(refs) and all(f["severity"] == "Info" for f in notices),
                        "References were not accounted for explicitly")
                cli = subprocess.run(
                    [sys.executable, str(script), "--json"], input=json.dumps(sample), cwd=root,
                    text=True, encoding="utf-8", capture_output=True, timeout=30,
                )
                require(cli.returncode == 0 and json.loads(cli.stdout) == result, "CLI and API disagree")
                baseline = subprocess.run(
                    [sys.executable, str(base / "scripts/schema_ecommerce_validate.py"), "--json"],
                    input=json.dumps(sample), cwd=base, text=True, encoding="utf-8", capture_output=True, timeout=30,
                )
                base_result = json.loads(baseline.stdout)
                false_failures = [f for f in base_result["findings"]
                                  if f["rule"] in {"missing-product-name", "missing-product-image", "missing-product-offers"}]
                require(baseline.returncode == 1 and len(false_failures) == 3 * len(refs),
                        "Actual example did not reproduce expected baseline false failures")
                report["examples"].append({
                    "code_sha256": digest, "products": len(products), "linked_references": len(refs),
                    "reference_urls": [node["url"] for node in refs],
                    "baseline_false_high_findings": len(false_failures), "baseline_returncode": baseline.returncode,
                    "candidate_summary": result["summary"], "candidate_returncode": cli.returncode,
                })
        require(len(report["examples"]) >= 2, "Did not find both complete multi-page examples in the live source")
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail every verification error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
