"""Settings (app/settings.py): the schema, the defaults, what is kept in the studio folder's settings.json, the write
guard in front of it, and the delivery sound rate a job is given. Run:
STUDIO_HOME=$(mktemp -d) python3 -m pytest tests/test_settings.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def fresh():
    path = server.HOME / "settings.json"
    kept = path.read_text() if path.exists() else None
    path.unlink(missing_ok=True)
    yield path
    if kept is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(kept)


def test_the_defaults_come_back_before_anything_is_saved(fresh):
    reply = client.get("/api/settings").json()
    assert reply["settings"] == settings.DEFAULTS and reply["defaults"] == settings.DEFAULTS
    assert reply["settings"]["appearance"]["type_size"] == "comfortable" and reply["settings"]["presets"]["recipe"] == "quick"
    assert "machine" in reply["schema"]["always"] and "settings" in reply["schema"]["always"]
    assert not fresh.exists()  # reading writes nothing


def test_a_partial_change_is_merged_kept_and_read_back(fresh):
    reply = client.post("/api/settings", json={"appearance": {"type_size": "largest", "glow": False}, "presets": {"audio_rate": "48000", "seconds": 8}})
    assert reply.status_code == 200, reply.text
    now = reply.json()["settings"]
    assert now["appearance"]["type_size"] == "largest" and now["appearance"]["glow"] is False and now["appearance"]["poster"] is True
    assert now["presets"]["audio_rate"] == "48000" and now["presets"]["seconds"] == 8 and now["presets"]["recipe"] == "quick"
    on_disk = json.loads(fresh.read_text())
    assert on_disk == now and oct(fresh.stat().st_mode & 0o777) == "0o600"
    assert client.get("/api/settings").json()["settings"] == now
    client.post("/api/settings", json={"layout": {"hidden": ["looks", "looks", "learn"], "order": ["sound", "story"]}})
    assert settings.load()["layout"] == {"hidden": ["looks", "learn"], "order": ["sound", "story"]}
    assert settings.load()["appearance"]["type_size"] == "largest"  # the earlier change stays


@pytest.mark.parametrize("body", [
    {"api_key": "sk-123"},                                   # unknown top-level key: no secrets, nothing unlisted
    {"appearance": {"token": "x"}},                          # unknown key in a section
    {"appearance": {"type_size": "huge"}},                   # not one of the choices
    {"appearance": {"glow": "yes"}},                         # a flag is true or false
    {"appearance": {"start_cards": ["clip", "nope"]}},
    {"appearance": "large"},                                 # a section is an object
    {"layout": {"hidden": ["machine"]}},                     # Machine and Settings always stay
    {"layout": {"hidden": ["settings"]}},
    {"layout": {"order": ["story", "attic"]}},
    {"presets": {"seconds": 40}}, {"presets": {"seconds": True}}, {"presets": {"recipe": "ultra"}},
    {"presets": {"image_model": "../../etc"}}, {"presets": {"audio_rate": "96000"}},
    {"hermes_profile": "a b"}, {"hermes_command": "hermes; rm -rf ~"}, {"hermes_command": "relative/hermes"},
])
def test_anything_outside_the_schema_is_refused_and_nothing_is_written(fresh, body):
    reply = client.post("/api/settings", json=body)
    said = reply.json()
    assert reply.status_code == 400 and (said.get("error") or said.get("detail")), (body, reply.text)
    assert not fresh.exists()


def a_program(tmp_path, mode=0o755) -> str:
    program = tmp_path / "hermes"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(mode)
    return str(program)


def test_the_hermes_keys_are_the_ones_agents_reads(fresh, tmp_path):
    command = a_program(tmp_path)
    reply = client.post("/api/settings", json={"hermes_profile": "studio", "hermes_command": command})
    assert reply.status_code == 200
    saved = json.loads(fresh.read_text())
    assert saved["hermes_profile"] == "studio" and saved["hermes_command"] == command


def test_hermes_command_is_a_program_this_computer_can_run(fresh, tmp_path):
    for command in (str(tmp_path / "missing"), str(tmp_path), a_program(tmp_path, 0o644), str(tmp_path / "x" / ".." / "hermes")):
        reply = client.post("/api/settings", json={"hermes_command": command})
        assert reply.status_code == 400 and "hermes_command" in reply.json()["error"], (command, reply.text)
    assert not fresh.exists()
    assert client.post("/api/settings", json={"hermes_command": ""}).status_code == 200  # cleared: the one on PATH


def test_the_hermes_keys_are_set_from_this_computer_only(fresh, tmp_path):
    """The program the Agent room runs with the user's sign-ins: a LAN client (HOST=0.0.0.0) cannot set it."""
    away = TestClient(server.app, client=("192.168.1.50", 50000))
    for body in ({"hermes_command": a_program(tmp_path)}, {"hermes_profile": "studio"}, {"appearance": {"glow": False}, "hermes_command": "/bin/sh"}):
        reply = away.post("/api/settings", json=body)
        assert reply.status_code == 403, (body, reply.text)
    assert not fresh.exists()
    assert away.post("/api/settings", json={"appearance": {"glow": False}}).status_code == 200  # the look is anyone's on the page
    assert client.post("/api/settings", json={"hermes_profile": "studio"}).status_code == 200


def test_a_page_of_another_site_cannot_change_settings(fresh):
    for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Host": "evil.example"}):
        reply = client.post("/api/settings", json={"appearance": {"glow": False}}, headers=headers)
        assert reply.status_code == 403, headers
    assert not fresh.exists()
    assert client.post("/api/settings", json={"appearance": {"glow": False}}, headers={"Origin": "http://testserver"}).status_code == 200


def test_a_hand_edited_file_with_bad_values_falls_back_to_the_defaults(fresh):
    fresh.write_text(json.dumps({"appearance": {"type_size": "giant", "glow": False}, "layout": {"hidden": ["machine"]}, "stray": 1}))
    now = settings.load()
    assert now["appearance"]["type_size"] == "comfortable" and now["appearance"]["glow"] is False
    assert now["layout"]["hidden"] == [] and "stray" not in now
    fresh.write_text("{ cut off")
    assert settings.load() == settings.DEFAULTS


def test_the_delivery_sound_rate_reaches_the_jobs_the_app_launches(fresh, monkeypatch):
    monkeypatch.delenv("DELIVERY_AUDIO_RATE", raising=False)
    assert "DELIVERY_AUDIO_RATE" not in server.environment({})
    client.post("/api/settings", json={"presets": {"audio_rate": "48000"}})
    assert server.environment({})["DELIVERY_AUDIO_RATE"] == "48000"
    monkeypatch.setenv("DELIVERY_AUDIO_RATE", "44100")  # the environment the studio was started with wins
    assert server.environment({})["DELIVERY_AUDIO_RATE"] == "44100"


def test_the_nodes_routes_are_wired_in():
    assert client.get("/api/settings").status_code == 200
    if (ROOT / "app" / "nodes.py").exists():
        reply = client.get("/api/nodes/graphs")
        assert reply.status_code == 200 and "templates" in reply.json()
