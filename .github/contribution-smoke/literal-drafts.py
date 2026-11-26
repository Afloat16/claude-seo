"""Check literal preservation on the actual CommonMark specification source.

Fetch the public Markdown document with the production safe fetcher, then
compare code and link tokens before/after cleanup with an independent Markdown
parser. No fixture is substituted for an unavailable live source.
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
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlparse

SAMPLE_URL = "https://spec.commonmark.org/0.31.2/spec.txt"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def literal_tokens(tokens):
    result = []
    for token in tokens:
        if token.type in {"fence", "code_block", "code_inline"}:
            result.append((token.type, token.info, token.content))
        elif token.type == "link_open":
            result.append((token.type, token.attrGet("href"), token.attrGet("title")))
        elif token.type == "image":
            result.append((token.type, token.attrGet("src"), token.attrGet("title")))
        if token.children:
            result.extend(literal_tokens(token.children))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    report = {
        "expected_sha": args.expected_sha, "tested_sha": None,
        "started_at": datetime.now(UTC).isoformat(), "source_url": SAMPLE_URL,
        "scope": "Actual published Markdown source; code/link token invariants and CLI/API agreement",
        "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        report["tested_sha"] = sha
        require(sha == args.expected_sha, "Checkout differs from expected contribution")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        sys.path.insert(0, str(root / "scripts"))
        import content_humanize
        import fetch_page
        import runtime
        from markdown_it import MarkdownIt

        for module in (content_humanize, fetch_page, runtime):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        script = runtime._resolve_script(root, "content_humanize.py", None)
        require(script == root / "scripts/content_humanize.py", "Humanizer is not registered")
        page = fetch_page.fetch_page(SAMPLE_URL, timeout=20, max_time=40, public_only=True)
        report["fetch"] = {
            "status": page.get("status_code"), "final_url": page.get("url"),
            "bytes": page.get("content_bytes"), "error": page.get("error"),
        }
        require(not page.get("error") and page.get("status_code") == 200, "Actual source fetch failed")
        require(not page.get("too_large"), "Source body is incomplete")
        require(urlparse(page.get("url") or "").hostname == "spec.commonmark.org", "Unexpected source host")
        source = page.get("content")
        require(isinstance(source, str) and len(source) > 10000 and source.startswith("# Introduction"),
                "Unexpected specification source")
        parser = MarkdownIt("commonmark")
        before = literal_tokens(parser.parse(source))
        result = content_humanize.humanize(source)
        after = literal_tokens(parser.parse(result["cleaned"]))
        counts = {kind: sum(token[0] == kind for token in before)
                  for kind in ("fence", "code_block", "code_inline", "link_open", "image")}
        require(counts["fence"] > 100 and counts["code_inline"] > 20 and counts["link_open"] > 20,
                "Actual source did not provide the expected literal coverage")
        require(before == after, "Cleanup changed actual code or link tokens")
        process = subprocess.run(
            [sys.executable, str(script), "-", "--json"], input=source,
            cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=30,
        )
        require(process.returncode == 0 and json.loads(process.stdout) == result, "CLI JSON and API disagree")
        with tempfile.TemporaryDirectory(prefix="humanize-live-") as temp:
            draft, output = Path(temp) / "draft.md", Path(temp) / "cleaned.md"
            draft.write_text(source, encoding="utf-8")
            file_process = subprocess.run(
                [sys.executable, str(script), str(draft), "--output", str(output)],
                cwd=root, text=True, encoding="utf-8", capture_output=True, timeout=30,
            )
            require(file_process.returncode == 0 and output.read_text(encoding="utf-8") == result["cleaned"],
                    "CLI file output and API disagree")
        report.update({
            "parser_version": version("markdown-it-py"), "literal_counts": counts,
            "literal_tokens_unchanged": True, "characters": len(source),
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "cleaned_sha256": hashlib.sha256(result["cleaned"].encode("utf-8")).hexdigest(),
            "change_count": result["change_count"], "invisible_count": result["invisible_count"],
            "cli_json_returncode": process.returncode, "cli_file_returncode": file_process.returncode,
            "passed": True,
        })
    except Exception as exc:  # noqa: BLE001 - record and fail every verification error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
