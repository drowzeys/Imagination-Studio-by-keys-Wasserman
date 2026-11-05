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
from feature_checks import FeatureCheck, MemoryRequirement, strict_matching
import windows_features as features
import windows_foley
import windows_baseline_checks as baseline
import windows_runtime
import windows_cuda
import windows_image_checks as images
import windows_audio_checks as audio
import windows_setup_fit
import windows_library_checks as library

CHECKS = {
    **{kind: FeatureCheck(kind, library.TITLES[kind],
                         "Production Library workflow on pinned bundled media; only its tested input/settings are enabled.",
                         tuple(MemoryRequirement(pool, None, "unknown") for pool in ("host", "device", "commit")),
                         group="edit") for kind in library.KINDS},
    "voice": FeatureCheck("voice", "Fish Audio Drama 3", "Short plain speech, up to 20 words / 120 characters, no cloning, streaming or transcription.",
                          tuple(MemoryRequirement(pool, None, "unknown") for pool in ("host", "device", "commit")), group="sound"),
    "music": FeatureCheck("music", "MiniMax-Music3", "10-second instrumental, native CUDA int8 engine, default sampling; no streaming or transcription.",
                          tuple(MemoryRequirement(pool, None, "unknown") for pool in ("host", "device", "commit")), group="sound"),
    **{model: FeatureCheck(model, spec[0], "Production native image workflow; qualify canvases and counts separately.",
                          tuple(MemoryRequirement(pool, None, "unknown") for pool in ("host", "device", "commit")), group="image")
       for model, spec in windows_runtime.IMAGE_SPECS.items()},
    "hlg": FeatureCheck("hlg", "HDR grading", "SDR-to-HLG grade and SDR copy; up to ten seconds, 1280x720, 24 fps.",
                        (MemoryRequirement("host", features.COMMIT_MB, "policy"), MemoryRequirement("commit", features.COMMIT_MB, "policy")), group="edit"),
    "finish": FeatureCheck("finish", "Tesseract Finish", "720p, 24 fps; up to 12 cuts and ten seconds including a title; no transcription or editor conversion.",
                           (MemoryRequirement("host", features.COMMIT_MB, "policy"), MemoryRequirement("commit", features.COMMIT_MB, "policy")), group="edit"),
    "foley": FeatureCheck("foley", "MMAudio foley", "Sound made for a clip's picture. MMAudio weights are non-commercial (CC-BY-NC 4.0).",
                          (MemoryRequirement("host", windows_foley.HOST_REQUIRED_MB, "estimated"),
                           MemoryRequirement("device", 12288, "policy", 2048),
                           MemoryRequirement("commit", 16384, "policy", 2048)), group="sound"),
    "scout": FeatureCheck("scout", "SD-Turbo storyboard drafts", "Six sequential 512x512 one-step drafts; no video or generative edits.",
                          (MemoryRequirement("host", None, "unknown"), MemoryRequirement("device", 12288, "policy", 2048),
                           MemoryRequirement("commit", 16384, "policy")), group="image"),
    "sfx": FeatureCheck("sfx", "Stable Audio Open effects", "Five seconds, 100 passes, CFG 7; stereo 44.1 kHz sound from words.",
                        (MemoryRequirement("host", None, "unknown"), MemoryRequirement("device", 12288, "policy", 2048),
                         MemoryRequirement("commit", 16384, "policy")), group="sound"),
    "graphics": FeatureCheck("graphics", "Motion graphics", "Six templates at 1280x720, 24 fps, ten seconds; private headless browser.",
                             (MemoryRequirement("host", 4096, "policy"), MemoryRequirement("commit", 4096, "policy")),
                             group="graphics"),
}


def gpu_check(job: dict) -> bool:
    return (native_image_check(job) or job["kind"] == "foley"
            or job["kind"] == "qualification" and job["params"].get("feature") in ("foley", *baseline.GPU_KINDS))


