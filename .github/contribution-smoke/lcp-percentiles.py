"""Fetch Google's actual published CrUX record and exercise the summarizer.

This is a live documentation test, not an authenticated CrUX query or a claim
about current web.dev performance. CLI tests replay the unchanged published
record at the provider boundary; public URL validation remains active.
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

SAMPLE_URL = "https://developer.chrome.com/blog/crux-2025-02"
BASE_SHA = "5d7d85ef32ade08dd4265cb678af8357ca6e6098"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def replay_cli(root: Path, sample: dict, as_json: bool):
    code = """
import json, sys
from pathlib import Path
from unittest.mock import patch
root = Path(sys.argv[1]).resolve()
as_json = sys.argv[2] == "json"
sys.path.insert(0, str(root / "scripts"))
import lcp_subparts
assert Path(lcp_subparts.__file__).resolve() == root / "scripts/lcp_subparts.py"
sample = json.load(sys.stdin)
key = sample["record"]["key"]
sys.argv = ["lcp_subparts.py", key["url"], "--form-factor", key["formFactor"]]
if as_json:
    sys.argv.append("--json")
with patch.object(lcp_subparts, "get_api_key", return_value="published-example-replay"), \
     patch.object(lcp_subparts, "_query_crux", return_value=sample):
    raise SystemExit(lcp_subparts.main())
"""
    return subprocess.run(
        [sys.executable, "-c", code, str(root), "json" if as_json else "text"],
        input=json.dumps(sample), text=True, encoding="utf-8", capture_output=True,
        cwd=root, timeout=30,
    )


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
        "scope": "Live official published CrUX example; not a current performance measurement",
        "authenticated_crux_request": False,
        "cli_provider_input": "Unmodified record from the actual fetched documentation",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        base_sha = subprocess.check_output(["git", "-C", str(base), "rev-parse", "HEAD"], text=True).strip()
        report.update({"tested_sha": sha, "baseline_sha": base_sha})
        require(sha == args.expected_sha and base_sha == BASE_SHA, "Checkout differs from expected commit")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))
        import fetch_page
        import lcp_subparts
        import runtime
        from bs4 import BeautifulSoup

        for module in (fetch_page, lcp_subparts, runtime):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        require(runtime._resolve_script(root, "lcp_subparts.py", None) == root / "scripts/lcp_subparts.py",
                "Script is not registered in the runtime")
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
        samples = []
        for block in BeautifulSoup(html, "html.parser").find_all("pre"):
            raw_json = block.get_text()
            try:
                sample = json.loads(raw_json)
            except ValueError:
                continue
            if not isinstance(sample, dict):
                continue
            record = sample.get("record", {})
            metrics = record.get("metrics", {})
            if all(key in metrics for key in lcp_subparts._LCP_SUBPART_METRICS):
                samples.append((raw_json, sample))
        require(len(samples) == 1, "Expected one complete published image-subpart record")
        raw_json, sample = samples[0]
        record = sample["record"]
        key = record["key"]
        require("largest_contentful_paint" not in record["metrics"], "Published example changed shape")
        result = lcp_subparts._summarise_record(sample, key["url"], key["formFactor"])
        expected = sorted(
            ({"metric": name, "p75_ms": float(record["metrics"][name]["percentiles"]["p75"])}
             for name in lcp_subparts._LCP_SUBPART_METRICS),
            key=lambda item: item["p75_ms"], reverse=True,
        )
        require(result["p75_lcp_ms"] is None and result["ranked_subparts"] == expected,
                "Measured image percentiles are missing or changed")
        require(len(result["recommendations"]) == 4 and result["interpretation"]["additive"] is False,
                "Independent diagnostic checks are missing")
        require("dominant_subparts" not in result and all("share" not in item for item in expected),
                "False contribution percentages remain")
        cli = replay_cli(root, sample, True)
        text_cli = replay_cli(root, sample, False)
        old = replay_cli(base, sample, True)
        require(cli.returncode == text_cli.returncode == old.returncode == 0,
                f"CLI replay failed: candidate={cli.stderr}, text={text_cli.stderr}, baseline={old.stderr}")
        require(json.loads(cli.stdout) == {k: v for k, v in result.items() if k != "raw"},
                "Candidate JSON CLI differs from production summary")
        require("independent p75" in text_cli.stdout and "40%" not in text_cli.stdout,
                "Text CLI still describes an additive contribution")
        old_result = json.loads(old.stdout)
        require(old_result["recommendations"] == [] and old_result["dominant_subparts"] == [],
                "Published record no longer reproduces the baseline diagnostic omission")
        report["published_record"] = {
            "code_sha256": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
            "key": key, "collection_period": record.get("collectionPeriod"),
            "overall_p75_ms": result["p75_lcp_ms"], "ranked_subparts": result["ranked_subparts"],
            "interpretation": result["interpretation"],
            "baseline_checks": len(old_result["recommendations"]),
            "candidate_checks": len(result["recommendations"]),
            "cli_returncodes": {"baseline_json": old.returncode,
                                "candidate_json": cli.returncode, "candidate_text": text_cli.returncode},
        }
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail every verification error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
