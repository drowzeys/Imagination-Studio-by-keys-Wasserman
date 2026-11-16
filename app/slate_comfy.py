"""Send Slate shots to an operator-configured ComfyUI worker: a ComfyUI on another PC, or Comfy Cloud (issue #185).

Comfy Cloud is the same panel with a key: ``STUDIO_COMFY_URL=https://cloud.comfy.org`` and ``STUDIO_COMFY_API_KEY``
(the person's own, from platform.comfy.org; API access needs a paid Comfy Cloud plan). The key is read from the
environment only: it is never written to a file, a log or an answer, it is sent only over https, and only to the
address it was set for (a finished file is fetched from the signed address the cloud redirects to without it).
The cloud speaks ComfyUI's own routes under ``/api`` and tells a job's state and outputs at ``/api/jobs/<id>``."""

import json
import os
import re
import urllib.error
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
    worker()  # a refused address is said on the panel, before anything is sent
    return json.loads(Path(path).read_text())


def own_ports() -> set[str]:
    """The ports this Studio's own ComfyUI listens on (spark/comfy.sh, spark/comfy_win.py, spark/spark_generate.py)."""

    return {(os.environ.get("SPARK_COMFY_PORT") or "8190").strip(), (os.environ.get("MAC_COMFY_PORT") or "8191").strip()}


def worker() -> str:
    """The operator's ComfyUI address. Never this Studio's own ComfyUI: its renders are queue jobs, admitted by the
    memory guard and the thermal gate, and a workflow posted to it directly would go around all three."""

    base = remote.address(os.environ["STUDIO_COMFY_URL"])
    parts = urllib.parse.urlsplit(base)
    if parts.hostname in ("127.0.0.1", "localhost", "::1") and str(parts.port or 80) in own_ports():
        raise ValueError("STUDIO_COMFY_URL is this Studio's own ComfyUI. Point it at the ComfyUI of another PC "
                         "(for one behind an SSH tunnel, use a local port other than " + " or ".join(sorted(own_ports())) + ").")
    return base


def cloud_key() -> str:
    """The Comfy Cloud API key, when one is set: the worker is then spoken to as Comfy Cloud."""

    return (os.environ.get("STUDIO_COMFY_API_KEY") or "").strip()


def request(route: str, body=None, content_type="application/json"):
    base, key = worker(), cloud_key()
    headers = {"Content-Type": content_type}
    if key:
        parts = urllib.parse.urlsplit(base)
        if parts.scheme != "https" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("STUDIO_COMFY_API_KEY is only sent over https: set STUDIO_COMFY_URL=https://cloud.comfy.org")
        route, headers["X-API-Key"] = "/api" + route, key
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    try:
        return remote.OPENER.open(urllib.request.Request(base + route, data=body, headers=headers), timeout=60)
    except urllib.error.HTTPError as error:
        if key and error.code in (401, 402, 429):
            raise ValueError({401: "Comfy Cloud refused the API key: check STUDIO_COMFY_API_KEY.",
                              402: "Comfy Cloud: not enough credits for this render.",
                              429: "Comfy Cloud: the subscription is not active (API access needs a paid plan)."}[error.code]) from None
        if key and error.code == 400:  # the cloud says what is wrong with a workflow in the answer's body
            said = error.read(4000).decode("utf-8", "replace").replace(key, "…")
            raise ValueError(f"Comfy Cloud did not take the request: {' '.join(said.split())[:300] or 'bad request'}") from None
        raise


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
    # partner nodes (MiniMax's own, for one) are paid for with the same key, which the cloud wants in the graph's extras
    extra = {"extra_data": {"api_key_comfy_org": cloud_key()}} if cloud_key() else {}
    with request("/prompt", {"prompt": workflow, **extra}) as reply:
        answer = json.load(reply)
    if not isinstance(answer, dict) or not isinstance(answer.get("prompt_id"), str):
        detail = answer.get("error") or answer.get("node_errors") if isinstance(answer, dict) else None
        raise ValueError(f"ComfyUI did not take the workflow: {json.dumps(detail)[:300] if detail else 'no job id came back'}")
    return answer["prompt_id"]


def cloud_entry(job: str) -> dict | None:
    """A Comfy Cloud job as {"outputs"} once it is done, None while it waits; ValueError with its reason when it
    failed or was cancelled."""

    with request("/jobs/" + job) as reply:
        said = json.load(reply)
    status = str(said.get("status") or "").lower()
    if status in ("completed", "success", "succeeded"):
        return {"outputs": said.get("outputs") or {}}
    if status in ("failed", "error", "non_retryable_error", "lost", "cancelled", "canceled", "expired"):
        error = said.get("execution_error")
        detail = (error.get("exception_message") or error.get("message") if isinstance(error, dict) else error) or said.get("error_message")
        raise ValueError(f"Comfy Cloud render {'was cancelled' if status.startswith('cancel') else 'failed'}: {str(detail or status)[:300]}")
    return None


def fetch(query: str):
    """The finished file, open for reading. Comfy Cloud answers with a redirect to a signed address: that one is
    fetched without the key, over https only, and no further redirect is followed."""

    try:
        return request("/view?" + query)
    except urllib.error.HTTPError as error:
        signed = error.headers.get("Location") if cloud_key() and error.code in (301, 302, 303, 307, 308) else None
        if not signed:
            raise
        local = urllib.parse.urlsplit(worker()).hostname in ("127.0.0.1", "localhost", "::1")  # a stand-in on this machine
        if urllib.parse.urlsplit(signed).scheme != "https" and not local:
            raise ValueError("Comfy Cloud answered with an address that is not https.") from None
        return remote.OPENER.open(urllib.request.Request(signed), timeout=60)


def result(home: Path, profile: dict, job: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", job):
        raise ValueError("Invalid ComfyUI job ID.")
    if cloud_key():
        entry = cloud_entry(job)
    else:
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
                  if isinstance(item, dict) and item.get("type", "output") == "output"
                  and str(item.get("filename", "")).lower().endswith(".mp4")), None)
    if video is None:
        raise ValueError("The configured output node did not produce an MP4.")
    target = home / "uploads" / f"comfy-{job}.mp4"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(f".{uuid.uuid4().hex}.part")
        try:
            query = urllib.parse.urlencode({key: video.get(key, "") for key in ("filename", "subfolder", "type")})
            with fetch(query) as reply, part.open("wb") as out:
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
