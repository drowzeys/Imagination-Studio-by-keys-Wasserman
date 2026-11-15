"""Optional operator-installed UI scripts; no dictionaries or downloads are bundled."""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

LANG = re.compile(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*")


def attach(home: Path):
    router = APIRouter(prefix="/api/languages")
    root = home / "i18n"

    def local(language):
        if len(language) > 48 or not LANG.fullmatch(language) or language.lower() == "en" or root.is_symlink():
            return None
        path = root / (language + ".js")
        return path if path.is_file() and not path.is_symlink() else None

    @router.get("")
    def listing():
        files = sorted(root.glob("*.js")) if root.is_dir() and not root.is_symlink() else []
        return [{"id": "en", "label": "English"}] + [{"id": p.stem, "label": p.stem} for p in files if local(p.stem)]

    @router.get("/{language}.js")
    def script(language: str):
        path = local(language)
        if path is None:
            raise HTTPException(404, "no such local language")
        return FileResponse(path, media_type="text/javascript", headers={"Cache-Control": "no-store"})

    return router
