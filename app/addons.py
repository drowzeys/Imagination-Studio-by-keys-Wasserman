"""Opt-in local extensions. Modules here are code installed and trusted by the operator, not a sandbox."""
from __future__ import annotations

import importlib.util
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute

ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")
log = logging.getLogger(__name__)


class Addon:
    def __init__(self, name: str, home: Path, queue):
        self.id, self.home, self.queue = name, home, queue
        self.router = APIRouter()
        self.agents = []

    def agent(self, id: str, label: str, command: str, *, ask, argv=None, read=None,
              session=False, signin="Sign in with this CLI in a terminal.", hints=()):
        """An adapter uses the existing agent worker, stream, work folder and Studio MCP tools."""
        if not ID.fullmatch(id) or any(not isinstance(value, str) or not value.strip() for value in (label, command, signin)) or not callable(ask):
            raise ValueError("an agent needs an id, label, command and ask callback")
        if (argv is None) != (read is None) or argv is not None and not (callable(argv) and callable(read)):
            raise ValueError("a harness needs both argv and read callbacks")
        if session and argv is None:
            raise ValueError("a session needs a harness adapter")
        hints = [(re.compile(pattern, re.I).pattern, text) for pattern, text in hints]
        self.agents.append(dict(id=id, label=label, command=command, ask=ask, argv=argv, read=read,
                                session=bool(session), signin=signin, hints=hints))


def attach(app, home: Path, queue):
    """Called once before the static mount. No folder is scanned unless explicitly enabled."""
    import agents

    loaded = {}
    if os.environ.get("STUDIO_ADDONS") == "1":
        root = home / "addons"
        for folder in sorted(root.iterdir()) if root.is_dir() and not root.is_symlink() else []:
            if not ID.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                addon = Addon(folder.name, home, queue)
                module_path = folder / "server.py"
                if module_path.exists():
                    if module_path.is_symlink():
                        raise ValueError("server.py must be a local file")
                    spec = importlib.util.spec_from_file_location("studio_addon_" + folder.name, module_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    module.register(addon)
                if any(not isinstance(route, APIRoute) for route in addon.router.routes):
                    raise ValueError("add-on routers support HTTP routes only")
                names = [a["id"] for a in addon.agents]
                if len(set(names)) != len(names) or any(name in agents.BACKENDS for name in names):
                    raise ValueError("an add-on cannot replace an agent id")
                # Validate every registration before committing any: a broken add-on leaves no partial backend.
                for a in addon.agents:
                    agents.register_addon(a)
                app.include_router(addon.router, prefix="/api/addons/" + addon.id)
                loaded[addon.id] = (folder.resolve(), addon)
            except Exception:
                log.warning("Add-on %s was not loaded", folder.name, exc_info=False)

    @app.get("/api/addons")
    def listing():
        return [{"id": name, "script": f"/api/addons/{name}/assets/client.js" if (folder / "client.js").is_file() else None,
                 "agents": [{k: a[k] for k in ("id", "label", "session")} | {"harness": a["argv"] is not None}
                            for a in addon.agents]} for name, (folder, addon) in loaded.items()]

    @app.get("/api/addons/{name}/assets/{path:path}")
    def asset(name: str, path: str):
        if name not in loaded:
            raise HTTPException(404, "no such add-on")
        folder = loaded[name][0]
        target = (folder / path).resolve()
        if folder not in target.parents or any(p.startswith(".") for p in Path(path).parts) or target.suffix not in (".js", ".css", ".svg", ".png") or not target.is_file():
            raise HTTPException(404, "no such add-on asset")
        return FileResponse(target)
