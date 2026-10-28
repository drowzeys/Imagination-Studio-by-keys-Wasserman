"""Explicit worker placement cannot be undone by the automatic Spark scheduler."""
import json
import threading
from pathlib import Path
import sys

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import local_gpus
import sparks
from test_local_gpus import gpu, worker


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.setenv("STUDIO_GPU", "0")
    monkeypatch.delenv("STUDIO_LOCAL_GPU_CHILD", raising=False)
    monkeypatch.delenv("STUDIO_LOCAL_GPU_UUID", raising=False)
    monkeypatch.setattr(local_gpus, "inventory", lambda **kw: {"supported": True, "devices": [gpu(0), gpu(1), gpu(2)]})
    info = {"guard": True, "code": {"VERSION": "synthetic"}, "version": "0.0.0", "active": 0,
            "fasth3": True, "ref2va": True, "ref2va_turbo": True, "ffmpeg": True,
            "loras": [], "text_encoders": [], "presets": ["small"], "qualities": ["fh8"],
            "memory": {"available_mb": 22000}, "disk_gb": 50}
    h = sparks.Hub(tmp_path / "studio", tmp_path / "pack", True, {}, [], threading.RLock(), threading.Event(),
                   lambda: info.copy(), lambda p: tmp_path / p, lambda j, w: None)
    h.mine = info["code"]
    workers = [{"address": "user@example.invalid", "name": "Remote", "enabled": True}, worker(gpu(1)), worker(gpu(2))]
    h.keep({"workers": workers, "sparks": 1})
    for w in h.workers():
        h.state(w["address"]).update(state="ready", info=info.copy())
    yield h
    for state in h.live.values():
        sparks.KEYS.pop(state.get("base"), None)


def job(hub, placement=None, *, count=1, kind="video"):
    found = {"id": str(len(hub.jobs)), "kind": kind, "params": {"preset": "small", "quality": "fh8"},
             "status": "queued", "title": "Synthetic clip", "sparks": {"chosen": count}, "log": "", "placement": placement}
    hub.jobs[found["id"]] = found
    hub.order.append(found["id"])
    return found


def target(hub):
    return hub.workers()[1]["address"]


def test_placement_normalization_and_video_only_default(hub):
    selected = {"worker": target(hub), "policy": "require"}
    config = hub.config()
    config["placement"] = selected
    hub.keep(config)
    assert hub.placement() == selected
    assert hub.placement(kind="scout") is None
    assert hub.placement({"worker": "head", "policy": "require"}, kind="scout")["worker"] == "head"
    with pytest.raises(HTTPException):
        hub.placement(selected, kind="scout")
    for invalid in ({"worker": target(hub)}, {"worker": "unknown", "policy": "require"},
                    {"worker": target(hub), "policy": "automatic"}, {"worker": target(hub), "policy": "require", "extra": True}):
        with pytest.raises(HTTPException):
            hub.placement(invalid)


def test_required_target_ignores_count_and_moves(hub):
    selected = {"worker": target(hub), "policy": "require"}
    clip = job(hub, selected)
    clip["sparks"].update(moves=100, **{"not": {target(hub): "old refusal"}})
    assert hub.team(clip) == [target(hub)]
    assert hub.claim() is None
    assert hub.claim(hub.workers()[0]["address"]) is None
    assert hub.claim(target(hub)) is clip
    assert clip["remote"] == selected["worker"]


def test_required_busy_target_waits_without_head_fallback(hub):
    address = target(hub)
    hub.state(address).update(job="another-clip", state="busy")
    clip = job(hub, {"worker": address, "policy": "require"}, count=7)
    assert hub.claim() is None and hub.claim(address) is None
    assert clip["progress"] and clip["status"] == "queued"
    assert hub.claim(hub.workers()[2]["address"]) is None
    hub.state(address).update(job=None, state="ready")
    assert hub.claim(address) is clip


@pytest.mark.parametrize("state", ["stopped", "down", "update", "unreachable"])
def test_required_unavailable_target_never_claimed_elsewhere(hub, state):
    address = target(hub)
    hub.state(address).update(state=state, why="synthetic unavailable")
    clip = job(hub, {"worker": address, "policy": "require"}, count=7)
    assert hub.team(clip) == [] and hub.claim() is None
    assert hub.claim(hub.workers()[0]["address"]) is None
    assert clip["status"] == "queued"


def test_required_worker_failure_fails_instead_of_moving(hub):
    address = target(hub)
    clip = job(hub, {"worker": address, "policy": "require"})
    hub.claim(address)
    with pytest.raises(RuntimeError):
        hub.move(clip, hub.workers()[1], "synthetic failure", down=True)
    assert clip["status"] == "running"  # server.perform catches the exception and marks it failed
    assert clip["remote"] == address and not clip.get("moved")
    assert hub.state(address)["state"] == "down"


