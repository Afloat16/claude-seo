"""Live public-page smoke tests for one exact claude-seo candidate checkout.

This validation driver stays outside the upstream change. It imports production
functions from the asserted checkout and performs real requests through the
repository's URL safety layer. No response, DNS, parser, or probe is replaced.
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
from urllib.parse import urljoin, urlparse


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("canonical", "images", "preload"), required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repo_root.resolve()
    report = {
        "case": args.case,
        "expected_sha": args.expected_sha,
        "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(),
        "network_mode": "real public HTTPS through production URL safety",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout does not match the expected patch SHA")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))

        import fetch_page
        import parse_html
        import preload_check
        from bs4 import BeautifulSoup

        for module in (fetch_page, parse_html, preload_check):
            require(
                Path(module.__file__).resolve().parent == root / "scripts",
                f"Imported {module.__name__} from a different checkout",
            )

        url = (
            "https://docs.python.org/3/library/functions.html"
            if args.case == "canonical"
            else "https://www.python.org/"
        )
        if args.case == "preload":
            # Match preload_check's actual safe-fetch and decode path.
            response = preload_check.safe_requests_get(url, timeout=20, allow_redirects=True)
            try:
                report["fetch"] = {
                    "requested_url": url,
                    "final_url": response.url,
                    "status_code": response.status_code,
                }
                response.raise_for_status()
                html = preload_check.decode_response_text(response)
                final_url = response.url
                headers = dict(response.headers)
            finally:
                response.close()
        else:
            page = fetch_page.fetch_page(url, timeout=20, max_time=50, public_only=True)
            report["fetch"] = {
                "requested_url": url,
                "final_url": page.get("url"),
                "status_code": page.get("status_code"),
                "content_bytes": page.get("content_bytes"),
                "error": page.get("error"),
                "fetch_duration_ms": page.get("fetch_duration_ms"),
            }
            require(page.get("error") is None, f"Live fetch failed: {page.get('error')}")
            require(not page.get("too_large"), "Live page exceeded the body-size limit")
            html = page.get("content")
            final_url = page.get("url")
            headers = page.get("headers", {})

        require(report["fetch"]["status_code"] == 200, "Live page did not return HTTP 200")
        require(urlparse(final_url).hostname in {"docs.python.org", "www.python.org", "python.org"},
                "Unexpected final host")
        require(isinstance(html, str) and len(html) > 1000, "Live HTML is empty or unexpectedly short")
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        require("python" in title.lower(), "Live response is not the expected Python page")
        report["fetch"].update({
            "title": title,
            "decoded_characters": len(html),
            "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        })

        if args.case == "canonical":
            parsed = parse_html.parse_html(html, final_url)
            canonical = next(
                (link for link in soup.find_all("link")
                 if "canonical" in [str(value).lower() for value in link.get("rel", [])]),
                None,
            )
            require(canonical is not None and bool(canonical.get("href")),
                    "Live document has no canonical declaration to verify")
            base = soup.find("base", href=True)
            document_base = urljoin(final_url, base["href"]) if base else final_url
            expected = urljoin(document_base, canonical["href"].strip())
            require(parsed["canonical"] == expected, "Live canonical target differs from its declared URL")
            require(bool(parsed["title"]), "Production parser returned no title")
            report["result"] = {
                "declared_canonical": canonical["href"],
                "document_base": document_base,
                "canonical": parsed["canonical"],
                "internal_links": len(parsed["links"]["internal"]),
                "external_links": len(parsed["links"]["external"]),
            }
        elif args.case == "images":
            parsed = parse_html.parse_html(html, final_url)
            candidates = [
                image for image in parsed["images"]
                if urlparse(image.get("resolved_src") or image.get("src") or "").hostname
                in {"www.python.org", "python.org"}
            ]
            require(bool(candidates), "Live page contains no same-site image to probe")
            # Probe the actual first same-site image, with no synthetic prefix.
            images = parse_html.probe_images([dict(candidates[0])], timeout=20, limit=1)
            image = images[0]
            report["result"] = {
                key: image.get(key) for key in
                ("src", "resolved_src", "probed_src", "served_format", "served_bytes", "negotiated", "probe_error")
            }
            report["result"]["images_on_live_page"] = len(parsed["images"])
            require(image["probe_error"] is None, f"Live image probe failed: {image['probe_error']}")
            require(image["served_format"] in {"jpeg", "png", "webp", "avif", "gif", "svg", "jxl"},
                    "Live image has no recognized served format")
        else:
            require(bool(soup.find_all("script")), "Live page has no scripts for the unload scan")
            result = preload_check.analyse(html, headers)
            require(result["score"] in {0, 25, 50, 75, 100}, "Unexpected preload score")
            require(all(isinstance(value, bool) for value in result["bfcache_signals"].values()),
                    "Invalid bfcache signal shape")
            require(isinstance(result["recommendations"], list), "Invalid recommendations shape")
            report["result"] = result
            report["script_elements_on_live_page"] = len(soup.find_all("script"))

        # The same strict JSON serialization used for the result artifact.
        json.dumps(report, allow_nan=False)
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - retain evidence and fail the CI job on any error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
