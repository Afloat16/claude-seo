"""Save one live public page with the production Shopify crawl worker.

This checks the saved artifact and its record mapping, not Shopify signatures
or a store crawl. Robots rules and the production URL safety layer are used.
No network response, filesystem operation, or crawl function is replaced.
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
from urllib.robotparser import RobotFileParser

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
        "scope": "One real public page, robots permitted, unsigned; no Shopify store or signature claim",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match the expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import shopify_crawl as crawl
        import url_safety

        for module in (crawl, url_safety):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")

        target = urlparse(SAMPLE_URL)
        origin = crawl.Origin(target.scheme, target.netloc)
        with url_safety.safe_requests_session(SAMPLE_URL, public_only=True) as session:
            session.headers.update(crawl.BASE_HEADERS)
            response = session.get(origin.root + "/robots.txt", timeout=20, allow_redirects=False, stream=True)
            report["robots_status"] = response.status_code
            require(response.status_code == 200, "Could not retrieve live robots rules")
            body, too_large = crawl.read_bounded(response, crawl.MAX_ROBOTS_BYTES)
            require(not too_large, "Robots file exceeded the size limit")
            robots = RobotFileParser()
            robots.parse(body.decode("utf-8", "replace").splitlines())
            require(robots.can_fetch(crawl.USER_AGENT, SAMPLE_URL), "Live robots rules disallow this page")
            delay = robots.crawl_delay(crawl.USER_AGENT) or 0
            require(delay <= 30, "Live robots delay exceeds this smoke test's time budget")
            with tempfile.TemporaryDirectory(prefix="seo-live-html-") as directory:
                artifact_dir = Path(directory)
                record = crawl.crawl_one(
                    lambda: session, SAMPLE_URL, origin, None, crawl.RateLimitGovernor(delay),
                    timeout=20, save_html_dir=artifact_dir,
                )
                report["record"] = record
                require(record.get("status") == 200 and record.get("html") is True,
                        "Production crawl worker did not return a successful HTML page")
                require("python" in (record.get("title") or "").lower(), "Unexpected live page title")
                filename = record.get("html_file")
                require(isinstance(filename, str) and Path(filename).name == filename,
                        "Missing or unsafe artifact filename")
                files = list(artifact_dir.iterdir())
                require(len(files) == 1 and files[0].name == filename, "Record does not map to one saved file")
                html = files[0].read_text(encoding="utf-8")
                require(len(html) > 1000, "Unexpectedly short saved HTML")
                require(crawl.analyze_html(html)["title"] == record["title"], "Saved content and record disagree")
                report["artifact"] = {
                    "filename": filename,
                    "bytes": files[0].stat().st_size,
                    "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                }
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - fail and record every validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
