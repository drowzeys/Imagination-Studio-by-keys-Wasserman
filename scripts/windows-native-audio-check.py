#!/usr/bin/env python3
"""Explicit native Voice/Music qualification; never downloads or starts checks automatically."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import windows_cuda
windows_cuda.activate()
import windows_audio_checks as checks

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--feature", choices=checks.KINDS, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--job-id", default="")
parser.add_argument("--report", type=Path)
parser.add_argument("--worker", action="store_true")
args = parser.parse_args()
try:
    if args.worker:
        checks.worker(args.feature, args.output)
    else:
        if args.report is None:
            parser.error("--report is required")
        receipt = checks.qualify(args.feature, args.output, args.job_id)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print("[qualification] Native audio recipe passed. Longer or reference-based recipes were not checked.", flush=True)
except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
    print(str(error), file=sys.stderr)
    raise SystemExit(1) from None
