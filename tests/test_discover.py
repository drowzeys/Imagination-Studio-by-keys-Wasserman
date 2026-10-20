"""Models & weights (app/discover.py): a made-up machine in a temporary folder, with decoys. No network, no GPU.
python3 -m pytest tests/test_discover.py"""
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import discover  # noqa: E402
import loras  # noqa: E402  (imported before any test stands in a home folder: their paths are read at import)
import music  # noqa: E402
import voice  # noqa: E402


def tensors(path: Path, keys=("model.layers.0.weight",), pad: int = 0) -> str:
    head = json.dumps({k: {"dtype": "F16", "shape": [1], "data_offsets": [2 * i, 2 * i + 2]} for i, k in enumerate(keys)}).encode()
    data = struct.pack("<Q", len(head)) + head + b"\0\0" * len(keys) + b"\0" * pad
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def lora(path: Path) -> str:
    return tensors(path, ("diffusion_model.blocks.0.attn.qkv.lora_A.weight", "diffusion_model.blocks.0.attn.qkv.lora_B.weight"))


def checkpoint(folder: Path, model_type: str, codec: bool) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text(json.dumps({"model_type": model_type}))
    shards = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    (folder / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": shards[0], "b": shards[1]}}))
    for s in shards:
        tensors(folder / s)
    if codec:
        (folder / "codec.pth").write_bytes(b"PK" + b"\0" * 100)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A Spark-like machine: home, the studio's folders, a data disk; the scan sees only these."""

    home = tmp_path / "home"
    data = tmp_path / "data"
    for p in (home, data):
        p.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("STUDIO_HOME", str(home / "TensorFoldStudio"))
    monkeypatch.setenv("PREFIX", str(home / ".local/opt/tensorfold-studio"))
    monkeypatch.setenv("STUDIO_SCAN_ROOTS", str(data))
    monkeypatch.setenv("STUDIO_SCAN_INDEX", "0")
    monkeypatch.setenv("STUDIO_DISCOVER_DELAY", "-1")
    for name in ("SPARK_MODELS", "DRAMA3_CUDA_MODEL", "DRAMA3_CUDA_CODEC", "DRAMA3_CUDA_BIN", "DRAMA3_CUDA_LIB", "MUSIC3_MODEL",
                 "HF_HOME", "HF_HUB_CACHE", "STUDIO_DISCOVER_AUTO", "MOTION_MODELS", "TF_H3_LIB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(discover, "SPARK", True)
    monkeypatch.setattr(discover, "MAC", False)
    monkeypatch.setattr(discover, "_cache", None)
    monkeypatch.setattr(discover, "_set_env", {})
    (home / "TensorFoldStudio").mkdir()
    return {"home": home, "data": data, "studio": home / "TensorFoldStudio", "models": home / "spark-studio-models"}


def scanned(link=True):
    return discover.scan(link=link, budget=30)


def row(nid):
    return next(n for n in discover.state()["needs"] if n["id"] == nid)


def test_the_catalogue_reads_its_names_from_the_code(machine):
    ids = {n["id"]: n for n in discover.catalogue()}
    for nid in ("drama3-checkpoint", "drama3-codec", "drama3-binary", "drama3-lib", "music3-weights", "music3-binary", "fasth3",
                "fasth3-int8", "ref2va", "ref2va-int8", "ref2va-turbo", "minimax-h3", "h3-text-encoder", "h3-video-vae", "h3-audio-vae",
                "qwen-image-2.1", "qwen-full", "qwen-text-encoder", "qwen-vae", "flux3k-fast", "flux3k-full", "flux3k-text-encoder",
                "flux3k-upsampler", "flux3k-vae", "flux3k-system-prompts", "sam3.1", "h3-latent-upscaler", "tf-h3-engine",
                "whisper-faster", "whisper-openai", "lora:wasserman-80s-horror"):
        assert nid in ids, nid
    # the file names come from the code that loads them, the folders from the installer's fetch lines
    generate = discover.literals(ROOT / "spark" / "spark_generate.py")
    assert ids["fasth3"]["expected"] == machine["models"] / "FastH3-Comfy/diffusion_models" / generate["FASTH3"]["bf16"]
    assert ids["fasth3-int8"]["expected"].name == generate["FASTH3"]["int8"]
    assert ids["h3-text-encoder"]["expected"] == machine["models"] / "FastH3-Comfy/text_encoders" / generate["H3_TEXT"]
    assert ids["h3-video-vae"]["expected"].name == generate["H3_VAE"]  # an os.environ.get default is read too
    assert ids["music3-weights"]["members"] == discover.literals(ROOT / "app" / "music.py")["WEIGHTS"]
    assert ids["drama3-checkpoint"]["expected"] == machine["home"] / "models" / "drama-3"
    presets = json.loads((ROOT / "app/lora-presets.json").read_text())
    assert ids["lora:wasserman-80s-horror"]["sha256"] == next(p["sha256"] for p in presets if p["id"] == "wasserman-80s-horror")
    # no machine's own names in it: everything is under the (made-up) home or the prefix
    for item in ids.values():
        assert str(item["expected"]).startswith(str(machine["home"])), item["id"]


def test_the_scan_finds_proves_and_ignores_decoys(machine):
    home, data = machine["home"], machine["data"]
    checkpoint(home / "models/fishaudio/drama-3", "fish_omni", True)  # the real one, in another folder
    checkpoint(home / "models/some-llm", "llama", False)  # a decoy: the same file names, another model
    checkpoint(data / "half/drama-3", "fish_omni", True)
    (data / "half/drama-3/model-00002-of-00002.safetensors").unlink()  # a decoy: a shard missing
    (data / "junk").mkdir()
    (data / "junk/codec-v1.safetensors").write_bytes(b"not a model")  # a decoy: the name, not the content
    (home / "node_modules/x").mkdir(parents=True)
    checkpoint(home / "node_modules/x/drama-3", "fish_omni", True)  # never looked at
    out = scanned(link=False)
    found = out["needs"]["drama3-checkpoint"]["candidates"]
    assert [c["path"] for c in found] == [str(home / "models/fishaudio/drama-3")]
    assert found[0]["verified"] and found[0]["shown"] == "~/models/fishaudio/drama-3"
    assert out["needs"]["drama3-codec"]["candidates"] == []
    # the codec is there only as the checkpoint's codec.pth: that needs a conversion, said with the command
    conv = out["needs"]["drama3-codec"]["conversions"]
    assert conv and conv[0]["path"].endswith("fishaudio/drama-3/codec.pth") and "drama3_codec.py" in conv[0]["convert"]
    assert str(home / "models/fishaudio/drama-3") in conv[0]["convert"]
    assert row("drama3-codec")["state"] == "needs-conversion" and row("drama3-checkpoint")["state"] == "found"
    assert out["scan"]["complete"] and (machine["studio"] / ".discovery.json").is_file()


def test_one_verified_candidate_is_linked_by_itself_and_resolve_follows_env_then_link_then_expected(machine, monkeypatch):
    home = machine["home"]
    checkpoint(home / "elsewhere/drama-3", "fish_omni", True)
    assert discover.resolve("drama3-checkpoint") is None
    scanned()
    expected = home / "models/drama-3"
    assert expected.is_symlink() and os.readlink(expected) == str(home / "elsewhere/drama-3")  # a symlink, nothing moved
    assert (home / "elsewhere/drama-3/config.json").is_file()
    assert discover.resolve("drama3-checkpoint") == expected
    assert row("drama3-checkpoint")["state"] == "expected" and row("drama3-checkpoint")["link"]["how"] == "symlink"
    other = home / "mine"
    checkpoint(other, "fish_omni", True)
    monkeypatch.setenv("DRAMA3_CUDA_MODEL", str(other))  # a variable the user set wins
    assert discover.resolve("drama3-checkpoint") == other
    monkeypatch.delenv("DRAMA3_CUDA_MODEL")
    discover.unlink("drama3-checkpoint")
    assert not expected.exists() and (home / "elsewhere/drama-3/config.json").is_file()
    assert discover.resolve("drama3-checkpoint") is None


def test_a_folder_that_cannot_be_written_gets_a_recorded_link(machine, monkeypatch):
    home = machine["home"]
    checkpoint(home / "elsewhere/drama-3", "fish_omni", True)
    locked = home / "locked"
    locked.mkdir()
    monkeypatch.setenv("DRAMA3_CUDA_MODEL", str(locked / "drama-3"))
    locked.chmod(0o500)
    try:
        scanned(link=False)
        record = discover.use("drama3-checkpoint", str(home / "elsewhere/drama-3"))
        assert record["how"] == "record" and not (locked / "drama-3").exists()
        # the user's own variable is kept; the loader reads the link through resolve
        assert os.environ["DRAMA3_CUDA_MODEL"] == str(locked / "drama-3")
        assert discover.resolve("drama3-checkpoint") == home / "elsewhere/drama-3"
    finally:
        locked.chmod(0o700)


def test_a_recorded_link_sets_the_variable_for_this_process_when_the_user_did_not(machine, monkeypatch):
    home = machine["home"]
    checkpoint(home / "elsewhere/drama-3", "fish_omni", True)
    scanned(link=False)
    real = os.symlink
    monkeypatch.setattr(os, "symlink", lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only")))
    record = discover.use("drama3-checkpoint", str(home / "elsewhere/drama-3"))
    monkeypatch.setattr(os, "symlink", real)
    assert record["how"] == "record" and record["env"] == "DRAMA3_CUDA_MODEL"
    assert os.environ["DRAMA3_CUDA_MODEL"] == str(home / "elsewhere/drama-3")
    # the expected place stays the default (the variable is the studio's own), and resolve finds the link
    assert discover.by_id("drama3-checkpoint")["expected"] == home / "models/drama-3"
    assert discover.resolve("drama3-checkpoint") == home / "elsewhere/drama-3"
    discover.unlink("drama3-checkpoint")
    assert "DRAMA3_CUDA_MODEL" not in os.environ
    monkeypatch.delenv("DRAMA3_CUDA_MODEL", raising=False)


def test_several_candidates_wait_for_a_choice_and_only_found_paths_are_linked(machine):
    home, data = machine["home"], machine["data"]
    checkpoint(home / "a/drama-3", "fish_omni", True)
    checkpoint(data / "b/drama-3", "fish_omni", True)
    scanned()
    assert not (home / "models/drama-3").exists()  # two: the person picks
    assert len(row("drama3-checkpoint")["candidates"]) == 2
    with pytest.raises(ValueError):
        discover.use("drama3-checkpoint", str(home))  # not something the scan found
    discover.use("drama3-checkpoint", str(data / "b/drama-3"))
    assert os.readlink(home / "models/drama-3") == str(data / "b/drama-3")


def test_a_users_own_file_or_symlink_is_never_replaced_or_unlinked(machine):
    home = machine["home"]
    checkpoint(home / "a/drama-3", "fish_omni", True)
    (home / "models").mkdir()
    os.symlink(str(home / "nowhere"), home / "models/drama-3")  # the user's own (broken) link
    scanned()
    assert os.readlink(home / "models/drama-3") == str(home / "nowhere")
    with pytest.raises(ValueError):
        discover.use("drama3-checkpoint", str(home / "a/drama-3"))
    with pytest.raises(LookupError):
        discover.unlink("drama3-checkpoint")
    assert os.readlink(home / "models/drama-3") == str(home / "nowhere")


def test_comfy_files_loras_and_text_encoders_are_found_in_caches_and_linked_where_comfyui_lists_them(machine):
    home, data, models = machine["home"], machine["data"], machine["models"]
    generate = discover.literals(ROOT / "spark" / "spark_generate.py")
    snapshot = home / ".cache/huggingface/hub/models--FastVideo--FastVideo-FastH3-Comfy/snapshots/abc/diffusion_models"
    blob = home / ".cache/huggingface/hub/models--FastVideo--FastVideo-FastH3-Comfy/blobs/123"
    tensors(blob, pad=2 << 20)
    snapshot.mkdir(parents=True)
    os.symlink(str(blob), snapshot / generate["FASTH3"]["bf16"])  # a Hugging Face snapshot is made of symlinks
    tensors(data / "tiny" / generate["QWEN"])  # a decoy: the right name, under a megabyte
    presets = {p["id"]: p for p in json.loads((ROOT / "app/lora-presets.json").read_text())}
    horror = presets["wasserman-80s-horror"]
    work = data / "trainer/release"
    digest = lora(work / "80s_horror_wk.safetensors")
    tensors(work / "80s_horror_wk_comfy.safetensors", ("blocks.0.attn.qkv.lora_down.weight", "blocks.0.attn.qkv.lora_up.weight"))  # another layout
    other = lora(data / "loras/someone_style.safetensors")  # a LoRA no preset names
    tensors(work.parent / "output/80s_horror_wk-step00000100.safetensors", ("blocks.0.lora_down.weight",))  # a training step
    tensors(data / "loras/not_a_lora.safetensors")
    encoder = data / "encoders/text_encoders/qwen3vl_32b_minimax_h3_int8_convrot_custom.safetensors"
    tensors(encoder, pad=0)
    with open(encoder, "ab") as f:
        f.truncate((1 << 30) + 64)  # sparse: a gigabyte that takes no disk
    original = {**horror}
    try:
        horror["sha256"] = digest  # the made-up file stands in for the author's
        (machine["studio"] / "loras").mkdir()
        (machine["studio"] / "loras/hub.json").write_text(json.dumps([{**horror, "id": "made-up-horror"}]))
        out = scanned()
    finally:
        horror.update(original)
    fasth3 = models / "FastH3-Comfy/diffusion_models" / generate["FASTH3"]["bf16"]
    assert fasth3.is_symlink() and os.readlink(fasth3) == str(snapshot / generate["FASTH3"]["bf16"])
    assert out["needs"]["qwen-image-2.1"]["candidates"] == []
    # the hub's own recipe with that sha256: the exact file proved and linked; the _comfy one only offered
    made = out["needs"]["lora:made-up-horror"]["candidates"]
    assert made[0]["path"] == str(work / "80s_horror_wk.safetensors") and made[0]["proof"] == "sha256"
    assert [c["verified"] for c in made] == [True, False]
    assert all("step" not in c["path"] for c in made)  # a training step's checkpoint is not offered
    # one proved by its sha256 (the other only offered): linked by itself
    assert os.readlink(machine["studio"] / "loras/made-up-horror.safetensors") == str(work / "80s_horror_wk.safetensors")
    # an unlisted LoRA is listed, never linked by itself; linking puts it in ComfyUI's loras folder
    (home / ".local/opt/tensorfold-studio/ComfyUI/models/loras").mkdir(parents=True)
    out = scanned()
    unlisted = row("lora-unlisted:someone_style.safetensors")
    assert unlisted["state"] == "found" and unlisted["candidates"][0]["path"] == str(data / "loras/someone_style.safetensors") and other
    assert not any(n["id"] == "lora-unlisted:not_a_lora.safetensors" for n in discover.state()["needs"])
    discover.use("lora-unlisted:someone_style.safetensors", str(data / "loras/someone_style.safetensors"))
    assert (home / ".local/opt/tensorfold-studio/ComfyUI/models/loras/someone_style.safetensors").is_symlink()
    # another MiniMax H3 text encoder: linked where the Create wizard's chooser (CLIPLoader's folder) lists it
    te = models / "FastH3-Comfy/text_encoders" / encoder.name
    assert te.is_symlink() and os.readlink(te) == str(encoder)


def test_music3_in_the_original_layout_needs_conversion_and_the_comfy_layout_links(machine):
    home, data = machine["home"], machine["data"]
    original = home / "minimax-music3/model"
    original.mkdir(parents=True)
    (original / "config.json").write_text(json.dumps({"model_type": "minimax_music3"}))
    (original / "dav.pth").write_bytes(b"PK")
    (original / "flowmatching_vae.pth").write_bytes(b"PK")
    scanned()
    music = row("music3-weights")
    assert music["state"] == "needs-conversion" and "Comfy-Org" in music["conversions"][0]["note"]
    weights = discover.literals(ROOT / "app" / "music.py")["WEIGHTS"]
    for part in weights:
        tensors(data / "comfy/MiniMax-Music3-Comfy" / part)
    scanned()
    assert os.readlink(machine["models"] / "MiniMax-Music3-Comfy") == str(data / "comfy/MiniMax-Music3-Comfy")


def test_the_hooks_consult_it(machine, monkeypatch):
    home = machine["home"]
    checkpoint(home / "elsewhere/drama-3", "fish_omni", True)
    scanned(link=False)
    monkeypatch.setattr(voice, "discover", discover)
    hint = voice.found_hint("drama3-checkpoint")
    assert "Not at ~/models/drama-3; found at ~/elsewhere/drama-3" in hint and "Models & weights" in hint
    assert "drama3_codec.py" in voice.found_hint("drama3-codec")
    assert "Not found on this machine after a scan at" in voice.found_hint("drama3-binary")
    discover.use("drama3-checkpoint", str(home / "elsewhere/drama-3"))
    there = lambda p: (p / "config.json").is_file()  # noqa: E731
    assert voice.located("drama3-checkpoint", home / "nowhere", there) == home / "models/drama-3"
    # the CUDA engine's own check takes the linked checkpoint and says what else is missing, with the scan's word
    monkeypatch.setattr(sys, "platform", "linux")
    one = voice.Cuda()
    binary = home / "bin/drama3-generate"
    binary.parent.mkdir()
    binary.write_bytes(b"\x7fELF")
    binary.chmod(0o755)
    one.bin, one.lib, one.model = binary, binary, home / "missing"
    why = one.look()
    assert one.model == home / "models/drama-3" and "No converted codec" in why and "drama3_codec.py" in why
    # music: the weights a scan linked are what the CUDA route reads
    weights = music.WEIGHTS
    for part in weights:
        tensors(machine["data"] / "m/MiniMax-Music3-Comfy" / part)
    monkeypatch.setattr(music, "SPARK", True)
    scanned()
    cuda = music.Cuda()
    cuda.model = home / "nowhere"
    cuda.bin = binary
    assert cuda.present() == "" and cuda.model == machine["models"] / "MiniMax-Music3-Comfy"


def test_the_lora_hub_links_a_found_file_instead_of_downloading(machine, monkeypatch):
    presets = {p["id"]: p for p in json.loads((ROOT / "app/lora-presets.json").read_text())}
    work = machine["data"] / "release"
    digest = lora(work / "80s_horror_wk.safetensors")
    (machine["studio"] / "loras").mkdir()
    (machine["studio"] / "loras/hub.json").write_text(json.dumps([{**presets["wasserman-80s-horror"], "id": "found-horror", "sha256": digest}]))
    scanned(link=False)
    monkeypatch.setattr(loras, "discover", discover)
    assert loras.found_here("found-horror")["path"] == str(work / "80s_horror_wk.safetensors")
    target = machine["studio"] / "loras/found-horror.safetensors"
    monkeypatch.setattr(loras, "download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("downloaded")))
    assert loras.link_found("found-horror", target) and target.is_symlink()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest


def test_the_api_answers_and_guards_its_writes(machine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    home = machine["home"]
    checkpoint(home / "a/drama-3", "fish_omni", True)
    checkpoint(home / "b/drama-3", "fish_omni", True)
    app = FastAPI()
    app.include_router(discover.attach(machine["studio"], home / ".local/opt/tensorfold-studio"))
    client = TestClient(app)
    scanned()
    view = client.get("/api/discover").json()
    assert {"needs", "scanning", "last_scan", "note"} <= set(view) and "Nothing is moved" in view["note"]
    assert next(n for n in view["needs"] if n["id"] == "drama3-checkpoint")["state"] == "found"
    # a write from another site, or not as JSON, is refused
    assert client.post("/api/discover/use", json={"need": "drama3-checkpoint", "path": str(home / "a/drama-3")},
                       headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/discover/use", content="need=x", headers={"content-type": "application/x-www-form-urlencoded"}).status_code == 415
    assert client.post("/api/discover/use", json={"need": "drama3-checkpoint", "path": "/etc"}).status_code == 400
    assert client.post("/api/discover/use", json={"need": "nothing", "path": "/etc"}).status_code == 404
    done = client.post("/api/discover/use", json={"need": "drama3-checkpoint", "path": str(home / "a/drama-3")})
    assert done.status_code == 200 and (home / "models/drama-3").is_symlink()
    assert client.post("/api/discover/unlink", json={"need": "drama3-checkpoint"}).status_code == 200
    assert not (home / "models/drama-3").exists()
    assert client.post("/api/discover/scan", json={}).status_code == 200
    import time
    for _ in range(600):  # the scan it started ends inside this test's made-up machine
        if not discover.scanning():
            break
        time.sleep(0.1)
    assert not discover.scanning()


def test_the_command_line_links_and_reports(machine, capsys):
    home = machine["home"]
    checkpoint(home / "elsewhere/drama-3", "fish_omni", True)
    assert discover.main(["--scan", "--seconds", "20"]) == 0
    printed = capsys.readouterr().out
    assert "drama3-checkpoint" in printed and "~/elsewhere/drama-3" in printed and not (home / "models/drama-3").exists()
    assert discover.main(["--scan", "--link", "--max-age", "3600", "--paths", str(home / "models")]) == 0
    printed = capsys.readouterr().out
    assert printed.strip() == "~/models/drama-3 -> ~/elsewhere/drama-3"


def test_an_installer_gate_links_a_folder_asked_for_by_a_file_in_it(machine, capsys):
    weights = discover.literals(ROOT / "app" / "music.py")["WEIGHTS"]
    for part in weights:
        tensors(machine["data"] / "kept/MiniMax-Music3-Comfy" / part)
    checkpoint(machine["home"] / "elsewhere/drama-3", "fish_omni", True)
    asked = [str(machine["models"] / "MiniMax-Music3-Comfy" / part) for part in weights]
    assert discover.main(["--scan", "--link", "--max-age", "3600", "--paths", *asked]) == 0
    assert capsys.readouterr().out.strip() == "~/spark-studio-models/MiniMax-Music3-Comfy -> " + str(machine["data"] / "kept/MiniMax-Music3-Comfy")
    assert not (machine["home"] / "models/drama-3").exists()  # a gate links only what it asked for
