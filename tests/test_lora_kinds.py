"""A LoRA is filed under the model it was trained on, and a job merges only its own model's: python3 -m pytest tests/test_lora_kinds.py
(or python3 tests/test_lora_kinds.py). No network, no GPU: tiny files with the tensor names of real LoRAs (tests/data/lora_kind_keys.json)."""
import json
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "spark"))
import lora_kinds  # noqa: E402
import loras  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

KEYS = json.loads((ROOT / "tests/data/lora_kind_keys.json").read_text())


def lora_file(path: Path, names: list[str]) -> Path:
    """A safetensors file whose tensors have these names (a 1 x 1 matrix each)."""
    head = json.dumps({n: {"dtype": "F16", "shape": [1, 1], "data_offsets": [0, 2]} for n in names}).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(head)) + head + b"\0\0")
    return path


def refused(call) -> str:
    try:
        call()
    except (HTTPException, ValueError) as error:
        return str(getattr(error, "detail", error))
    raise AssertionError("it was not refused")


def test_the_header_says_which_model_a_lora_is_for():
    want = {"h3 (ai-toolkit)": "h3", "h3 with adaln (ai-toolkit)": "h3", "h3 (kohya)": "h3",
            "qwen-image 2.1 (ai-toolkit)": "qwen-image", "qwen-image attention only (diffusers)": "qwen-image",
            "flux.2 (diffusers)": "flux", "flux.1 (diffusers)": "flux", "flux.1 (kohya)": "flux",
            "ltx-video (comfy)": "ltx", "sdxl (peft)": "sd", "sd 1.x (kohya)": "sd", "unknown model (wan-style)": "other"}
    assert set(want) == set(KEYS)
    folder = Path(tempfile.mkdtemp())
    for name, kind in want.items():
        assert lora_kinds.detect(lora_file(folder / "x.safetensors", KEYS[name]))["kind"] == kind, name
    assert lora_kinds.detect(lora_file(folder / "a.safetensors", KEYS["flux.2 (diffusers)"]))["family"] == "Flux.2"
    assert lora_kinds.detect(lora_file(folder / "b.safetensors", KEYS["flux.1 (diffusers)"]))["family"] == "Flux.1"


def test_a_file_that_is_not_a_lora_is_refused():
    folder = Path(tempfile.mkdtemp())
    assert "not a LoRA" in refused(lambda: lora_kinds.detect(lora_file(folder / "w.safetensors", ["model.layers.0.weight", "model.norm.weight"])))
    assert "not a safetensors file" in refused(lambda: lora_kinds.detect((folder / "t.safetensors").write_bytes(b"nope") and folder / "t.safetensors"))


def test_the_check_names_the_model_a_file_is_for():
    folder = Path(tempfile.mkdtemp())
    picture = lora_file(folder / "p.safetensors", KEYS["qwen-image 2.1 (ai-toolkit)"])
    flux = lora_file(folder / "f.safetensors", KEYS["flux.2 (diffusers)"])
    assert loras.check(picture, "image") == loras.check(picture, "qwen-image") == 18
    assert "it is a Qwen-Image 2.x LoRA" in refused(lambda: loras.check(picture, "video"))
    assert "it is a Flux (Flux.2) LoRA" in refused(lambda: loras.check(flux, "image"))
    assert loras.check(flux, "flux") == 6


def test_presets_keep_their_model():
    presets = json.loads((ROOT / "app/lora-presets.json").read_text())
    home = Path(tempfile.mkdtemp())
    for spark in (True, False):
        router, _, _ = loras.attach(home, ROOT, spark)
        client = TestClient(_app(router))
        listing = {r["id"]: r for r in client.get("/api/loras").json()}
        assert len(listing) >= len(presets)
        for preset in presets:
            got = listing[preset["id"]]
            assert got["kind"] == ("qwen-image" if preset.get("target") == "image" else "h3"), preset["id"]
            assert got["base"] == lora_kinds.label(got["kind"])
        angles = listing["qwen-multiple-angles"]
        assert angles["target"] == "image" and angles["jobs"] == (["scout", "edit"] if spark else ["edit"])
        if not spark:
            assert "Edit a picture" in angles["jobs_why"]["scout"] or "use Edit a picture" in angles["jobs_why"]["scout"]
        assert listing["camera-motion"]["jobs"] == ["video"] and listing["camera-motion"]["target"] == "video"


def _app(router):
    app = FastAPI()
    app.include_router(router)
    return app


def hub(spark=True):
    """A studio folder, the hub over it, and a client for its routes."""
    home = Path(tempfile.mkdtemp())
    (home / "uploads").mkdir()
    router, resolve, lead = loras.attach(home, ROOT, spark)
    return home, resolve, lead, TestClient(_app(router))


def upload(home: Path, name: str, keys: list[str]) -> str:
    lora_file(home / "uploads" / name, keys)
    return f"uploads/{name}"


