"""Check generated OrderAction property placement against live Schema.org tables.

The network inputs are current official documentation pages. The generated
action uses explicit test inputs, not observed merchant terms or a live order.
No fixture is substituted for a failed documentation fetch.
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
from urllib.parse import urlparse

SAMPLE_URL = "https://schema.org/OrderAction"


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
        "scope": "Live official property tables and generated test input; no live merchant or ordering claim",
        "sources": [],
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout differs from expected contribution")
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

        properties = {}
        for kind in ("OrderAction", "Organization", "EntryPoint"):
            url = "https://schema.org/" + kind
            page = fetch_page.fetch_page(url, timeout=20, max_time=40, public_only=True)
            evidence = {
                "url": url, "status": page.get("status_code"), "final_url": page.get("url"),
                "error": page.get("error"), "bytes": page.get("content_bytes"),
            }
            report["sources"].append(evidence)
            require(not page.get("error") and page.get("status_code") == 200,
                    f"Actual {kind} documentation fetch failed")
            require(not page.get("too_large"), "Documentation body is incomplete")
            require(urlparse(page.get("url") or "").hostname == "schema.org", "Unexpected source host")
            html = page.get("content")
            require(isinstance(html, str) and len(html) > 1000, "Unexpectedly short documentation")
            evidence["sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest()
            soup = BeautifulSoup(html, "html.parser")
            require(soup.h1 is not None and soup.h1.get_text(strip=True) == kind, "Unexpected type page")
            table = soup.find("table", class_="definition-table")
            require(table is not None, f"No published {kind} property table found")
            names = set()
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"], recursive=False)
                if len(cells) != 3:
                    continue
                for link in cells[0].find_all("a", href=True):
                    name = link.get_text(strip=True)
                    if name and urlparse(link["href"]).path.rstrip("/").rsplit("/", 1)[-1] == name:
                        names.add(name)
            require(len(names) > 10, f"Could not read the {kind} property definitions")
            properties[kind] = names
            evidence["property_count"] = len(names)

        result = schema_generate.order_action(
            "Documentation sample", order_url=SAMPLE_URL,
            accepted_payment_method=["Cash"], delivery_method=["https://schema.org/OnSitePickup"],
        )
        nodes = {"OrderAction": result, "Organization": result["provider"], "EntryPoint": result["target"]}
        for kind, node in nodes.items():
            unsupported = sorted(key for key in node if not key.startswith("@") and key not in properties[kind])
            require(not unsupported, f"Unsupported properties on {kind}: {unsupported}")
        require("merchant" not in result and "acceptedPaymentMethod" not in result, "Wrong property placement")
        require("inLanguage" not in result["target"] and "priceSpecification" not in result,
                "Unsupported language or unsupplied price terms remain")
        bare = schema_generate.order_action("Documentation sample", order_url=SAMPLE_URL)
        require("deliveryMethod" not in bare, "Unsupplied delivery terms remain")
        process = subprocess.run(
            [sys.executable, str(script), "order", "--merchant", "Documentation sample",
             "--order-url", SAMPLE_URL, "--accepted-payment-method", "Cash",
             "--delivery-method", "https://schema.org/OnSitePickup"],
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
        report["cli"] = {"returncode": process.returncode, "stderr": process.stderr}
        require(process.returncode == 0 and json.loads(process.stdout) == result, "CLI and API disagree")
        report["generated"] = result
        report["checked_nodes"] = {kind: sorted(key for key in node if not key.startswith("@"))
                                   for kind, node in nodes.items()}
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - fail and record every verification error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
