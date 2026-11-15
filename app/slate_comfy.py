"""Send Slate shots to an operator-configured ComfyUI worker."""

import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import remote

IMAGE_LIMIT = 20 << 20


def profiles() -> dict:
    path = os.environ.get("STUDIO_COMFY_WORKFLOWS")
    if not path or not os.environ.get("STUDIO_COMFY_URL"):
        return {}
    return json.loads(Path(path).read_text())


def request(route: str, body=None, content_type="application/json"):
    base = remote.address(os.environ["STUDIO_COMFY_URL"])
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    return remote.OPENER.open(urllib.request.Request(
        base + route, data=body, headers={"Content-Type": content_type}), timeout=60)


def upload(home: Path, value: str) -> str:
    image = (home / value).resolve()
    if (home.resolve() not in image.parents or image.suffix.lower() not in remote.SUFFIXES[2:]
            or any(part.startswith(".") for part in image.relative_to(home.resolve()).parts)
            or not image.is_file() or image.stat().st_size > IMAGE_LIMIT):
        raise ValueError("Select a Studio reference image up to 20 MB.")
    boundary = uuid.uuid4().hex
    name = uuid.uuid4().hex + image.suffix.lower()
    header = f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n\r\n'
    data = header.encode() + image.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    with request("/upload/image", data, f"multipart/form-data; boundary={boundary}") as reply:
        result = json.load(reply)
    return "/".join(filter(None, [result.get("subfolder"), result["name"]]))


def submit(home: Path, profile: dict, prompt: str, seed: int, images: list[str]) -> str:
    workflow = json.loads(Path(profile["workflow"]).read_text())
    inputs = profile["inputs"]
    reference_slots = inputs.get("references", [])
    if len(images) != len(reference_slots):
        raise ValueError(f"This workflow requires {len(reference_slots)} reference images.")
    fields = [(inputs["prompt"], prompt), (inputs["seed"], seed)]
    fields.extend((slot, upload(home, image)) for slot, image in zip(reference_slots, images))
    for (node, key), value in fields:
        if key not in workflow[node]["inputs"]:
            raise ValueError(f"Workflow input {node}.{key} does not exist.")
        workflow[node]["inputs"][key] = value
    with request("/prompt", {"prompt": workflow}) as reply:
        return json.load(reply)["prompt_id"]


def result(home: Path, profile: dict, job: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", job):
        raise ValueError("Invalid ComfyUI job ID.")
    with request("/history/" + job) as reply:
        entry = json.load(reply).get(job)
    if not entry:
        return {"state": "waiting"}
    if entry.get("status", {}).get("status_str") == "error":
        messages = entry["status"].get("messages", [])
        detail = next((data.get("exception_message") for kind, data in messages if kind == "execution_error"), None)
        raise ValueError(f"ComfyUI render failed: {(detail or 'see the worker log')[:300]}")
    output = entry.get("outputs", {}).get(profile["output"], {})
    video = next((item for group in output.values() if isinstance(group, list) for item in group
                  if isinstance(item, dict) and item.get("type") == "output"
                  and str(item.get("filename", "")).lower().endswith(".mp4")), None)
    if video is None:
        raise ValueError("The configured output node did not produce an MP4.")
    target = home / "uploads" / f"comfy-{job}.mp4"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(f".{uuid.uuid4().hex}.part")
        try:
            query = urllib.parse.urlencode({key: video.get(key, "") for key in ("filename", "subfolder", "type")})
            with request("/view?" + query) as reply, part.open("wb") as out:
                size = 0
                while chunk := reply.read(1 << 20):
                    size += len(chunk)
                    if size > remote.LIMIT:
                        raise ValueError("ComfyUI output is too large.")
                    out.write(chunk)
                if not size:
                    raise ValueError("ComfyUI returned an empty MP4.")
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    return {"state": "done", "path": target.relative_to(home).as_posix()}