def test_the_hub_files_an_uploaded_lora_under_its_own_model():
    home, resolve, _, client = hub()
    picture = client.post("/api/loras", json={"name": "Face (picture)", "file": upload(home, "a.safetensors", KEYS["qwen-image 2.1 (ai-toolkit)"]), "trigger": "xyz1", "strength": 1.0}).json()
    assert picture["kind"] == "qwen-image" and picture["base"] == "Qwen-Image 2.x" and picture["target"] == "image"
    assert picture["installed"] and picture["usable"] and picture["jobs"] == ["scout", "edit"]
    assert (home / "loras" / f"{picture['id']}.safetensors").is_file() and not (home / "loras" / f"{picture['id']}.comfy.safetensors").exists()
    clip = client.post("/api/loras", json={"name": "Look (clip)", "file": upload(home, "b.safetensors", KEYS["h3 (ai-toolkit)"])}).json()
    assert clip["kind"] == "h3" and clip["jobs"] == ["video"] and clip["usable"]
    stored = client.post("/api/loras", json={"name": "Face (flux)", "file": upload(home, "c.safetensors", KEYS["flux.2 (diffusers)"])}).json()
    assert stored["kind"] == "flux" and stored["status"] == "stored" and stored["installed"] and not stored["usable"] and stored["jobs"] == []
    assert "no Flux engine yet" in stored["why"]
    ltx = client.post("/api/loras", json={"name": "Clip (ltx)", "file": upload(home, "d.safetensors", KEYS["ltx-video (comfy)"])}).json()
    assert ltx["kind"] == "ltx" and ltx["status"] == "stored"
    # what the hub lists is what was added, under the models' names
    kinds = {r["id"]: r["kind"] for r in client.get("/api/loras").json() if not r["builtin"]}
    assert sorted(kinds.values()) == ["flux", "h3", "ltx", "qwen-image"]
    # the file the studio keeps is the author's own bytes for a picture or stored LoRA
    assert (home / "loras" / f"{stored['id']}.safetensors").read_bytes() == lora_file(home / "same.safetensors", KEYS["flux.2 (diffusers)"]).read_bytes()


def test_a_wrong_kind_or_a_file_that_is_not_a_lora_is_refused_at_the_door():
    home, _, _, client = hub()
    said = client.post("/api/loras", json={"name": "Wrong", "kind": "h3", "file": upload(home, "a.safetensors", KEYS["qwen-image 2.1 (ai-toolkit)"])})
    assert said.status_code == 400 and "Qwen-Image 2.x LoRA's" in said.json()["detail"]
    assert not (home / "loras" / "wrong.safetensors").exists()
    weights = client.post("/api/loras", json={"name": "Weights", "file": upload(home, "w.safetensors", ["model.layers.0.weight"])})
    assert weights.status_code == 400 and "not a LoRA" in weights.json()["detail"]
    assert client.post("/api/loras", json={"name": "Odd", "kind": "wan", "url": "https://example.invalid/x.safetensors"}).status_code == 400
    assert [r for r in client.get("/api/loras").json() if not r["builtin"]] == []
    # an address with no kind said is a MiniMax H3 LoRA, as it always was; one that says picture is checked as a picture
    assert client.post("/api/loras", json={"name": "From a link", "url": "https://example.invalid/x.safetensors"}).json()["kind"] == "h3"
    assert client.post("/api/loras", json={"name": "Picture link", "kind": "qwen-image", "url": "https://example.invalid/y.safetensors"}).json()["target"] == "image"


def test_each_job_takes_only_its_own_models_loras():
    home, resolve, lead, client = hub(spark=True)
    ids = {}
    for name, key in (("picture", "qwen-image 2.1 (ai-toolkit)"), ("clip", "h3 (ai-toolkit)"), ("flux", "flux.2 (diffusers)")):
        ids[name] = client.post("/api/loras", json={"name": name, "file": upload(home, name + ".safetensors", KEYS[key]), "trigger": name + "-trigger"}).json()["id"]
    assert [p["id"] for p in resolve([{"id": ids["clip"]}], "video")] == [ids["clip"]]
    got = resolve([{"id": ids["picture"], "strength": 0.8}], "image", job="scout")
    assert got[0]["strength"] == 0.8 and got[0]["trigger"] == "picture-trigger" and got[0]["kind"] == "qwen-image"
    assert resolve([{"id": ids["picture"]}], "image")  # Edit a picture, the default job of a picture
    assert "cannot go on a clip" in refused(lambda: resolve([{"id": ids["picture"]}], "video"))
    assert "cannot go on a new picture" in refused(lambda: resolve([{"id": ids["clip"]}], "image", job="scout"))
    assert "cannot go on Edit a picture" in refused(lambda: resolve([{"id": ids["clip"]}], "image"))
    for job, target in (("video", "video"), ("scout", "image"), ("edit", "image")):
        assert "stored in the hub" in refused(lambda: resolve([{"id": ids["flux"]}], target, job=job))
    assert "at most" in refused(lambda: resolve([{"id": ids["picture"]}] * (loras.LIMIT + 1), "image"))


