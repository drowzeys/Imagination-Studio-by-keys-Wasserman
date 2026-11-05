#!/usr/bin/env python3
"""Plan/run registered native recipe checks sequentially on the real app and export portable JSON."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "spark")]


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def call(path, body=None):
    url = "http://127.0.0.1:7870" + path
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as reply:
        return json.load(reply)


def selection(models, scopes, force, reference):
    import shared_measurements as shared
    import windows_measurements as windows
    from windows_audio_checks import digest
    results = []
    for model, scope, module, key in windows.recipes():
        if models and model not in models or scopes and scope not in scopes:
            continue
        why = module.prerequisites(model)
        if why:
            results.append({"model": model, "scope": scope, "state": "not_installed"})
            continue
        fp = module.fingerprint(model, scope) if module.__name__ == "windows_image_checks" else module.fingerprint(model)
        receipt = windows.evidence.saved(key, "")
        previous = receipt.get("fingerprint", {})
        hashes = {}
        for name, row in fp.get("models", {}).items():
            old = previous.get("models", {}).get(name)
            known = receipt.get("model_sha256", {}).get(name)
            if old == row and known:
                hashes[name] = known
            else:
                path = Path(name)
                if not path.is_absolute():
                    import windows_runtime
                    path = windows_runtime.model_root() / path
                hashes[name] = digest(path, row["size"], row["mtime_ns"])
        identity = shared.portable_identity(fp, hashes)
        hardware = windows.hardware(fp)
        execution = windows.execution_profile(model, fp)
        same = shared.complete_identity(identity) and any(
                   row["model"] == windows.model_id(model) and row["feature"] == model and row["scope"] == scope
                   and row["provenance_complete"] and row.get("identity", {}).get("sha256") == identity["sha256"]
                   and row.get("hardware") == hardware
                   and shared.compatible(row["execution"], execution)
                   for row in reference["profiles"] if row.get("identity") and row["outcome"] == "passed")
        identity_key = hashlib.sha256((model + "/" + scope + identity["sha256"]
                                      + json.dumps(hardware, sort_keys=True)
                                      + json.dumps(execution, sort_keys=True)).encode()).hexdigest()
        results.append({"model": model, "scope": scope, "identity": identity_key,
                        "state": "run" if force or not same else "unchanged"})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", help="Comma-separated registered native feature/model ids")
    parser.add_argument("--scopes", help="Optional comma-separated image scopes")
    parser.add_argument("--force-all", action="store_true", help="Rerun all selected installed recipes, even with matching reference evidence")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Explicitly allow GPU qualification jobs; otherwise plan only")
    parser.add_argument("--timeout-hours", type=float, default=24, help="Overall invocation deadline (default: 24 hours)")
    parser.add_argument("--export-only", action="store_true", help="Export existing receipts only, without hardware probes or inference")
    args = parser.parse_args(argv)
    if not 0 < args.timeout_hours <= 168:
        parser.error("--timeout-hours must be between zero and 168.")
    import windows_cuda
    windows_cuda.activate()
    import windows_media
    os.environ["PATH"] = windows_media.search_path(os.environ.get("PATH", ""))
    import shared_measurements as shared
    import windows_measurements as windows
    if args.export_only:
        save(args.output, windows.export())
        print("Exported saved observations; no inference or hardware probes.")
        return 0
    known = list(windows.recipes())
    models = set(args.models.split(",")) if args.models else set()
    scopes = set(args.scopes.split(",")) if args.scopes else set()
    if models - {row[0] for row in known} or scopes - {row[1] for row in known}:
        parser.error("Unknown model/feature or recipe scope.")
    reference = shared.load(args.reference) if args.reference else shared.load()
    plan = selection(models, scopes, args.force_all, reference)
    save(args.output, windows.export())
    for row in plan:
        print(row["model"] + "/" + row["scope"] + ": " + row["state"])
    if not args.run:
        print("Plan only. Add --run to queue actual qualification jobs. No models are downloaded.")
        return 0
    runstate = args.output.with_suffix(".resume.json")
    resume = json.loads(runstate.read_text()) if runstate.exists() else {"completed": [], "active": None}
    active_id = resume["active"]["job"] if resume.get("active") else None
    if any(job["status"] in ("queued", "running") and job["id"] != active_id for job in call("/api/jobs")):
        raise RuntimeError("The live queue is busy; no sweep jobs were submitted.")
    campaign_deadline = time.monotonic() + args.timeout_hours * 3600
    try:
        for row in plan:
            if row["state"] != "run" or row["identity"] in resume["completed"]:
                continue
            params = {"feature": row["model"]}
            if row["model"] in __import__("windows_image_checks").KINDS:
                params["scope"] = row["scope"]
            active = resume.get("active")
            if active and active["identity"] != row["identity"]:
                raise RuntimeError("A previous sweep job has a different identity. Inspect its live job before resuming.")
            if not active:
                job = call("/api/feature-checks/check", params)
                module = next(module for model, scope, module, _ in known if model == row["model"] and scope == row["scope"])
                limit = (module.timeout_seconds(row["model"], row["scope"]) + 300
                         if module.__name__ == "windows_image_checks" else module.TIMEOUT_S + 300)
                active = {"job": job["id"], "identity": row["identity"], "deadline": time.time() + limit}
                resume["active"] = active
                save(runstate, resume)
            while True:
                job = call("/api/jobs/" + active["job"])
                if job["status"] not in ("queued", "running"):
                    break
                if time.monotonic() >= campaign_deadline or time.time() >= active["deadline"]:
                    call("/api/jobs/" + active["job"] + "/cancel", {})
                    save(args.output, windows.export())
                    raise RuntimeError("Sweep deadline reached; owned job cancellation requested. Inspect Feature checks before resuming.")
                time.sleep(2)
            save(args.output, windows.export())
            resume["active"] = None
            if job["status"] == "done":
                resume["completed"].append(row["identity"])
            save(runstate, resume)
            if job["status"] != "done":
                raise RuntimeError("Sweep stopped on a failed/cancelled recipe; observations exported. Inspect Feature checks before resuming.")
    except KeyboardInterrupt:
        if resume.get("active"):
            call("/api/jobs/" + resume["active"]["job"] + "/cancel", {})
        save(args.output, windows.export())
        raise
    print("Sweep completed. Review the portable export before checking it in; local qualification remains separate.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
