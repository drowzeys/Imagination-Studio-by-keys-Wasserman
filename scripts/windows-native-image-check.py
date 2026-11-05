#!/usr/bin/env python3
"""Run an explicit native Windows image recipe check, without downloading models."""
import argparse
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_cuda
windows_cuda.activate()
import windows_image_checks

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model", choices=windows_image_checks.KINDS, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--job-id", default="")
selection = parser.add_mutually_exclusive_group()
selection.add_argument("--scope", choices=windows_image_checks.SCOPES, default="basic")
selection.add_argument("--group", choices=windows_image_checks.GROUPS)
args = parser.parse_args()
try:
    report = (windows_image_checks.qualify_group(args.model, args.output, args.job_id, args.group) if args.group else
              windows_image_checks.qualify(args.model, args.output, args.job_id, args.scope))
except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1) from None
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
if not report["ok"]:
    print(report["error"], file=sys.stderr)
    raise SystemExit(1)
if args.group:
    print("[qualification] Group finished. Each recipe's existing or new pass is saved independently.", flush=True)
    raise SystemExit(0)
settings = windows_image_checks.scope_settings(args.scope)
print(f"[qualification] Passed: {settings['width']}x{settings['height']}, {settings['count']} image(s). Other levels are unchanged.", flush=True)