def native_image_check(job: dict) -> bool:
    return job["kind"] == "qualification" and job["params"].get("feature") in (*images.KINDS, *audio.KINDS, *library.KINDS)


def timeout_seconds(job: dict) -> int:
    if native_image_check(job):
        params = job["params"]
        if params["feature"] in library.KINDS:
            return library.TIMEOUT_S + 180
        if params["feature"] in audio.KINDS:
            return audio.TIMEOUT_S + 120
        scopes = images.GROUPS[params["group"]][1] if params.get("group") in images.GROUPS else (params.get("scope", "basic"),)
        return sum(images.timeout_seconds(params["feature"], scope) + 120 for scope in scopes)
    return 600


def save_outcome(job: dict) -> None:
    kind = job["params"]["feature"]
    previous = features.saved(kind, "-attempt")
    same_job = previous.get("job_id") == job["id"]
    saved = previous if same_job else {}
    error = saved.get("error") if job["status"] == "failed" else None
    if error:
        job["error"] = error
    features.save_attempt(kind, {**saved, "job_id": job["id"], "ok": False, "state": job["status"],
                                 "finished": job["finished"], "error": error or job.get("error") or "The check was cancelled."})
    scope = saved.get("scope") or job["params"].get("scope")
    if kind in images.KINDS and scope in images.SCOPES:
        features.save_attempt(f"{kind}-{scope}", features.saved(kind, "-attempt"))


def discovery(supported: bool) -> dict:
    return {"supported": supported, "url": "/api/feature-checks",
            "features": ({**{kind: check.title for kind, check in CHECKS.items()},
                          **{kind: spec[0] for kind, spec in windows_runtime.IMAGE_SPECS.items()},
                          "video": "H3 / FastH3 video", "voice": "Fish Audio Drama 3", "music": "MiniMax-Music3"} if supported else {}),
            "mode": windows_runtime.mode() if supported else None}


def parameters(params: dict) -> dict:
    if not isinstance(params, dict) or "feature" not in params or params["feature"] not in CHECKS:
        raise HTTPException(400, "choose a registered feature for this qualification check")
    if params["feature"] in images.KINDS:
        if set(params) - {"feature", "scope", "group"} or "scope" in params and "group" in params:
            raise HTTPException(400, "Choose a registered image qualification level.")
        if "group" in params:
            if not isinstance(params["group"], str) or params["group"] not in images.GROUPS:
                raise HTTPException(400, "Choose a registered image qualification group.")
            return {"feature": params["feature"], "group": params["group"]}
        try:
            scope = images.scope_settings(params.get("scope", "basic"))["id"]
        except (ValueError, TypeError):
            raise HTTPException(400, "Choose a registered image qualification level.") from None
        return {"feature": params["feature"], "scope": scope}
    if set(params) != {"feature"}:
        raise HTTPException(400, "choose a registered feature for this qualification check")
    return {"feature": params["feature"]}


