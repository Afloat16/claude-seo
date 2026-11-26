"""Verify shared charset decoding against the actual French Python docs.

The remote bytes are not modified. Comment and attribute regressions are
covered by the unit suite; this smoke exercises safe fetching, the byte
prescan, decoded SEO metadata and the production JSON CLI on real HTML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

SAMPLE_URL = "https://docs.python.org/fr/3/"


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
        "scope": "Unmodified real HTTP response, safe byte fetch, shared decoder and JSON CLI",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import parse_html
        import runtime
        import url_safety

        for module in (parse_html, runtime, url_safety):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "fetch_page.py", None)
        require(script == root / "scripts/fetch_page.py", "Fetcher is not registered")
        with url_safety.safe_requests_session(SAMPLE_URL, public_only=True) as session:
            with session.get(SAMPLE_URL, timeout=20, stream=True) as response:
                report["fetch"] = {
                    "final_url": response.url, "status": response.status_code,
                    "content_type": response.headers.get("Content-Type", ""),
                }
                require(response.status_code == 200, "Live page did not return HTTP 200")
                require(urlparse(response.url).hostname == "docs.python.org", "Unexpected final host")
                raw, too_large, timed_out = url_safety.read_bounded_until(
                    response, 1_000_000, time.monotonic() + 30,
                )
        require(not too_large and not timed_out and len(raw) > 1000,
                "Live body was incomplete or unexpectedly short")
        # This live document declares UTF-8 itself. Strict decoding provides
        # an independent comparison for the shared prescan/decoder output.
        declared = url_safety._extract_meta_charset(raw)
        require(isinstance(declared, str) and declared.lower() in {"utf-8", "utf8"},
                "Live page no longer supplies the expected UTF-8 meta declaration")
        expected = raw.decode("utf-8")
        require(any(ord(char) > 127 for char in expected), "Sample has no non-ASCII text")
        decoded = url_safety.decode_body(raw, report["fetch"]["content_type"])
        require(decoded == expected, "Production decoder differs from actual UTF-8 bytes")
        parsed = parse_html.parse_html(decoded, base_url=report["fetch"]["final_url"])
        require(bool(parsed.get("title")) and "Python" in parsed["title"], "Missing actual document title")
        report["fetch"].update(
            bytes=len(raw), characters=len(decoded), meta_charset=declared,
            non_ascii_characters=sum(ord(char) > 127 for char in decoded),
            body_sha256=hashlib.sha256(raw).hexdigest(), title=parsed["title"],
        )
        process = subprocess.run(
            [sys.executable, str(script), SAMPLE_URL, "--json", "--timeout", "20", "--max-time", "30"],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=45,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0, "Live fetch CLI failed")
        output = json.loads(process.stdout)
        require(output.get("status_code") == 200 and not output.get("error"), "CLI fetch was not successful")
        cli_parsed = parse_html.parse_html(output.get("content") or "", base_url=output.get("url"))
        require(cli_parsed["title"] == parsed["title"], "CLI and safe byte fetch disagree on actual title")
        report["cli"]["title"] = cli_parsed["title"]
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail any validation error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
