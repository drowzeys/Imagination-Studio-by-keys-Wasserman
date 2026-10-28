"""Clearing the studio: finished jobs and the clips, stills and check files they wrote go to the trash folder.

Nothing here erases a file except "Empty trash". A deletion moves each file to ~/ImaginationStudio/trash under a
timestamped name (the convention of the library's and the LoRA hub's delete) and writes down where it came from, so
the last deletion can be put back. What can be moved: pictures, clips and sound in renders, exports, scouts, uploads
and qa, Drama 3 voice takes in voice, songs in music (a YuE2 song with its score folder), and clips in projects and slate. Never a project file, a LoRA, a model, or a file a queued or running job
still reads or writes.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

MEDIA = (".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")
FOLDERS = {"renders": MEDIA, "exports": MEDIA, "scouts": MEDIA, "uploads": MEDIA, "qa": MEDIA,
           "voice": MEDIA, "music": (".wav",), "projects": (".mp4",), "slate": (".mp4",)}  # in a film's folder only its clips, never its project file
# a YuE2 song's own folder beside it (music/<job>.yue2: its score and what it was made from), which goes with the song
SONG_FOLDER = (".abc", ".npy", ".json", ".txt", ".mid", ".wav")
BUSY = ("queued", "running")
REFERENCES = ("image", "references", "reference_videos", "reference_audios", "first_frame_from", "ref_audio")
LAST = "last-deletion.json"


def attach(home: Path, jobs: dict, order: list, lock: threading.Lock):
    router = APIRouter()
    trash = home / "trash"
    root = home.resolve()

    def movable(path: str | Path) -> Path:
        """The file at a path given by a page, when it is one a deletion may move; ValueError says why not."""

        text = str(path)
        if not text or "\x00" in text:
            raise ValueError("not a path")
        target = (home / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
        if root not in target.parents:
            raise ValueError("outside the studio folder")
        parts = target.relative_to(root).parts
        if parts[0] not in FOLDERS or len(parts) < 2:
            raise ValueError("not in a folder of clips or pictures")
        song_folder = parts[0] == "music" and len(parts) >= 3 and parts[1].endswith(".yue2")
        song_note = parts[0] == "music" and len(parts) == 2 and target.name.endswith(".take.json")  # the song's record
        if not song_note and target.suffix.lower() not in (SONG_FOLDER if song_folder else FOLDERS[parts[0]]):
            raise ValueError("not a clip, picture or sound file")
        if target.is_symlink() or (home / text).is_symlink() or not target.is_file():
            raise ValueError("no such file")
        return target

    def rel(path: Path) -> str:
        return str(path.resolve().relative_to(root))

    def named(params: dict) -> set[str]:
        """The studio files a job's settings name: its first frame and its references."""

        out = set()
        for key in REFERENCES:
            value = params.get(key)
            for one in value if isinstance(value, list) else [value]:
                if isinstance(one, dict):
                    one = one.get("path")
                if isinstance(one, str) and one:
                    out.add(one)
        return out

    def films() -> str:
        """Every project and Slate file as text: a clip named there is in a film."""

        text = []
        for folder in ("projects", "slate"):
            for file in (home / folder).rglob("*.json"):
                try:
                    text.append(file.read_text())
                except OSError:
                    pass
        return "\n".join(text)

    def of_job(job: dict) -> list[str]:
        """What one job wrote, and the uploads only it reads."""

        found = list(job.get("outputs") or [])
        for folder, pattern in (("qa", f"{job['id']}-take*"), ("uploads", f"{job['id']}_first.*")):
            found += [rel(p) for p in (home / folder).glob(pattern)]
        for take in (job.get("qa") or {}).get("takes") or []:
            if take.get("sheet"):
                found.append(take["sheet"])
        found += [p for p in named(job["params"]) if p.startswith("uploads/")]
        return list(dict.fromkeys(found))

    def with_song(moved: list[dict], gone: set[str], keep_films: bool) -> tuple[list[dict], list[dict]]:
        """What goes with a song that went to the trash: its record (music/<id>.take.json) and a YuE2 song's folder of
        its score and what it was made from (music/<id>.yue2/), emptied and removed. Never without the song itself."""

        extra = []
        for one in moved:
            name = Path(one["from"])
            if name.parts[0] == "music" and len(name.parts) == 2 and name.suffix == ".wav":
                extra += [f"music/{name.stem}.take.json"]
                extra += sorted(rel(p) for p in (home / "music" / f"{name.stem}.yue2").rglob("*") if p.is_file())
        more, skipped = move(extra, gone, keep_films) if extra else ([], [])
        for one in moved:
            folder = home / "music" / f"{Path(one['from']).stem}.yue2"
            if one["from"].startswith("music/") and folder.is_dir() and not folder.is_symlink():
                for inner in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)) + [folder]:
                    try:  # emptied, bottom up (restoring makes them again); a file left in one keeps it
                        inner.rmdir()
                    except OSError:
                        pass
        return more, [one for one in skipped if one.get("why") != "no such file"]

    def move(paths: list[str], gone: set[str], keep_films: bool) -> tuple[list[dict], list[dict]]:
        """Called with the lock held. (moved, skipped)."""

        busy_text = json.dumps([[j["params"], j.get("outputs")] for j in jobs.values() if j["status"] in BUSY])
        busy_ids = {i for i, j in jobs.items() if j["status"] in BUSY}
        used = set().union(*[named(j["params"]) for i, j in jobs.items() if i not in gone]) if jobs else set()
        film = films() if keep_films else ""
        stamp, moved, skipped = time.strftime("%Y%m%d-%H%M%S-"), [], []
        for path in dict.fromkeys(paths):
            try:
                target = movable(path)
            except ValueError as error:
                if str(error) != "no such file":  # a file already gone is not worth a line
                    skipped.append({"path": str(path), "why": str(error)})
                continue
            name = rel(target)
            if json.dumps(name)[1:-1] in busy_text:
                skipped.append({"path": name, "why": "a queued or running job uses it"})
            elif name.startswith("music/") and (Path(name).parts[1].split(".")[0] in busy_ids or
                                                 json.dumps(Path(name).parts[1].split(".")[0]) in busy_text + film):
                # a song is named by its take id (a project's soundtrack, an export's or a finish's), and one being
                # made is its job's own id
                skipped.append({"path": name, "why": "a film, a queued job or the song being made uses it"})
            elif name.startswith("uploads/") and name in used:
                skipped.append({"path": name, "why": "another job in the queue list uses it"})
            elif film and json.dumps(name)[1:-1] in film:
                skipped.append({"path": name, "why": "it is in a film (a project's timeline or a Slate take)"})
            else:
                kept = trash / (stamp + name.replace("/", "__"))
                kept.parent.mkdir(parents=True, exist_ok=True)
                n = 1
                while kept.exists():
                    kept = kept.with_name(f"{kept.stem}-{(n := n + 1)}{kept.suffix}")
                shutil.move(str(target), str(kept))
                moved.append({"from": name, "to": kept.name, "size": kept.stat().st_size})
        return moved, skipped

    @router.post("/api/clear")
    def clear(body: dict) -> dict:
        """Jobs out of the queue list and files into the trash. `jobs`: ids; `finished` / `failed`: every job that is
        done or cancelled / that failed; `paths`: library files. A file in a film stays unless `films` is true."""

        with lock:
            picked = [i for i in (body.get("jobs") or []) if i in jobs]
            if body.get("finished"):
                picked += [i for i in order if jobs[i]["status"] in ("done", "cancelled")]
            if body.get("failed"):
                picked += [i for i in order if jobs[i]["status"] == "failed"]
            picked = list(dict.fromkeys(picked))
            waiting = [i for i in picked if jobs[i]["status"] in BUSY]
            gone = [i for i in picked if i not in waiting]
            paths = [p for i in gone for p in of_job(jobs[i])] + [str(p) for p in (body.get("paths") or [])]
            moved, skipped = move(paths, set(gone), not body.get("films"))
            more, also = with_song(moved, set(gone), not body.get("films"))
            moved, skipped = moved + more, skipped + also
            records = [jobs.pop(i) for i in gone]
            order[:] = [i for i in order if i in jobs]
            if moved or records:
                trash.mkdir(parents=True, exist_ok=True)
                fresh = trash / (LAST + ".new")
                fresh.write_text(json.dumps({"when": time.time(), "files": moved, "jobs": records}))
                fresh.replace(trash / LAST)
        return {"jobs": len(gone), "files": len(moved), "bytes": sum(m["size"] for m in moved), "skipped": skipped
                + [{"path": i, "why": "the job is queued or running: cancel it first"} for i in waiting]}

    def contents() -> list[Path]:
        return [p for p in trash.iterdir() if p.is_file() and p.name not in (LAST, LAST + ".new")] if trash.is_dir() else []

    def last() -> dict | None:
        try:
            return json.loads((trash / LAST).read_text())
        except (OSError, ValueError):
            return None

    @router.get("/api/trash")
    def trash_state() -> dict:
        files, note = contents(), last()
        there = [f for f in (note or {}).get("files", []) if (trash / f["to"]).is_file()]
        return {"files": len(files), "bytes": sum(p.stat().st_size for p in files), "folder": str(trash),
                "last": {"when": note["when"], "files": len(there), "jobs": len(note.get("jobs", []))} if note and (there or note.get("jobs")) else None}

    @router.post("/api/trash/restore")
    def restore() -> dict:
        """The last deletion put back: its files where they were, its jobs in the queue list again."""

        with lock:
            note = last()
            if not note:
                raise HTTPException(400, "there is no deletion to put back")
            back, lost = 0, []
            for f in note.get("files", []):
                source, target = trash / Path(f["to"]).name, (home / f["from"]).resolve()
                if root not in target.parents or target.relative_to(root).parts[0] not in FOLDERS:
                    continue
                if not source.is_file() or target.exists():
                    lost.append(f["from"])
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                back += 1
            again = 0
            for job in note.get("jobs", []):
                if isinstance(job, dict) and job.get("id") and job["id"] not in jobs:
                    jobs[job["id"]] = job
                    order.append(job["id"])
                    again += 1
            order.sort(key=lambda i: jobs[i].get("created") or 0)
            (trash / LAST).unlink(missing_ok=True)
        return {"files": back, "jobs": again, "missing": lost}

    @router.delete("/api/trash")
    def empty(confirm: str = "") -> dict:
        """Erases what is in the trash folder. This is the one request in the studio that cannot be undone."""

        if confirm != "erase":
            raise HTTPException(400, "emptying the trash erases its files for good: ask with confirm=erase")
        with lock:
            files = contents()
            size = sum(p.stat().st_size for p in files)
            for p in files:
                p.unlink()
            (trash / LAST).unlink(missing_ok=True)
        return {"files": len(files), "bytes": size}

    return router, movable