def test_preferred_free_worker_takes_priority_over_head_and_others(hub):
    address = target(hub)
    clip = job(hub, {"worker": address, "policy": "prefer"}, count=1)
    assert hub.team(clip) == [address]
    assert hub.claim() is None
    assert hub.claim(hub.workers()[0]["address"]) is None
    assert hub.claim(address) is clip


@pytest.mark.parametrize("state", ["stopped", "busy"])
def test_preferred_unavailable_worker_falls_back_to_head(hub, state):
    address = target(hub)
    hub.state(address).update(state=state, job="other" if state == "busy" else None)
    clip = job(hub, {"worker": address, "policy": "prefer"})
    assert hub.claim() is clip and clip["remote"] is None


def test_prefer_worker_failure_uses_automatic_fallback(hub):
    address = target(hub)
    clip = job(hub, {"worker": address, "policy": "prefer"})
    hub.claim(address)
    with pytest.raises(sparks.Moved):
        hub.move(clip, hub.workers()[1], "synthetic refusal", down=False)
    assert clip["status"] == "queued"
    hub.state(address).update(job=None, state="ready")
    assert hub.claim() is clip


def test_require_head_excludes_workers_and_prefer_head_yields_when_busy(hub):
    required = job(hub, {"worker": "head", "policy": "require"}, count=7)
    assert hub.team(required) == []
    assert hub.claim(target(hub)) is None
    assert hub.claim() is required
    preferred = job(hub, {"worker": "head", "policy": "prefer"}, count=3)
    assert hub.team(preferred)
    assert hub.claim(hub.workers()[0]["address"]) is preferred


def test_worker_capability_uses_selected_card_not_head(hub):
    address = target(hub)
    hub.state(address)["info"]["presets"] = ["tiny"]
    clip = job(hub, {"worker": address, "policy": "require"})
    assert hub.team(clip) == [] and hub.claim() is None
    assert hub.plan({"placement": clip["placement"], "params": clip["params"]})["mode"] == "wait"


def test_automatic_remote_spark_behavior_is_unchanged(hub):
    remote = hub.workers()[0]
    assert remote["kind"] == "ssh"
    clip = job(hub, count=2)
    assert hub.team(clip) == [remote["address"]]
    assert hub.claim() is None and hub.claim(remote["address"]) is clip
    with pytest.raises(sparks.Moved):
        hub.move(clip, remote, "synthetic remote loss", down=True)
    assert clip["status"] == "queued" and clip["sparks"]["moves"] == 1
    assert remote["address"] in clip["sparks"]["not"]




def test_required_busy_estimate_waits_and_prefer_falls_back(hub, monkeypatch):
    address = target(hub)
    hub.state(address).update(state="busy", job="other")
    monkeypatch.setattr(sparks, "call", lambda *a, **k: pytest.fail("busy guard should not refuse queued clip"))
    result = hub.estimate_worker({}, {"worker": address, "policy": "require"})
    assert result["waiting"] and result["memory"]["fits"] is None
    assert hub.estimate_worker({}, {"worker": address, "policy": "prefer"}) is None


def test_required_estimate_failure_is_never_head_estimate(hub, monkeypatch):
    monkeypatch.setattr(hub, "connect", lambda addr: "http://127.0.0.1:34567")

    def refuse(*args, **kwargs):
        raise OSError("synthetic connection failure")

    monkeypatch.setattr(sparks, "call", refuse)
    with pytest.raises(HTTPException) as raised:
        hub.estimate_worker({}, {"worker": target(hub), "policy": "require"})
    assert raised.value.status_code == 503
    assert hub.estimate_worker({}, {"worker": target(hub), "policy": "prefer"}) is None


def test_plan_explains_required_wait_and_prefer_fallback(hub):
    address = target(hub)
    hub.state(address).update(state="stopped")
    plan = hub.plan({"sparks": 1, "placement": {"worker": address, "policy": "require"}})
    assert plan["mode"] == "wait"
    prefer = hub.plan({"placement": {"worker": address, "policy": "prefer"}})
    assert prefer["mode"] != "wait" and prefer["used"] >= 1


def test_automatic_nonvideo_estimate_does_not_inherit_video_default(hub, monkeypatch):
    config = hub.config()
    config["placement"] = {"worker": target(hub), "policy": "require"}
    hub.keep(config)
    monkeypatch.setattr(sparks, "call", lambda *a, **k: pytest.fail("automatic estimate used saved video placement"))
    assert hub.estimate_worker({}, None) is None


def test_preferred_worker_memory_refusal_falls_back_but_required_does_not(hub, monkeypatch):
    monkeypatch.setattr(hub, "connect", lambda addr: "http://127.0.0.1:34567")
    monkeypatch.setattr(sparks, "call", lambda *a, **k: {"memory": {"fits": False, "reason": "not enough selected VRAM"}})
    assert hub.estimate_worker({}, {"worker": target(hub), "policy": "prefer"}) is None
    result = hub.estimate_worker({}, {"worker": target(hub), "policy": "require"})
    assert result is not None



