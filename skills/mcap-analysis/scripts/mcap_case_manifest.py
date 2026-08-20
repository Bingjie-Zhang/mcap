#!/usr/bin/env python3
"""Generate a read-only provenance manifest for one analysis case.

Records everything needed to reproduce or audit the analysis later:
recording hash/size/time-range, tool and registry versions, optional source
repo commit. Run this FIRST for every case; include the manifest in the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mcap_common import recording_bounds

SKILL_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def registry_version() -> str | None:
    candidates = sorted((SKILL_ROOT / "references").glob("*_topic_registry.yaml")) \
        if (SKILL_ROOT / "references").exists() else []
    if not candidates:
        return None
    registry = candidates[0]
    match = re.search(r'^registry_version:\s*"?([^"\n]+)"?', registry.read_text(), re.MULTILINE)
    return match.group(1).strip() if match else "unversioned"


def skill_version() -> str | None:
    for candidate in (SKILL_ROOT / "VERSION", SKILL_ROOT.parents[1] / "VERSION"):
        if candidate.exists():
            return candidate.read_text().strip()
    return None


def git_commit(repo: Path) -> dict[str, str | bool | None]:
    def run(*argv: str) -> str | None:
        proc = subprocess.run(["git", "-C", str(repo), *argv],
                              capture_output=True, text=True, timeout=30)
        return proc.stdout.strip() if proc.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "repo": str(repo),
        "commit": commit,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def mcap_package_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("mcap")
    except Exception:
        return None


def build_manifest(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    recording = args.recording
    stat = recording.stat()
    hash_started = time.monotonic()
    recording_sha256 = sha256_file(recording)
    hash_secs = round(time.monotonic() - hash_started, 3)
    try:
        start_secs, end_secs = recording_bounds(recording)
        bounds = {"start_secs": start_secs, "end_secs": end_secs,
                  "duration_s": round(end_secs - start_secs, 3)}
    except (ValueError, OSError) as exc:
        bounds = {"error": str(exc)}
    manifest = {
        "case_id": args.case_id,
        "analysis_profile": args.profile,
        "analysis_started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recording": {
            "path": str(recording),
            "sha256": recording_sha256,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            **bounds,
        },
        "tools": {
            "python": platform.python_version(),
            "mcap_package": mcap_package_version(),
            "skill_version": skill_version(),
            "registry_version": registry_version(),
        },
    }
    if args.repo:
        manifest["source"] = git_commit(args.repo)
    manifest["timing"] = {"total_secs": round(time.monotonic() - started, 3), "hash_secs": hash_secs}
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--case-id", required=True, help="Jira key or user-supplied case ID")
    parser.add_argument("--profile", default="quick", choices=["quick", "standard", "deep"])
    parser.add_argument("--repo", type=Path, help="Optional source repo for commit provenance (read-only)")
    args = parser.parse_args()
    if not args.recording.is_file():
        print(json.dumps({"error": f"recording not found: {args.recording}"}), file=sys.stderr)
        return 1
    print(json.dumps(build_manifest(args), ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
