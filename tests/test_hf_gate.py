"""Gated weights (app/hf_gate.py and the LoRA hub's Install): python3 -m pytest tests/test_hf_gate.py.
No network: the hub's answers are stand-ins."""
import io
import json
import os
import stat
import sys
import tempfile
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import hf_gate  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
GOOD = "hf_" + "A1b2" * 9
FILE = "https://huggingface.co/alvdansen/h3-keyframe-animation/resolve/main/adapters/h3_seq_step12000.safetensors"


class Reply(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """A stand-in huggingface.co: repos and who may read what."""

    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    hf_gate.gates.clear()
    world = {"gated": {"alvdansen/h3-keyframe-animation": "auto", "SOLRICKS/3D-Animation-Style-MiniMax-H3": "manual", "open/lora": False},
             "granted": set(), "pending": set(), "calls": []}

    def fake(request, timeout=None):
        url, auth = request.full_url, request.get_header("Authorization") or ""
        world["calls"].append((url, bool(auth)))
        if url.startswith("https://huggingface.co/api/models/"):
            return Reply(json.dumps({"gated": world["gated"][url.split("/api/models/")[1]]}).encode())
        if url.endswith("/api/whoami-v2"):
            if auth != "Bearer " + GOOD:
                raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
            return Reply(json.dumps({"name": "filmmaker"}).encode())
        repo = hf_gate.repo_of(url)
        if not auth or auth != "Bearer " + GOOD:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b"Access to model is restricted"))
        if repo in world["pending"]:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"Your request to access model is awaiting a review from the repo authors."))
        if world["gated"].get(repo) and repo not in world["granted"]:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b"You have to accept the conditions to access its files."))
        return Reply(b"x")

    monkeypatch.setattr(hf_gate.urllib.request, "urlopen", fake)
    return world


def test_a_file_is_walked_from_sign_in_to_accept_to_granted(hub):
    gate = hf_gate.gate_of(FILE)
    assert gate["gated"] == "auto" and gate["access"] == "sign_in" and gate["page"] == "https://huggingface.co/alvdansen/h3-keyframe-animation"
    assert client.post("/api/hf/token", json={"token": "not-a-token"}).status_code == 400
    assert client.post("/api/hf/token", json={"token": "hf_" + "Z" * 30}).status_code == 400  # the hub refuses it
    saved = client.post("/api/hf/token", json={"token": GOOD}).json()
    assert saved["user"] == "filmmaker" and GOOD not in json.dumps(saved)
    kept = hf_gate.token_file()
    assert kept.read_text().strip() == GOOD and stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert client.get("/api/hf").json()["user"] == "filmmaker"
    gate = hf_gate.gate_of(FILE)
    assert gate["access"] == "accept" and gate["user"] == "filmmaker"
    hub["granted"].add("alvdansen/h3-keyframe-animation")
    assert hf_gate.gate_of(FILE)["access"] == "granted"
    assert not any("huggingface.co" not in url for url, _ in hub["calls"])  # the token goes nowhere else


def test_a_manual_gate_says_it_waits_on_the_author_and_an_open_file_is_open(hub):
    hf_gate.save_token(GOOD)
    hub["pending"].add("SOLRICKS/3D-Animation-Style-MiniMax-H3")
    gate = hf_gate.gate_of("https://huggingface.co/SOLRICKS/3D-Animation-Style-MiniMax-H3/resolve/main/x.safetensors")
    assert gate["gated"] == "manual" and gate["access"] == "pending"
    assert hf_gate.gate_of("https://huggingface.co/open/lora/resolve/main/a.safetensors")["access"] == "open"
    assert hf_gate.gate_of("https://example.com/a.safetensors") == {"gated": False, "access": "open"}
    assert hf_gate.repo_of("https://huggingface.co/datasets/a/b/resolve/main/x") is None


def test_install_of_a_gated_lora_answers_with_the_gate_and_starts_nothing(hub):
    keyframe = next(r for r in client.get("/api/loras").json() if r["id"] == "keyframe-animation")
    out = client.post("/api/loras/keyframe-animation/install", json={}).json()
    assert out["gate"]["access"] == "sign_in" and out["gate"]["gated"] == "auto"
    assert not (client.get("/api/loras").json() and next(r for r in client.get("/api/loras").json() if r["id"] == "keyframe-animation").get("installing"))
    assert keyframe["name"] == "Keyframe Animation"


def test_the_token_is_only_saved_from_this_computer(hub):
    assert client.post("/api/hf/token", json={"token": GOOD}, headers={"host": "192.168.1.20:7870"}).status_code == 403