def build(home, params, job_id):
    params = parameters(params)
    kind = params["feature"]
    if kind in library.KINDS:
        if why := library.prerequisites(kind):
            raise HTTPException(400, why)
        target = home / "exports" / f"{job_id}-qualification.json"
        return [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-library-check.py"),
                "--feature", kind, "--job-id", job_id, "--output", str(home / "renders" / "calibration" / job_id),
                "--report", str(target)], {}, [str(target)]
    if kind in audio.KINDS:
        if why := audio.prerequisites(kind):
            raise HTTPException(400, why)
        target = home / "exports" / f"{job_id}-qualification.json"
        return [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-native-audio-check.py"),
                "--feature", kind, "--job-id", job_id, "--output", str(home / "renders" / "calibration" / job_id),
                "--report", str(target)], {}, [str(target)]
    if kind in images.KINDS:
        if why := images.prerequisites(kind):
            raise HTTPException(400, why)
        target = home / "exports" / f"{job_id}-qualification.json"
        return [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-native-image-check.py"),
                "--model", kind, *(("--group", params["group"]) if "group" in params else ("--scope", params["scope"])),
                "--job-id", job_id,
                "--output", str(home / "renders" / "calibration" / job_id),
                "--report", str(target)], {}, [str(target)]
    if kind in baseline.KINDS:
        if why := baseline.prerequisites(kind):
            raise HTTPException(400, why)
        target = home / "exports" / f"{job_id}-qualification.json"
        return [sys.executable, "-X", "utf8", str(features.PACK / "scripts" / "windows-baseline-check.py"),
                "--feature", kind, "--output", str(home / "renders" / "calibration" / job_id),
                "--report", str(target)], {}, [str(target)]
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
        if not (server.WINDOWS or server.WIN32):
            raise HTTPException(501, "Feature checks are not implemented for this runtime profile; existing model guards are unchanged.")

    def snapshot():
        selected_mode = windows_runtime.mode()
        foley = windows_runtime.usage_status("foley", selected_mode=selected_mode)
        try:
            _, free = features.host_memory()
            with features.WindowsSignals() as signals:
                used, limit, _, low = signals.read()
            floor = max(0, int(os.environ.get("SPARK_MEMORY_FLOOR_MB") or 0))
            memory = {"host_free_mb": free, "commit_free_mb": limit - used, "low_memory": low, "host_floor_mb": floor,
                      "required_host_mb": max(floor, features.COMMIT_MB), "required_commit_mb": features.COMMIT_MB,
                      "pools": {"host": {"label": "Host RAM", "available_mb": free},
                                "commit": {"label": "Commit backing", "available_mb": limit - used}}}
        except (OSError, ValueError) as error:
            memory = {"error": str(error)}
        if "pools" in memory:
            device_pool = {"label": "Dedicated GPU", "available_mb": None}
            if windows_cuda.active() or foley.get("installed") or any(baseline.status(kind)["installed"] for kind in baseline.GPU_KINDS):
                try:
                    from windows_memory import WddmMemory, single_gpu_uuid
                    _, device_pool["available_mb"] = WddmMemory(single_gpu_uuid()).read()
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                    device_pool["reason"] = str(error)
            memory["pools"]["device"] = device_pool
        cards = []
        with server.lock:
            current = [server.public(job) for job in server.jobs.values()
                       if job["kind"] == "qualification"]
        for kind in CHECKS:
            if kind in (*images.KINDS, *audio.KINDS, *library.KINDS):
                continue
            found = foley if kind == "foley" else windows_runtime.usage_status(kind, selected_mode=selected_mode)
            try:
                attempt = features.saved(kind, "-attempt")
                if kind in baseline.KINDS:
                    evidence = baseline.evidence(kind, found["qualified"])
                else:
                    receipt = features.saved(kind, "")
                    evidence = {"checked_at": receipt.get("checked_at"), "results": receipt.get("results", []),
                                "matches": found["qualified"]}
            except (OSError, ValueError, KeyError, TypeError) as error:
                attempt, evidence = {"error": str(error)}, {}
            jobs = [job for job in current if job["params"].get("feature") == kind]
            latest = max(jobs, key=lambda job: job["created"], default=None)
            spec = CHECKS[kind].public()
            minimum_host = spec["requirements"][0]["required_mb"]
            if minimum_host is not None:
                spec["requirements"][0]["required_mb"] = max(minimum_host, memory.get("host_floor_mb", 0))
            destination = {"scout": ("Image", "Open storyboard drafts"), "sfx": ("Foley", "Open sound effects"),
                           "graphics": ("Motion graphics", "Open motion graphics"), "hlg": ("Apps", "Open HDR grading"),
                           "foley": ("Foley", "Open Foley"), "finish": ("Finish", "Open Finish")}[kind]
            cards.append({**spec, **found,
                          "check_available": True, "open_tab": destination[0], "open_mode": "sfx" if kind == "sfx" else "foley" if kind == "foley" else "",
                          "open_query": "keys-HDR" if kind == "hlg" else "", "open_category": "finish",
                          "open_label": destination[1],
                          "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Minimum installed RAM not established",
                                     "required_headroom_mb": minimum_host, "basis": "Conservative admission requirement, not a measured minimum",
                                     "job_commit_cap_mb": 16384 if kind in ("foley", *baseline.GPU_KINDS) else features.COMMIT_MB,
                                     "gpu_required": kind in ("foley", *baseline.GPU_KINDS),
                                     "lowest_tested_ram_mb": found.get("hardware", {}).get("physical_total_mb") if kind in baseline.KINDS else 65536},
                          "evidence": evidence, "attempt": attempt, "job": latest,
                          "setup": (rf"powershell -ExecutionPolicy Bypass -File scripts\{baseline.SETUP[kind]}" if kind in baseline.KINDS else
                                    r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-foley.ps1" if kind == "foley" else
                                    r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-finish.ps1" if kind == "finish" else
                                    r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-ffmpeg.ps1")})
        for choice in windows_runtime.image_options(selected_mode):
            if choice["id"] == "sd-turbo":
                continue
            implemented = choice["id"] in images.KINDS
            jobs = [job for job in current if job["params"].get("feature") == choice["id"]]
            try:
                receipts = [level["evidence"] for level in choice.get("qualification_levels", []) if level["qualified"]]
                receipt = {"results": [result for saved in receipts for result in saved.get("results", [])],
                           "checked_at": max((saved["checked_at"] for saved in receipts), default=None)}
                attempt = features.saved(choice["id"], "-attempt") if implemented else {}
            except (OSError, ValueError, TypeError) as error:
                receipt, attempt = {}, {"error": str(error)}
            cards.append({**choice, "group": "image", "check_available": implemented,
                          **windows_setup_fit.model_info(choice["id"], installed=choice["installed"], evidence=choice),
                          "open_tab": "Create", "open_model": choice["id"], "open_label": "Open " + choice["title"],
                          "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Windows memory requirement not established",
                                     "lowest_tested_ram_mb": choice.get("hardware", {}).get("physical_total_mb"),
                                     "required_headroom_mb": None, "job_commit_cap_mb": None,
                                     "gpu_required": True, "basis": choice["measurement_source"]},
                          "evidence": {"matches": choice["qualified"], "results": receipt.get("results", []),
                                       "checked_at": receipt.get("checked_at")},
                          "attempt": attempt,
                          "job": max(jobs, key=lambda job: job["created"], default=None)})
        for kind in library.KINDS:
            found = library.usage_status(kind, selected_mode)
            try:
                receipt = features.saved(kind, "")
                attempt = features.saved(kind, "-attempt")
            except (OSError, ValueError, TypeError) as error:
                receipt, attempt = {}, {"error": str(error)}
            cards.append({**CHECKS[kind].public(), **found, "check_available": True, "open_tab": "Library",
                          "open_label": "Open Library", "backend": "native", "recipe": library.recipe(kind),
                          "scope": library.scope_label(kind),
                          "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Windows recipe requirement not established",
                                     "lowest_tested_ram_mb": found.get("hardware", {}).get("physical_total_mb"),
                                     "gpu_required": True, "job_commit_cap_mb": None},
                          "evidence": {"matches": found["qualified"], "checked_at": receipt.get("checked_at"),
                                       "results": receipt.get("results", [])}, "attempt": attempt,
                          "job": max((job for job in current if job["params"].get("feature") == kind),
                                     key=lambda job: job["created"], default=None),
                          "setup": windows_cuda.setup_hint("swap,h3-shared" if kind == "swap" else
                                                           "workflows" if kind == "mask" else
                                                           "workflows,library-h3,h3-shared")})
        video = windows_runtime.native_status("video", selected_mode)
        cards.append({"id": "video", "title": "H3 / FastH3 video", "group": "video", **video,
                      "scope": f"The existing H3 policy requires a measured CUDA allocation pool of at least {windows_cuda.MIN_POOL_MB} MiB. This is not a minimum installed-RAM claim.",
                      "requirements": [], "check_available": False,
                      "memory": {"minimum_installed_ram_mb": None, "minimum_label": "See the H3 CUDA-pool policy",
                                 "lowest_tested_ram_mb": None, "job_commit_cap_mb": None, "gpu_required": True},
                      "evidence": {"matches": False, "results": []}, "attempt": {}, "job": None,
                      "setup": r"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-cuda.ps1 -Parts video",
                      "open_tab": "Video", "open_label": "Open video"})
        import voice
        import music
        for kind, title, raw, part, tab in (
                ("voice", "Fish Audio Drama 3", voice.state(), "voice -Voice", "Voice"),
                ("music", "MiniMax-Music3", music.state(), "music", "Music")):
            found = windows_runtime.gate_native_state(kind, raw, selected_mode)
            cards.append({"id": kind, "title": title, "group": "sound", **found,
                          **windows_setup_fit.model_info(kind, installed=found["installed"]),
                          "scope": CHECKS[kind].scope,
                          "requirements": [{"pool": "device", "required_mb": windows_setup_fit.requirement(kind)["required_mb"],
                                            "reserve_mb": 0, "basis": "policy"},
                                           {"pool": "host", "required_mb": None, "reserve_mb": 0, "basis": "unknown"},
                                           {"pool": "commit", "required_mb": None, "reserve_mb": 0, "basis": "unknown"}],
                          "check_available": True,
                          "memory": {"minimum_installed_ram_mb": None, "minimum_label": "Installed RAM alone does not establish fit",
                                     "basis": f"Existing native engine admission: about {windows_setup_fit.requirement(kind)['required_mb'] / 1024:g} GiB free GPU pool. Not a minimum installed-RAM claim.",
                                     "lowest_tested_ram_mb": None, "job_commit_cap_mb": None, "gpu_required": True},
                          "evidence": {"matches": found["qualified"], "results": features.saved(kind, "").get("results", []),
                                       "checked_at": features.saved(kind, "").get("checked_at")},
                          "attempt": features.saved(kind, "-attempt"),
                          "job": max((job for job in current if job["params"].get("feature") == kind),
                                     key=lambda job: job["created"], default=None),
                          "setup": rf"powershell -ExecutionPolicy Bypass -File scripts\setup-windows-cuda.ps1 -Parts {part}",
                          "open_tab": tab, "open_label": "Open " + title})
        return {"features": cards, "memory": memory, "mode": selected_mode, "policy": "strict" if strict_matching() else "balanced",
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

    @router.post("/api/feature-checks/mode")
    def set_mode(request: Request, body: dict):
        enabled(request, True)
        if set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise HTTPException(400, "Choose whether qualification gating is enabled.")
        with server.lock:
            if any(job["status"] in ("queued", "running") for job in server.jobs.values()):
                raise HTTPException(409, "Wait for queued work to finish before changing qualification gating.")
            try:
                selected_mode = windows_runtime.set_mode(body["enabled"])
            except ValueError as error:
                raise HTTPException(409, str(error)) from None
        return {"mode": selected_mode}

    @router.post("/api/feature-checks/check")
    @router.post("/api/windows/features/check")
    def check(request: Request, body: dict):
        enabled(request, True)
        params = parameters(body)
        kind = params["feature"]
        with server.lock:
            for job in server.jobs.values():
                if (job["kind"] == "qualification" and job["params"].get("feature") == kind
                        and job["status"] in ("queued", "running")):
                    raise HTTPException(409, "This feature check is already queued or running.")
        return server.enqueue("qualification", "Check " + CHECKS[kind].title, params)

    return router
