"""Install from Models & weights (app/installs.py, POST /api/discover/install): python3 -m pytest tests/test_installs.py.
No real installer runs: the plans are read from the needs' own words, and the runner runs small shell lines."""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
os.environ["STUDIO_DISCOVER_DELAY"] = "-1"
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import discover  # noqa: E402
import installs  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """discover keeps what it read of its cache file, and an installer's end starts a scan of the machine: neither is
    left behind for another test file (a scan holds the one scan lock for up to two minutes)."""
    discover._cache = None
    monkeypatch.setattr(discover, "start_scan", lambda: False)
    yield
    discover._cache = None


def item(download, nid="x", expected="/tmp/nowhere/x", kind="file"):
    return discover.need(nid, "A model", "Engine", Path(expected), kind, download=download)


def test_each_needs_words_become_the_installer_that_does_it(monkeypatch):
    monkeypatch.setattr(installs, "SPARK", False)  # a Mac's plans; a Spark's are below
    assert installs.plan_of(item("scripts/setup-voice.sh"))["command"][1:] == [str(ROOT / "scripts/setup-voice.sh")]
    music = installs.plan_of(item("oneshot-setup.sh --music (22 GB)"))
    assert music["how"] == "script" and music["command"][1].endswith("scripts/setup-music.sh") and music["size_gb"] == 22  # its own setup, not the whole one-shot
    core = installs.plan_of(item("oneshot-setup.sh --ref2va (62 GB)"))
    assert core["how"] == "oneshot" and core["command"][2:] == ["--no-render", "--ref2va"]  # nothing is rendered
    hf = installs.plan_of(item("hf download fishaudio/drama-3", expected="/m/drama-3", kind="folder"))
    assert hf["command"] == ["hf", "download", "fishaudio/drama-3", "--local-dir", "/m/drama-3"]
    assert installs.plan_of(item("the LoRA hub's Install (from x)", nid="lora:turnaround"))["lora"] == "turnaround"
    assert installs.plan_of(item("downloaded by faster-whisper on first use"))["how"] == "first_use"
    assert installs.plan_of(item("README, Voice: build TensorFold's drama3-cuda branch"))["how"] == "manual"
    for need in discover.all_needs():  # every need of this machine has a plan, and none runs as root
        plan = installs.plan_of(need)
        assert plan["how"] in ("script", "oneshot", "hf", "lora", "first_use", "manual") and "sudo" not in " ".join(plan["command"] or [])


def test_on_a_dgx_spark_every_install_goes_through_its_own_installer(monkeypatch):
    monkeypatch.setattr(installs, "SPARK", True)
    for words, flags in (("oneshot-setup.sh --ref2va (62 GB)", ["--ref2va"]), ("oneshot-setup.sh --music (22 GB)", ["--music"]),
                         ("bash oneshot-setup-spark.sh --sound", ["--sound"]), ("oneshot-setup-spark.sh --moss --turbo", ["--moss"])):
        plan = installs.plan_of(item(words))
        assert plan["how"] == "oneshot" and plan["command"][1].endswith("oneshot-setup-spark.sh"), words
        assert plan["command"][2:] == ["--no-render", *flags], words  # the Mac-only --turbo is left out
    built = installs.plan_of(item("bash spark/build-music3.sh"))
    assert built["how"] == "script" and built["command"][1].endswith("spark/build-music3.sh")


def wait():
    ends = time.time() + 20
    while time.time() < ends and installs.public().get("state") == "running":
        time.sleep(0.05)
    return installs.public()


def test_an_installer_runs_one_at_a_time_and_its_end_is_reported(monkeypatch, tmp_path):
    target = tmp_path / "model.bin"
    monkeypatch.setattr(installs, "plan_of", lambda it: {"how": "script", "shown": "fake", "size_gb": None,
                                                         "command": ["sh", "-c", f"echo fetching; sleep 0.3; echo hf_{'a' * 30}; touch {target}"]})
    first = installs.start(item("x", expected=str(target)))
    assert first["state"] == "running"
    with pytest.raises(ValueError):
        installs.start(item("x", expected=str(target)))
    done = wait()
    assert done["state"] == "done" and target.is_file() and "fetching" in done["lines"]
    assert not any("aaaaaaaaaaaaaaaaaaaa" in line for line in done["lines"])  # a token in the output is not kept


def test_a_gated_download_hands_back_the_gate(monkeypatch, tmp_path):
    import hf_gate
    monkeypatch.setattr(hf_gate, "repo_gate", lambda repo: {"gated": "auto", "access": "accept", "page": f"https://huggingface.co/{repo}"})
    monkeypatch.setattr(installs, "plan_of", lambda it: {"how": "script", "shown": "fake", "size_gb": None, "command": ["sh", "-c",
        "echo 'GatedRepoError: Access to model black-forest-labs/flux-3-image-klein is restricted. https://huggingface.co/black-forest-labs/flux-3-image-klein'; exit 3"]})
    installs.start(item("x", expected=str(tmp_path / "m")))
    done = wait()
    assert done["state"] == "gated" and done["gate"]["repo"] == "black-forest-labs/flux-3-image-klein" and done["gate"]["access"] == "accept"


def test_not_enough_disk_is_said_before_anything_downloads(monkeypatch):
    monkeypatch.setattr(installs, "free_gb", lambda place: 20.0)
    with pytest.raises(ValueError) as error:
        installs.start(item("oneshot-setup.sh --ref2va (62 GB)"))
    assert "62 GB" in str(error.value) and "20 GB free" in str(error.value)


def test_the_routes_install_link_everything_and_refuse_what_cannot_run(monkeypatch):
    assert client.post("/api/discover/install", json={"need": "no-such"}).status_code == 404
    first_use = next((n for n in discover.all_needs() if installs.plan_of(n)["how"] == "first_use"), None)
    if first_use and not discover.present(first_use):
        said = client.post("/api/discover/install", json={"need": first_use["id"]})
        assert said.status_code == 400 and "first time it is used" in said.json()["error"]
    monkeypatch.setattr(discover, "auto_link", lambda places=None: ["fasth3"])
    assert client.post("/api/discover/link-all", json={}).json()["linked"] == ["fasth3"]
    assert client.post("/api/discover/install", json={"need": "fasth3"}, headers={"origin": "https://elsewhere.example"}).status_code == 403
    listed = client.get("/api/discover").json()
    assert all("install" in n for n in listed["needs"]) and "installing" in listed
