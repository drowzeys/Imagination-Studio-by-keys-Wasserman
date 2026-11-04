"""Progressive qualification controls: explicit normal-queue jobs, never automatic test renders."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import agents
import windows_features as features


def parameters(params: dict) -> dict:
    if not isinstance(params, dict) or set(params) != {"feature"} or params["feature"] not in features.KINDS:
        raise HTTPException(400, "choose hlg or finish for this qualification check")
    return {"feature": params["feature"]}


def build(home, params, job_id):
    kind = parameters(params)["feature"]
    if why := features.prerequisites(kind):
        raise HTTPException(400, why)
    target = home / "exports" / f"{job_id}-qualification.json"
    return [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-features-check.py"),
            "--feature", kind, "--report", str(target)], {}, [str(target)]


def attach(server):
    router = APIRouter()

    def enabled(request, mutation=False):
        agents.local(request, mutation)
        if not server.WINDOWS:
            raise HTTPException(501, "These bounded checks are for the Windows preview; the native-tier guards are unchanged.")

    def snapshot():
        try:
            _, free = features.host_memory()
            with features.WindowsSignals() as signals:
                used, limit, _, low = signals.read()
            floor = max(0, int(os.environ.get("SPARK_MEMORY_FLOOR_MB") or 0))
            memory = {"host_free_mb": free, "commit_free_mb": limit - used, "low_memory": low,
                      "required_host_mb": max(floor, features.COMMIT_MB), "required_commit_mb": features.COMMIT_MB}
        except (OSError, ValueError) as error:
            memory = {"error": str(error)}
        cards = []
        with server.lock:
            current = [server.public(job) for job in server.jobs.values()
                       if job["kind"] == "qualification"]
        for kind in features.KINDS:
            found = features.status(kind)
            try:
                receipt = features.saved(kind, "")
                attempt = features.saved(kind, "-attempt")
                evidence = {"checked_at": receipt.get("checked_at"), "results": receipt.get("results", []),
                            "matches": found["qualified"]}
            except (OSError, ValueError) as error:
                attempt, evidence = {"error": str(error)}, {}
            jobs = [job for job in current if job["params"].get("feature") == kind]
            latest = max(jobs, key=lambda job: job["created"], default=None)
            cards.append({"id": kind, "title": features.TITLES[kind], **found,
                          "check_available": True, "open_tab": "Apps", "open_label": "Open HDR grading" if kind == "hlg" else "Open Finish",
                          "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Minimum installed RAM not established",
                                     "required_headroom_mb": features.COMMIT_MB, "basis": "Conservative admission requirement, not a measured minimum",
                                     "job_commit_cap_mb": features.COMMIT_MB, "gpu_required": False,
                                     "lowest_tested_ram_mb": 65536},
                          "evidence": evidence, "attempt": attempt, "job": latest,
                          "setup": (r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-finish.ps1"
                                    if kind == "finish" else r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-ffmpeg.ps1")})
        cards.append({"id": "foley", "title": "MMAudio foley", "available": False, "installed": False,
                      "qualified": False, "state": "not_qualified", "check_available": False,
                      "why": "Foley is not qualified for this Windows profile. Dependency setup and a guarded live check are still required.",
                      "scope": "Sound made for a clip's picture. MMAudio weights are non-commercial (CC-BY-NC 4.0).",
                      "next_step": "The pinned node pack exists upstream, but this profile has no runnable qualification check yet. Dependency setup must succeed before a live test can establish memory requirements.",
                      "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Minimum installed RAM not established",
                                 "required_headroom_mb": None, "basis": "No measured memory envelope for this Windows profile",
                                 "job_commit_cap_mb": None, "gpu_required": True, "lowest_tested_ram_mb": None},
                      "evidence": {}, "attempt": {}, "job": None, "setup": None})
        return {"features": cards, "memory": memory,
                "note": "Base workflows do not require these checks. Saved evidence is reused; checks only run when you ask."}

    @router.get("/api/windows/features")
    def state(request: Request):
        enabled(request)
        return snapshot()

    @router.get("/api/windows/features/report")
    def report(request: Request):
        enabled(request)
        return Response(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), **snapshot()}, indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="windows-feature-report.json"'})

    @router.post("/api/windows/features/check")
    def check(request: Request, body: dict):
        enabled(request, True)
        params = parameters(body)
        kind = params["feature"]
        with server.lock:
            for job in server.jobs.values():
                if job["kind"] == "qualification" and job["params"] == params and job["status"] in ("queued", "running"):
                    raise HTTPException(409, "This feature check is already queued or running.")
        return server.enqueue("qualification", "Check " + features.TITLES[kind], params)

    return router
