"""Progressive qualification controls: explicit normal-queue jobs, never automatic test renders."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import agents
from feature_checks import FeatureCheck, MemoryRequirement
import windows_features as features
import windows_foley

CHECKS = {
    "hlg": FeatureCheck("hlg", "HDR grading", "SDR-to-HLG grade and SDR copy; up to ten seconds, 1280x720, 24 fps.",
                        (MemoryRequirement("host", features.COMMIT_MB, "policy"), MemoryRequirement("commit", features.COMMIT_MB, "policy")), group="edit"),
    "finish": FeatureCheck("finish", "Tesseract Finish", "720p, 24 fps; up to 12 cuts and ten seconds including a title; no transcription or editor conversion.",
                           (MemoryRequirement("host", features.COMMIT_MB, "policy"), MemoryRequirement("commit", features.COMMIT_MB, "policy")), group="edit"),
    "foley": FeatureCheck("foley", "MMAudio foley", "Sound made for a clip's picture. MMAudio weights are non-commercial (CC-BY-NC 4.0).",
                          (MemoryRequirement("host", windows_foley.HOST_REQUIRED_MB, "estimated"),
                           MemoryRequirement("device", 12288, "policy", 2048),
                           MemoryRequirement("commit", 16384, "policy", 2048)), group="sound"),
}


def discovery(supported: bool) -> dict:
    return {"supported": supported, "url": "/api/feature-checks",
            "features": {kind: check.title for kind, check in CHECKS.items()} if supported else {}}


def parameters(params: dict) -> dict:
    if not isinstance(params, dict) or set(params) != {"feature"} or params["feature"] not in CHECKS:
        raise HTTPException(400, "choose a registered feature for this qualification check")
    return {"feature": params["feature"]}


def build(home, params, job_id):
    kind = parameters(params)["feature"]
    if kind == "foley":
        found = windows_foley.status()
        if not found["installed"]:
            raise HTTPException(400, found["why"])
        target = home / "exports" / f"{job_id}-qualification.json"
        return [str(windows_foley.gpu_python()), "-X", "utf8", str(features.PACK / "app/windows_foley.py"),
                "--qualify", "--output", str(home / "renders/calibration" / job_id),
                "--report", str(target)], {}, [str(target)]
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
            raise HTTPException(501, "Feature checks are not implemented for this runtime profile; existing model guards are unchanged.")

    def snapshot():
        foley = windows_foley.status()
        try:
            _, free = features.host_memory()
            with features.WindowsSignals() as signals:
                used, limit, _, low = signals.read()
            floor = max(0, int(os.environ.get("SPARK_MEMORY_FLOOR_MB") or 0))
            memory = {"host_free_mb": free, "commit_free_mb": limit - used, "low_memory": low,
                      "required_host_mb": max(floor, features.COMMIT_MB), "required_commit_mb": features.COMMIT_MB,
                      "pools": {"host": {"label": "Host RAM", "available_mb": free},
                                "commit": {"label": "Commit backing", "available_mb": limit - used}}}
        except (OSError, ValueError) as error:
            memory = {"error": str(error)}
        if "pools" in memory:
            device_pool = {"label": "Dedicated GPU", "available_mb": None}
            if foley.get("installed"):
                try:
                    with windows_foley.DedicatedMemory(windows_foley.device_uuid()) as device:
                        _, device_pool["available_mb"] = device.read()
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    device_pool["reason"] = str(error)
            memory["pools"]["device"] = device_pool
        cards = []
        with server.lock:
            current = [server.public(job) for job in server.jobs.values()
                       if job["kind"] == "qualification"]
        for kind in CHECKS:
            found = foley if kind == "foley" else features.status(kind)
            try:
                receipt = features.saved(kind, "")
                attempt = features.saved(kind, "-attempt")
                evidence = {"checked_at": receipt.get("checked_at"), "results": receipt.get("results", []),
                            "matches": found["qualified"]}
            except (OSError, ValueError) as error:
                attempt, evidence = {"error": str(error)}, {}
            jobs = [job for job in current if job["params"].get("feature") == kind]
            latest = max(jobs, key=lambda job: job["created"], default=None)
            spec = CHECKS[kind].public()
            minimum_host = windows_foley.HOST_REQUIRED_MB if kind == "foley" else features.COMMIT_MB
            spec["requirements"][0]["required_mb"] = max(minimum_host, memory.get("required_host_mb", minimum_host))
            cards.append({**spec, **found,
                          "check_available": True, "open_tab": "Apps" if kind == "hlg" else "Foley" if kind == "foley" else "Finish",
                          "open_query": "keys-HDR" if kind == "hlg" else "", "open_category": "finish",
                          "open_label": "Open HDR grading" if kind == "hlg" else "Open Foley" if kind == "foley" else "Open Finish",
                          "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Minimum installed RAM not established",
                                     "required_headroom_mb": minimum_host, "basis": "Conservative admission requirement, not a measured minimum",
                                     "job_commit_cap_mb": 16384 if kind == "foley" else features.COMMIT_MB, "gpu_required": kind == "foley",
                                     "lowest_tested_ram_mb": 65536},
                          "evidence": evidence, "attempt": attempt, "job": latest,
                          "setup": (r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-foley.ps1" if kind == "foley" else
                                    r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-finish.ps1" if kind == "finish" else
                                    r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-ffmpeg.ps1")})
        return {"features": cards, "memory": memory,
                "note": "Base workflows do not require these checks. Saved evidence is reused; checks only run when you ask."}

    @router.get("/api/feature-checks")
    @router.get("/api/windows/features")
    def state(request: Request):
        enabled(request)
        return snapshot()

    @router.get("/api/feature-checks/report")
    @router.get("/api/windows/features/report")
    def report(request: Request):
        enabled(request)
        return Response(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), **snapshot()}, indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="feature-check-report.json"'})

    @router.post("/api/feature-checks/check")
    @router.post("/api/windows/features/check")
    def check(request: Request, body: dict):
        enabled(request, True)
        params = parameters(body)
        kind = params["feature"]
        with server.lock:
            for job in server.jobs.values():
                if job["kind"] == "qualification" and job["params"] == params and job["status"] in ("queued", "running"):
                    raise HTTPException(409, "This feature check is already queued or running.")
        return server.enqueue("qualification", "Check " + CHECKS[kind].title, params)

    return router