def test_local_connect_never_uses_ssh_and_handshake_checks_uuid(hub, monkeypatch):
    local = hub.workers()[1]
    monkeypatch.setattr(hub.local, "credentials", lambda w: ("http://127.0.0.1:34567", "synthetic-key-not-a-real-token"))
    monkeypatch.setattr(hub.local, "owned", lambda w: True)
    monkeypatch.setattr(hub.local, "conflict", lambda ident: "")
    monkeypatch.setattr(hub, "dial", lambda address: pytest.fail("local worker used SSH"))
    monkeypatch.setattr(hub, "shell", lambda *a, **k: pytest.fail("local worker used SSH"))
    base = hub.connect(local["address"])
    monkeypatch.setattr(sparks, "call", lambda *a, **k: {**hub.info(), "gpu_uuid": gpu(2)["gpu_uuid"]})
    assert hub.check(local)["state"] == "down"
    assert hub.state(local["address"])["info"] is None
    monkeypatch.setattr(sparks, "call", lambda *a, **k: {**hub.info(), "gpu_uuid": local["gpu_uuid"]})
    assert hub.check(local)["state"] == "ready"
    assert hub.glance(local)
    hub.hang_up(local["address"])
    assert base not in sparks.KEYS




def test_api_saves_and_clears_video_placement_without_changing_count(hub, monkeypatch):
    app = FastAPI()
    app.include_router(hub.router())
    monkeypatch.setattr(hub.local, "owned", lambda w: False)
    client = TestClient(app)
    selected = {"worker": target(hub), "policy": "require"}
    response = client.post("/api/sparks", json={"placement": selected})
    assert response.status_code == 200 and response.json()["placement"] == selected
    assert response.json()["default"] == 1
    response = client.post("/api/sparks", json={"placement": None})
    assert response.status_code == 200 and response.json()["placement"] is None


def test_readonly_local_check_does_not_register_or_start(hub, monkeypatch):
    config = hub.config()
    config["workers"] = [config["workers"][0]]
    hub.keep(config)
    monkeypatch.setattr(hub.local, "start_worker", lambda *a, **k: pytest.fail("Check started a worker"))
    before = hub.config()
    hub.local_action({"gpu": gpu(1)["id"], "action": "check"})
    assert hub.config() == before


def test_remote_limit_is_independent_of_local_rows(hub, monkeypatch):
    monkeypatch.setattr(hub, "act", lambda *a: None)
    monkeypatch.setattr(hub.local, "owned", lambda w: False)
    app = FastAPI()
    app.include_router(hub.router())
    client = TestClient(app)
    for i in range(3):
        assert client.post("/api/sparks/workers", json={"address": f"spark-{i}.example.invalid"}).status_code == 200
    assert client.post("/api/sparks/workers", json={"address": "too-many.example.invalid"}).status_code == 400
    assert client.get("/api/sparks").json()["limit"] == sparks.LIMIT + 2


def test_local_remove_requires_stopped_owned_process(hub, monkeypatch):
    app = FastAPI()
    app.include_router(hub.router())
    client = TestClient(app)
    monkeypatch.setattr(hub.local, "owned", lambda w: True)
    assert client.delete("/api/sparks/workers/" + target(hub)).status_code == 400
    monkeypatch.setattr(hub.local, "owned", lambda w: False)
    assert client.delete("/api/sparks/workers/" + target(hub)).status_code == 200


