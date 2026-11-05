#!/usr/bin/env python3
"""Refresh checked-in download byte sizes from public HF metadata, never download model payloads."""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import model_catalog


def refresh():
    data = json.loads(model_catalog.PATH.read_text(encoding="utf-8"))
    sources = {}
    for rows in data["downloads"].values():
        for row in rows:
            sources.setdefault((row["repo"], row.get("revision", "main")), set()).update(row["files"])
    for rows in data.get("checked_downloads", {}).values():
        for row in rows:
            sources.setdefault((row["repo"], row["revision"]), set()).add(row["file"])
    for row in data["checkpoints"].values():
        sources[(row["repo"], row["revision"])] = None
    sizes = {}
    for (repo, revision), names in sources.items():
        url = "https://huggingface.co/api/models/" + repo + "/revision/" + urllib.parse.quote(revision, safe="") + "?blobs=true"
        with urllib.request.urlopen(url, timeout=30) as response:
            metadata = json.load(response)
        files = {row["rfilename"]: row["size"] for row in metadata["siblings"] if type(row.get("size")) is int}
        selected = files if names is None else {name: files[name] for name in sorted(names)}
        entry = {"files": selected, "repository_revision": metadata["sha"]}
        if names is None:
            entry["snapshot_files"] = sorted(files)
        sizes[repo + "@" + revision] = entry
        print(repo + ": " + str(len(selected)) + " file sizes", flush=True)
    data["file_sizes"] = sizes
    data["sizes_checked_at"] = datetime.now(timezone.utc).isoformat()
    model_catalog.PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    refresh()
