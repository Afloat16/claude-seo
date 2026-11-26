#!/usr/bin/env python3
"""
Independent LCP image-subpart percentiles via CrUX API.

Since January 2025 CrUX has exposed the four LCP sub-metrics:
  - largest_contentful_paint_image_time_to_first_byte
  - largest_contentful_paint_image_resource_load_delay
  - largest_contentful_paint_image_resource_load_duration
  - largest_contentful_paint_image_element_render_delay

Each p75 aggregates a separate distribution of image LCP on full page
loads. These values can prioritize investigation, but neither their sum
nor their ratio to overall LCP gives a decomposition of overall p75 LCP.

Usage::

    python scripts/lcp_subparts.py https://example.com/
    python scripts/lcp_subparts.py https://example.com/ --form-factor PHONE --json

Requires the same Google API key used by ``scripts/crux_history.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from google_auth import get_api_key, google_api_key_headers, redact_google_api_key  # noqa: E402
from url_safety import URLSafetyError, validate_url_strict  # noqa: E402

CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"


_LCP_SUBPART_METRICS = [
    "largest_contentful_paint_image_time_to_first_byte",
    "largest_contentful_paint_image_resource_load_delay",
    "largest_contentful_paint_image_resource_load_duration",
    "largest_contentful_paint_image_element_render_delay",
]


def _query_crux(url: str, form_factor: str, api_key: str) -> dict:
    payload = {
        "url": url,
        "formFactor": form_factor,
        "metrics": _LCP_SUBPART_METRICS + ["largest_contentful_paint"],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", **google_api_key_headers(api_key)}
    request = urllib.request.Request(
        CRUX_ENDPOINT,
        data=body,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "error": (
                f"CrUX HTTP {exc.code}: "
                f"{redact_google_api_key(exc.read().decode('utf-8', 'replace'))}"
            )
        }
    except urllib.error.URLError as exc:
        return {"error": f"CrUX request failed: {redact_google_api_key(exc.reason)}"}


def _percentile(metric: dict) -> float | None:
    p = metric.get("percentiles", {}).get("p75")
    if p is None:
        return None
    try:
        return float(p)
    except (TypeError, ValueError):
        return None


def _summarise_record(raw: dict, url: str, form_factor: str) -> dict:
    """Describe observed p75s without treating them as additive timings."""
    metrics = raw.get("record", {}).get("metrics", {})
    breakdown: dict[str, float | None] = {}
    for key in _LCP_SUBPART_METRICS:
        m = metrics.get(key)
        breakdown[key] = _percentile(m) if m else None

    overall_lcp = _percentile(metrics.get("largest_contentful_paint", {})) \
        if metrics.get("largest_contentful_paint") else None

    # CrUX computes each percentile independently, and image subparts
    # exclude text/video LCP, back-forward navigations and prerenders.
    # https://developer.chrome.com/docs/crux/methodology/metrics
    ranked = sorted(
        ({"metric": key, "p75_ms": val} for key, val in breakdown.items() if val is not None),
        key=lambda part: part["p75_ms"],
        reverse=True,
    )

    recommendations: list[str] = []
    for d in ranked:
        if d["p75_ms"] <= 0:
            continue
        if d["metric"].endswith("time_to_first_byte"):
            recommendations.append(
                "Time to first byte: if a representative image-LCP trace confirms "
                "a slow response, check redirects, network latency, origin "
                "response time and CDN cache hits."
            )
        elif d["metric"].endswith("resource_load_delay"):
            recommendations.append(
                "Resource load delay: if a representative image-LCP trace confirms "
                "late discovery, check when the image becomes discoverable and "
                "whether preload or fetchpriority would help."
            )
        elif d["metric"].endswith("resource_load_duration"):
            recommendations.append(
                "Resource load duration: if a representative image-LCP trace confirms "
                "a slow download, check image byte size, responsive sizes, "
                "image formats and network throughput."
            )
        elif d["metric"].endswith("element_render_delay"):
            recommendations.append(
                "Element render delay: if a representative image-LCP trace confirms "
                "a delay after the image is available, inspect rendering work, "
                "blocking CSS/JS and when the element becomes visible."
            )

    return {
        "url": url,
        "form_factor": form_factor,
        "p75_lcp_ms": overall_lcp,
        "subparts_p75_ms": breakdown,
        "ranked_subparts": ranked,
        "interpretation": {
            "aggregation": "independent_p75",
            "subparts_population": "image_lcp_full_page_loads",
            "additive": False,
            "note": (
                "Each image subpart is an independent p75, not a contribution to "
                "overall p75 LCP. Image subparts exclude text/video LCP, "
                "back-forward navigations and prerendered pages that overall LCP "
                "can include. Use their relative values to prioritize investigation "
                "and confirm causes in representative traces."
            ),
        },
        "recommendations": recommendations,
        "raw": raw,
    }


def analyse(url: str, form_factor: str = "PHONE") -> dict:
    try:
        url, _ = validate_url_strict(url)
    except URLSafetyError as exc:
        return {"error": f"url_safety: {exc}"}

    api_key = get_api_key()
    if not api_key:
        return {
            "error": "Google API key not configured. "
                     "Run `python scripts/google_auth.py --setup`.",
        }

    raw = _query_crux(url, form_factor, api_key)
    if "error" in raw:
        return raw
    return _summarise_record(raw, url, form_factor)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report independent image-LCP subpart p75s via CrUX."
    )
    parser.add_argument("url")
    parser.add_argument(
        "--form-factor",
        choices=("PHONE", "DESKTOP", "TABLET", "ALL_FORM_FACTORS"),
        default="PHONE",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = analyse(args.url, args.form_factor)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        # Strip the noisy CrUX raw response from JSON output by default.
        out = {k: v for k, v in result.items() if k != "raw"}
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"URL: {result['url']}")
        print(f"Form factor: {result['form_factor']}")
        overall = result["p75_lcp_ms"]
        print(f"Overall p75 LCP: {overall if overall is not None else 'n/a'} ms")
        print("Subparts (independent p75 ms; image LCP on full page loads):")
        for k, v in result["subparts_p75_ms"].items():
            label = k.replace("largest_contentful_paint_image_", "")
            print(f"  {label:35s} {v if v is not None else 'n/a'}")
        print(f"\n{result['interpretation']['note']}")
        if result["ranked_subparts"]:
            print("\nSubparts ranked by observed p75 (not shares of overall LCP):")
            for d in result["ranked_subparts"]:
                short = d["metric"].replace("largest_contentful_paint_image_", "")
                print(f"  {short} = {d['p75_ms']:.0f} ms")
        if result["recommendations"]:
            print("\nChecks to prioritize and confirm in traces:")
            for r in result["recommendations"]:
                print(f"  - {r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
