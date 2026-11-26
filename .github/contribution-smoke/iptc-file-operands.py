"""Exercise real ExifTool against temporary copies of a live public PNG.

The source-type value is controlled test metadata, not a claim about the
downloaded image's provenance. Only disposable copies are ever modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

SAMPLE_URL = "https://www.python.org/static/img/python-logo.png"
BASE_SHA = "5d7d85ef32ade08dd4265cb678af8357ca6e6098"
SOURCE_TYPE = "digitalCapture"
URI = "https://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cli(root: Path, cwd: Path, command: str, target: str):
    args = [sys.executable, str(root / "scripts/iptc_ai_label.py"), command, target, "--json"]
    if command == "inject":
        args += ["--source-type", SOURCE_TYPE]
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=40)
    require(bool(proc.stdout.strip()), f"CLI produced no JSON: {proc.stderr}")
    return proc.returncode, json.loads(proc.stdout)


def direct_read(image: Path) -> str:
    proc = subprocess.run(
        ["exiftool", "-XMP-iptcExt:DigitalSourceType", "-s3", "--", str(image)],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    require(proc.returncode == 0, f"Independent ExifTool read failed: {proc.stderr}")
    return proc.stdout.strip()


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
        "scope": "Real ExifTool on disposable copies of an actual public PNG",
        "source_type_is_controlled_test_metadata": True, "passed": False,
    }
    try:
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        base_sha = subprocess.check_output(["git", "-C", str(base), "rev-parse", "HEAD"], text=True).strip()
        report.update({"tested_sha": sha, "baseline_sha": base_sha})
        require(sha == args.expected_sha and base_sha == BASE_SHA, "Checkout differs from expected commit")
        require(not os.environ.get("CLAUDE_SEO_LOCAL_TARGETS"), "Local-target exceptions must be unset")
        require(shutil.which("exiftool") is not None, "Real ExifTool must be installed")
        version = subprocess.check_output(["exiftool", "-ver"], text=True, timeout=15).strip()
        report["exiftool_version"] = version
        require(bool(version), "ExifTool version unavailable")
        sys.path.insert(0, str(root / "scripts"))
        import iptc_ai_label
        import runtime
        import url_safety

        for module in (iptc_ai_label, runtime, url_safety):
            require(Path(module.__file__).resolve().parent == root / "scripts",
                    f"Imported {module.__name__} from another checkout")
        require(runtime._resolve_script(root, "iptc_ai_label.py", None) == root / "scripts/iptc_ai_label.py",
                "Script is not registered in the runtime")
        deadline = time.monotonic() + 50
        with url_safety.safe_requests_session(SAMPLE_URL, public_only=True) as session:
            response = session.get(SAMPLE_URL, timeout=20, stream=True)
            try:
                report["fetch"] = {"status": response.status_code, "final_url": response.url,
                                   "content_type": response.headers.get("Content-Type")}
                response.raise_for_status()
                raw, too_large, timed_out = url_safety.read_bounded_until(response, 1024 * 1024, deadline)
            finally:
                response.close()
        require(report["fetch"]["status"] == 200 and not too_large and not timed_out,
                "Actual image fetch failed or is incomplete")
        require(raw.startswith(b"\x89PNG\r\n\x1a\n"), "Actual source is not a PNG")
        report["fetch"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        with tempfile.TemporaryDirectory(prefix="iptc-file-validation-") as temp:
            work = Path(temp)
            original = work / "original.png"
            original.write_bytes(raw)
            original_hash = digest(original)
            report["directory_checks"] = {}
            for label, checkout in (("baseline", base), ("candidate", root)):
                folder = work / label
                folder.mkdir()
                images = [folder / "first.png", folder / "second.png"]
                for image in images:
                    image.write_bytes(raw)
                before = [digest(image) for image in images]
                rc, result = cli(checkout, work, "inject", str(folder))
                changed = sum(digest(image) != value for image, value in zip(images, before, strict=True))
                report["directory_checks"][label] = {
                    "returncode": rc, "ok": result["ok"], "error": result["error"],
                    "changed_images": changed, "images": len(images),
                }
                if label == "baseline":
                    require(rc == 0 and result["ok"] and changed == 2,
                            "Baseline did not reproduce unintended directory-wide writes")
                    require(all(direct_read(image) == URI for image in images), "Baseline tags not written")
                else:
                    require(rc == 2 and not result["ok"] and result["error"] == "image-not-a-file" and changed == 0,
                            "Candidate directory injection changed an image or failed to reject the directory")

            dash = work / "-sample.png"
            dash.write_bytes(raw)
            old_rc, old_result = cli(base, work, "inject", "./-sample.png")
            require(old_rc == 2 and not old_result["ok"] and digest(dash) == original_hash,
                    "Baseline did not reproduce option-like filename failure")
            new_rc, new_result = cli(root, work, "inject", "./-sample.png")
            require(new_rc == 0 and new_result["ok"] and direct_read(dash) == URI,
                    "Candidate did not write the exact option-like filename")
            old_audit_rc, old_audit = cli(base, work, "audit", "./-sample.png")
            new_audit_rc, new_audit = cli(root, work, "audit", "./-sample.png")
            require(old_audit_rc == 0 and old_audit["summary"]["missing"] == 1,
                    "Baseline did not reproduce its false missing-tag result")
            require(new_audit_rc == 0 and new_audit["summary"][SOURCE_TYPE] == 1
                    and new_audit["images"][0]["source_type"] == URI,
                    "Candidate audit could not read back the exact image")
            report["leading_dash"] = {
                "baseline_write_rc": old_rc, "candidate_write_rc": new_rc,
                "baseline_missing_tags": old_audit["summary"]["missing"],
                "candidate_source_type": new_audit["images"][0]["source_type"],
                "audit_returncodes": [old_audit_rc, new_audit_rc],
            }

            folder_link = work / "directory-link.png"
            folder_link.symlink_to(work / "candidate", target_is_directory=True)
            before = [digest(p) for p in (work / "candidate").iterdir()]
            rc, result = cli(root, work, "inject", str(folder_link))
            require(rc == 2 and result["error"] == "image-not-a-file"
                    and before == [digest(p) for p in (work / "candidate").iterdir()],
                    "A directory symlink enabled a batch write")
            report["directory_symlink"] = {"returncode": rc, "error": result["error"], "images_changed": 0}

            observations = {}
            for label, checkout in (("baseline", base), ("candidate", root)):
                folder = work / (label + "-symlink")
                folder.mkdir()
                target, link = folder / "target.png", folder / "link.png"
                target.write_bytes(raw)
                link.symlink_to(target.name)
                rc, result = cli(checkout, folder, "inject", "link.png")
                observations[label] = {
                    "returncode": rc, "ok": result["ok"], "is_symlink_after": link.is_symlink(),
                    "link_source_type": direct_read(link), "target_source_type": direct_read(target),
                    "target_bytes_changed": digest(target) != original_hash,
                }
            require(observations["candidate"] == observations["baseline"],
                    "Candidate changed the existing ExifTool file-symlink behavior")
            report["file_symlink"] = observations

            pipe = work / "pipe.png"
            os.mkfifo(pipe)
            rc, result = cli(root, work, "inject", str(pipe))
            require(rc == 2 and result["error"] == "image-not-a-file", "Non-regular file was not rejected")
            report["non_regular_file"] = {"returncode": rc, "error": result["error"]}
            require(digest(original) == original_hash, "Unselected original image was modified")
            report["unselected_original_unchanged"] = True
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 - record and fail every verification error
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
