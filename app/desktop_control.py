"""Private launch identity and idle shutdown for the Windows desktop controller."""
from __future__ import annotations

import hmac
import ipaddress
import os

from fastapi import APIRouter, HTTPException, Request


def attach(server):
    router = APIRouter()
    token = os.environ.get("STUDIO_DESKTOP_TOKEN", "")
    session = os.environ.get("STUDIO_DESKTOP_SESSION", "")
    backend = os.environ.get("STUDIO_DESKTOP_BACKEND", "")
    draining = False

    def authorize(request):
        if not token or not session or backend != "native":
            raise HTTPException(404, "This app was not started by the desktop controller.")
        try:
            local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            local = False
        if not local or request.headers.get("origin") or not hmac.compare_digest(
                request.headers.get("x-studio-desktop", "").encode(), token.encode()):
            raise HTTPException(403, "Desktop launch identity does not match.")

    def active():
        return any(job["status"] in ("queued", "running") for job in server.jobs.values())

    @router.get("/api/desktop/session")
    def status(request: Request):
        authorize(request)
        with server.lock:
            return {"session": session, "backend": backend, "busy": active(),
                    "stopping": server.stopping.is_set()}

    @router.post("/api/desktop/stop")
    def stop(request: Request):
        nonlocal draining
        authorize(request)
        with server.lock:
            if active():
                raise HTTPException(409, "Wait for queued and running jobs to finish before stopping or switching.")
            if draining:
                return {"stopping": True}
            shutdown = getattr(server.app.state, "desktop_shutdown", None)
            if shutdown is None:
                raise HTTPException(503, "Desktop shutdown is not ready.")
            server.stopping.set()
            draining = True
        try:
            shutdown()
            return {"stopping": True}
        except (OSError, RuntimeError) as error:
            with server.lock:
                draining = False
            raise HTTPException(503, "The owned backend did not drain; switching remains blocked. Retry Stop after checking the Studio log.") from error

    return router