def test_unticked_running_worker_still_blocks_a_fifth_gpu(hub, monkeypatch):
    config = hub.config()
    fourth, fifth = worker(gpu(3)), worker(gpu(4))
    fifth["enabled"] = False
    config["workers"].extend([fourth, fifth])
    hub.keep(config)
    owned_address = target(hub)
    monkeypatch.setattr(hub.local, "owned", lambda w: w["address"] == owned_address)
    app = FastAPI()
    app.include_router(hub.router())
    client = TestClient(app)
    off = client.post("/api/sparks/workers/" + owned_address, json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["local_gpus"]["managed"] == 4
    assert client.post("/api/sparks/workers/" + fifth["address"], json={"enabled": True}).status_code == 400
    assert client.post("/api/sparks/workers/" + owned_address, json={"enabled": True}).status_code == 200


def test_disabled_inventory_does_not_expand_managed_chooser_beyond_four_cards(hub):
    config = hub.config()
    for index in range(3, 8):
        row = worker(gpu(index))
        row["enabled"] = False
        config["workers"].append(row)
    hub.keep(config)
    assert hub.chooser_limit() == sparks.LIMIT + local_gpus.LIMIT - 1


def test_mixed_workers_do_not_claim_spark_scaling_was_measured(hub):
    mixed = hub.plan({"sparks": 3, "clips": 8, "seconds": 10})
    assert mixed["mode"] == "parallel" and not mixed["estimate"]["measured"]
    config = hub.config()
    config["workers"] = [w for w in config["workers"] if w.get("kind") == "ssh"]
    hub.keep(config)
    remote_only = hub.plan({"sparks": 3, "clips": 8, "seconds": 10})
    assert remote_only["mode"] == "parallel" and remote_only["estimate"]["measured"]


@pytest.mark.parametrize("local_count", [2, 3])
def test_identical_cards_keep_distinct_job_and_estimate_labels_after_reordering(hub, monkeypatch, local_count):
    devices = [gpu(index) for index in range(local_count + 1)]
    for device in devices:
        device["name"] = "Identical synthetic NVIDIA GPU"
    monkeypatch.setattr(local_gpus, "inventory", lambda **kw: {"supported": True, "devices": devices})
    config = hub.config()
    remote_name = config["workers"][0]["name"]
    if local_count == 3:
        config["workers"].append(worker(devices[3]))
    for row in config["workers"]:
        if row.get("kind") == "local":
            row["name"] = devices[0]["name"]
    hub.keep(config)
    local_rows = [w for w in hub.workers() if w.get("kind") == "local"]
    for row in local_rows:
        hub.check_local(row)  # even stopped cards have a current, distinct identity
    estimates = [hub.estimate_worker({}, {"worker": row["address"], "policy": "require"}) for row in local_rows]
    names = [estimate["worker"]["name"] for estimate in estimates]
    assert len(set(names)) == local_count
    assert all(row["gpu_uuid"] not in name for row, name in zip(local_rows, names))
    old_names = {}
    for row in local_rows:
        hub.state(row["address"]).update(state="ready", info=hub.info())
        clip = job(hub, {"worker": row["address"], "policy": "require"})
        assert hub.claim(row["address"]) is clip
        old_names[row["address"]] = clip["spark"]
        clip["status"] = "done"
        hub.state(row["address"])["job"] = None
    assert len(set(old_names.values())) == local_count
    for index, device in enumerate(reversed(devices[1:]), 1):
        device["index"] = index
    for row in local_rows:
        hub.check_local(row)
    updated = [w for w in hub.workers() if w.get("kind") == "local"]
    assert len({row["name"] for row in updated}) == local_count
    assert all(row["gpu_uuid"] not in row["name"] for row in updated)
    assert any(row["name"] != old_names[row["address"]] for row in updated)
    assert hub.workers()[0]["name"] == remote_name
    for row in updated:
        hub.state(row["address"]).update(state="ready", info=hub.info())
        clip = job(hub, {"worker": row["address"], "policy": "require"})
        assert hub.claim(row["address"]) is clip
        assert clip["remote"] == row["address"] and clip["spark"] == row["name"]


def test_local_overview_keeps_labels_status_and_uuid_placement_together(hub, monkeypatch):
    local = hub.workers()[1]
    selected = {"worker": local["address"], "policy": "require"}
    config = hub.config()
    config.update(placement=selected, sparks=7)
    hub.keep(config)
    labels = hub.folder / "labels.json"
    text = json.dumps({"head": "Render head", local["address"]: "Second card"})
    labels.write_text(text)
    head_status = {"gpu": {"name": "Synthetic head", "temp_c": 32}}
    worker_status = {"gpu": {"name": "Synthetic worker", "temp_c": 41}}
    info = hub.info()
    monkeypatch.setattr(hub, "info", lambda: {**info, "status": head_status})
    monkeypatch.setattr(hub.local, "owned", lambda row: row["address"] == local["address"])
    hub.state(local["address"])["info"]["status"] = worker_status

    overview = hub.overview()
    row = next(row for row in overview["workers"] if row["address"] == local["address"])
    device = next(device for device in overview["local_gpus"]["devices"] if device["id"] == local["address"])
    assert overview["this"]["label"] == "Render head" and overview["this"]["status"] == head_status
    assert row["label"] == "Second card" and row["status"] == worker_status
    assert row["kind"] == "local" and row["owned"] and row["gpu_uuid"] == local["gpu_uuid"]
    assert device["worker"] == row
    assert overview["names"][local["address"]] == overview["names"][local["name"]] == "Second card"
    assert overview["names"][hub.name] == "Render head"
    assert overview["placement"] == selected
    assert overview["limit"] == overview["default"] == sparks.LIMIT + 2
    assert overview["remote_limit"] == sparks.LIMIT
    hub.serialized(hub.keep)(hub.config())
    assert labels.read_text() == text
    clip = job(hub, selected)
    assert hub.claim(local["address"]) is clip
    assert clip["remote"] == local["address"] and clip["spark"] == local["name"]