def test_a_mac_merges_picture_loras_in_edit_a_picture_and_says_why_not_in_a_new_picture():
    home, resolve, _, client = hub(spark=False)
    added = client.post("/api/loras", json={"name": "picture", "file": upload(home, "p.safetensors", KEYS["qwen-image 2.1 (ai-toolkit)"])}).json()
    assert added["jobs"] == ["edit"] and "fused" in added["jobs_why"]["scout"]
    assert resolve([{"id": added["id"]}], "image", job="edit")[0]["path"].endswith(f"{added['id']}.safetensors")
    assert "cannot go on a new picture here" in refused(lambda: resolve([{"id": added["id"]}], "image", job="scout"))
    # a clip LoRA on a Mac is still rewritten for the MLX engine, a picture LoRA is read as it is
    assert [kind for kind in (lora_kinds.jobs_for("h3", False), lora_kinds.jobs_for("flux", False))] == [("video",), ()]


def test_a_new_picture_carries_the_picked_loras_to_the_renderer():
    import picture_edit
    import server
    home, resolve, _, client = hub(spark=True)
    added = client.post("/api/loras", json={"name": "Face", "file": upload(home, "p.safetensors", KEYS["qwen-image 2.1 (ai-toolkit)"]), "trigger": "face-trigger", "strength": 1.0}).json()
    had = server.lora_resolve, picture_edit.base_installed
    server.lora_resolve, picture_edit.base_installed = resolve, lambda spark: True
    try:
        command, env, outputs = server.build("scout", {"prompt": "a woman by a truck", "count": 2, "seed": 3, "width": 1024, "height": 576,
                                                       "loras": [{"id": added["id"], "strength": 1.0}]}, "t1")
        assert command[:2] == ["bash", str(ROOT / "scripts/scout.sh")] and command[2] == "face-trigger a woman by a truck"
        assert env["LORA"] == f"{home / 'loras' / (added['id'] + '.safetensors')}:1" and env["TURBO"] == "0"  # the base model they are trained on
        assert [Path(o).name for o in outputs] == ["scout_s3.png", "scout_s4.png", "sheet.jpg"]
        # a trigger already in the prompt is not written twice; "base": false keeps the Turbo model
        command, env, _ = server.build("scout", {"prompt": "face-trigger smiling", "loras": [{"id": added["id"]}], "base": False}, "t2")
        assert command[2] == "face-trigger smiling" and env["TURBO"] == "1"
        # no LoRA: the job is exactly what it was
        command, env, _ = server.build("scout", {"prompt": "a truck"}, "t3")
        assert command[2] == "a truck" and "LORA" not in env and "TURBO" not in env
        picture_edit.base_installed = lambda spark: False
        assert "base model is not installed" in refused(lambda: server.build("scout", {"prompt": "x", "loras": [{"id": added["id"]}], "base": True}, "t4"))
        clip = client.post("/api/loras", json={"name": "Clip", "file": upload(home, "c.safetensors", KEYS["h3 (ai-toolkit)"])}).json()
        assert "cannot go on a new picture" in refused(lambda: server.build("scout", {"prompt": "x", "loras": [{"id": clip["id"]}]}, "t5"))
    finally:
        server.lora_resolve, picture_edit.base_installed = had


def test_the_spark_picture_graph_merges_the_loras_in_order():
    import spark_generate as G

    class Args:
        base, steps, cfg, negative, width, height = True, 40, 4.0, "", 1024, 576

    plain = G.image_graph(Args, "a truck", 1, [])
    assert not [k for k, v in plain.items() if v["class_type"] == "LoraLoaderModelOnly"] and plain["12"]["inputs"]["model"] == ["1", 0]
    graph = G.image_graph(Args, "a truck", 1, [("hub_a.safetensors", 1.0), ("hub_b.safetensors", 0.5)])
    assert graph["80"]["inputs"] == {"model": ["1", 0], "lora_name": "hub_a.safetensors", "strength_model": 1.0}
    assert graph["81"]["inputs"] == {"model": ["80", 0], "lora_name": "hub_b.safetensors", "strength_model": 0.5}
    assert graph["12"]["class_type"] == "KSampler" and graph["12"]["inputs"]["model"] == ["81", 0]

    class Turbo(Args):
        base = False

    turbo = G.image_graph(Turbo, "a truck", 1, [("hub_a.safetensors", 1.0)])
    assert turbo["11"]["inputs"]["model"] == ["80", 0] and turbo["1"]["inputs"]["unet_name"] == G.QWEN


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
