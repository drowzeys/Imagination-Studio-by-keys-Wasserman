"""Run an existing baseline check as explicit, owned qualification queue work."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import windows_baseline_checks as baseline
import windows_features as features
import windows_media
from windows_process import OwnedJob


def check(kind: str, output: Path) -> dict:
    if why := baseline.prerequisites(kind):
        raise RuntimeError(why)
    if output.exists():
        raise ValueError("Choose a new qualification output folder.")
    graphics_check = kind == "graphics"
    if graphics_check:
        features.headroom(kind)
    owned = OwnedJob(job_commit_mb=4096 if graphics_check else 16384)
    try:
        with features.watch(owned) if graphics_check else contextlib.nullcontext():
            process = owned.spawn(baseline.command(kind, output), stdin=subprocess.DEVNULL,
                                  env={**os.environ, "PATH": windows_media.search_path(os.environ.get("PATH", ""))})
            if process.wait(timeout=550):
                raise RuntimeError(f"The {kind} check failed; see its engine error in the job log.")
    finally:
        owned.close()
    found = baseline.status(kind)
    if not found["qualified"]:
        raise RuntimeError(found["why"] or "The completed check did not establish qualification.")
    receipt = {"ok": True, "feature": kind, **baseline.evidence(kind, True),
               "checked_at": datetime.now(timezone.utc).isoformat(), "time_basis": "check"}
    features.save_pass(kind, receipt)
    features.save_attempt(kind, {"ok": True, "finished": receipt["checked_at"]})
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature", choices=baseline.KINDS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    os.environ["PATH"] = windows_media.search_path(os.environ.get("PATH", ""))
    try:
        result = check(args.feature, args.output)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        features.save_attempt(args.feature, {"ok": False, "error": str(error), "state": "failed",
                                           "finished": datetime.now(timezone.utc).isoformat()})
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
