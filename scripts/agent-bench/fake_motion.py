#!/usr/bin/env python3
"""Motion Previs's analysis stand-in for the agent benchmark (dry_studio.py sets MOTION_PYTHON to this file): asked
`--probe`, it says everything is there; given a job, it writes a pack's manifest and says it finished."""
import json
import sys
import time

args = [a for a in sys.argv[1:] if a != "-u"]
if "--probe" in args:
    print(json.dumps({"python": sys.executable, "ffmpeg": True, "pose": True, "camera": True, "ai_depth": True, "pose_models": [], "why": {}}))
    sys.exit(0)
spec = json.loads(open(args[-1]).read())
time.sleep(float(spec.get("bench_seconds") or 2))
folder = spec["folder"]
open(folder + "/bundle_manifest.json", "w").write(json.dumps({"bench": "dry run", "source": spec.get("source"), "mode": spec.get("mode")}))
print("@@" + json.dumps({"finished": True, "timings": {}, "memory": None}), flush=True)
