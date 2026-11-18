"""What the Sparks page calls each machine: python3 -m pytest tests/test_sparks_naming.py. A worker is named by what
it is (its card, "Mac" or "Windows PC", from what its app says of itself), a sentence about any worker does not call
it a Spark, and a count of machines says "Sparks" only while they are all DGX Sparks. No GPU and no other machine."""
import sys
import threading
from pathlib import Path

from test_local_gpus_page import run

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import sparks  # noqa: E402

STATION = "user@workstation"
SPARK = "user@spark-2"
PLAIN = {"prompt": "x", "preset": "small", "quality": "fh8", "frames": 124}


def info(gpu: str | None, platform: str = "spark", **gpu_more) -> dict:
    """What a worker's app says of itself (/api/spark/self), with its readings (telemetry.compact) naming its card."""

    status = {"platform": platform, "gpu": {"name": gpu, "util_pct": 0, **gpu_more} if gpu else None}
    return {"platform": platform, "fasth3": True, "guard": True, "loras": [], "text_encoders": [], "memory": {"available_mb": 200000},
            "disk_gb": 900, "code": {}, "status": status}


def a_hub(home: Path, *workers: tuple[str, dict | None], mine: dict | None = None) -> sparks.Hub:
    mine = mine or {"version": "4.5.0", "disk_gb": 900, "platform": "spark"}
    hub = sparks.Hub(home, ROOT, True, {}, [], threading.Lock(), threading.Event(), lambda: mine,
                     lambda path: home / path, lambda job, worker: None, lambda kind, params: 60)
    hub.keep({"workers": [{"address": address, "name": address.split("@")[1], "folder": "Imagination-Studio", "enabled": True}
                          for address, _ in workers], "sparks": 3})
    for address, said in workers:
        if said is not None:
            hub.state(address).update(state="ready", info=said)
    return hub


def test_a_card_is_called_by_its_name_and_a_gb10_is_a_dgx_spark():
    assert sparks.gpu_word("NVIDIA GB10") == "DGX Spark" and sparks.gpu_word("GB10") == "DGX Spark"
    assert sparks.gpu_word("NVIDIA GB300") == "GB300"
    assert sparks.gpu_word("NVIDIA RTX PRO 6000 Blackwell Workstation Edition") == "RTX PRO 6000 Blackwell Workstation Edition"
    assert sparks.gpu_word("NVIDIA GB300", 2) == "2× GB300"  # paired cards of one name (STUDIO_GPUS)
    assert sparks.gpu_word("NVIDIA GB300 + NVIDIA RTX PRO 6000") == "GB300 + RTX PRO 6000"
    assert sparks.gpu_word("NVIDIA GB100") == "GB100"  # not a GB10
    assert sparks.gpu_word(None) == "" and sparks.gpu_word("") == ""


def test_what_a_machine_is_comes_from_what_its_app_says():
    assert sparks.what_of(info("NVIDIA GB300")) == "GB300"
    assert sparks.what_of(info("NVIDIA GB10")) == "DGX Spark"
    assert sparks.what_of(info(None, "mac")) == "Mac" and sparks.what_of(info("NVIDIA RTX 5090", "windows")) == "Windows PC"
    # a Linux machine that names no card: an app from before its readings were sent, or no answer from nvidia-smi
    assert sparks.what_of({"platform": "spark"}) == "" and sparks.what_of(info(None)) == "" and sparks.what_of(None) == ""


def test_the_list_says_what_each_worker_is_and_keeps_it_while_one_is_down(tmp_path):
    hub = a_hub(tmp_path, (STATION, info("NVIDIA GB300")), (SPARK, info("NVIDIA GB10")), ("user@new-one", None))
    rows = {row["address"]: row for row in hub.overview()["workers"]}
    assert rows[STATION]["what"] == "GB300" and rows[SPARK]["what"] == "DGX Spark" and rows["user@new-one"]["what"] == ""
    assert rows[STATION]["label"] == "workstation"  # the name stays the machine's; what it is is its own field
    assert hub.overview()["this"]["what"] == ""  # this head's own readings name no card here
    # a worker that stops answering is still what it was
    hub.state(STATION).update(state="down", info=None, why="It stopped answering for 60 s")
    assert {row["address"]: row for row in hub.overview()["workers"]}[STATION]["what"] == "GB300"
    # this machine, from its own readings
    assert a_hub(tmp_path / "head", mine={**info("NVIDIA GB10"), "version": "4.5.0"}).overview()["this"]["what"] == "DGX Spark"
    # what ssh found, before any app answered
    hub.state("user@new-one")["platform"] = "mac"
    assert hub.what("user@new-one") == "Mac"


def test_a_worker_that_goes_down_before_the_page_asks_is_still_what_it_was(tmp_path):
    hub = a_hub(tmp_path, (STATION, None))
    live = hub.state(STATION)
    live.update(state="ready", info=info("NVIDIA GB300"))  # an answer, kept as the loop and a check keep it
    live["info"] = None  # and gone again, with nothing read in between
    live.update(state="down", info=None)
    assert hub.what(STATION) == "GB300"
    # a later answer from another card replaces it
    live["info"] = info("NVIDIA GB10")
    live.update(info=None)
    assert hub.what(STATION) == "DGX Spark"


def test_a_clip_cancelled_on_a_worker_says_what_the_worker_is(tmp_path, monkeypatch):
    """A worker's job that ends cancelled with no error of its own goes back with "cancelled on that GB300"."""

    def answer(base, path, body=None, method=None, timeout=sparks.CALL):
        return {"memory": {"fits": True}} if path == "/api/estimate" else {"id": "j1"} if path == "/api/jobs" else {"status": "cancelled"}

    monkeypatch.setattr(sparks, "call", answer)
    for said, word in ((info("NVIDIA GB300"), "GB300"), ({**info(None), "status": None}, "machine")):
        hub = a_hub(tmp_path / word, (STATION, said))
        moved = []
        hub.connect = lambda address: "http://127.0.0.1:1"
        hub.move = lambda job, worker, why, down: moved.append(why)
        job = {"id": "a", "status": "running", "kind": "video", "title": "x", "log": ""}
        assert hub.render(job, hub.workers()[0], PLAIN, tmp_path / "x.mp4") is False
        assert moved == [f"its render failed: cancelled on that {word}"]


def test_a_worker_busy_with_a_job_that_is_not_ours_says_so_in_one_sentence(tmp_path):
    hub = a_hub(tmp_path, (STATION, info("NVIDIA GB300")))
    hub.state(STATION).update(state="busy", job=None)
    assert hub.usable(hub.workers()[0]) == sparks.BUSY_ELSEWHERE == "rendering a job this Studio did not send"
    assert "Spark" not in sparks.BUSY_ELSEWHERE


def test_a_count_says_sparks_only_while_every_worker_is_a_dgx_spark(tmp_path):
    sparks_only = a_hub(tmp_path / "a", (SPARK, info("NVIDIA GB10")), ("user@older", {**info(None), "status": None}))
    said = sparks_only.plan({"sparks": 3, "kind": "video", "clips": 8, "params": PLAIN})
    assert said["count"] == "3 Sparks" and "One clip per Spark" in said["says"]
    mixed = a_hub(tmp_path / "b", (SPARK, info("NVIDIA GB10")), (STATION, info("NVIDIA GB300")))
    said = mixed.plan({"sparks": 3, "kind": "video", "clips": 8, "params": PLAIN})
    assert said["count"] == "3 machines" and "One clip per machine" in said["says"] and "Spark" not in said["says"]
    assert said["estimate"]["measured"] is False and "another card" in said["estimate"]["note"]
    assert "never split across machines" in mixed.plan({"sparks": 3, "kind": "video", "clips": 1, "params": PLAIN})["says"]
    # what is not handed to a worker says so without calling the worker a Spark
    assert sparks.capable("scout", PLAIN, info("NVIDIA GB300")) == "only single clips are handed to another machine"
    assert sparks.here_only("scout", {}).endswith("only clips are handed to other machines.")
    encoder = {**PLAIN, "text_encoder": "/home/you/ComfyUI/models/text_encoders/other.safetensors"}
    assert sparks.capable("video", encoder, info("NVIDIA GB300")) == "the text encoder file is not on that GB300"
    assert sparks.capable("video", encoder, {**info(None), "status": None}) == "the text encoder file is not on that machine"


PAGE = """
globalThis.S = { platform: "spark", platform_label: "DGX SPARK · NVIDIA GB10 · H3-CUDA" };
const status = { at: Date.now() / 1000, gpu: { util_pct: 3 } };
const d = { supported: true, limit: 5, available: 3, listed: 3, default: 1, placement: null, head: "spark", folder: "~/Studio", names: {},
  this: { name: "spark-1", label: "spark-1", kind: "Spark", what: "DGX Spark", state: "ready", version: "4.5.0", can: {}, status },
  workers: [{ address: "user@workstation", name: "workstation", label: "workstation", kind: "ssh", platform: "spark", what: "GB300", state: "ready", says: "idle", enabled: true, usable: true, version: "4.5.0", can: {}, status },
            { address: "user@spark-2", name: "spark-2", label: "spark-2", kind: "ssh", platform: "spark", what: "DGX Spark", state: "ready", says: "idle", enabled: true, usable: true, version: "4.5.0", can: {}, status }] };
SPK.data = d; const page = viewSparks();
out.page = text(page);
out.rows = all(page, n => n.attrs && n.attrs.class === "seghead").map(text);
out.titles = all(page, n => n.attrs && typeof n.attrs.title === "string").map(n => n.attrs.title);
out.options = select(page, "Default video worker").kids.map(o => text(o));
out.cards = text(spkMachinesPanel());
out.unnamed = select(spkPlacement({ ...d, workers: [{ ...d.workers[0], name: "user@workstation", label: "user@workstation" }] }), "Default video worker").kids.map(o => text(o));
out.counts = [spkCount(d, 3), spkUnit(d)];
const sparksOnly = { ...d, workers: [d.workers[1], { ...d.workers[0], what: "" }] };
out.sparksOnly = [spkCount(sparksOnly, 3), spkUnit(sparksOnly)];
/* a worker that says what it is, and nothing else changed: the page is drawn again, not only its cards */
const said = JSON.parse(JSON.stringify(sparksOnly)); said.workers[1].what = "GB300";
const head = JSON.parse(JSON.stringify(d)); head.this.what = "GB300";
out.redraw = [spkShape(said) !== spkShape(sparksOnly), spkSig(said) !== spkSig(sparksOnly), spkShape(head) !== spkShape(d)];
let drawn = 0; globalThis.render = () => { drawn += 1; }; tab = "Sparks";
SPK.data = sparksOnly; answers["/api/sparks"] = said; await spkLoad(true); out.drawn = drawn; out.unit = spkUnit();
"""


def test_the_page_names_each_machine_by_what_it_is():
    out = run(PAGE)
    assert any("workstation" in row and "GB300 · user@workstation" in row for row in out["rows"])
    assert any("spark-2" in row and "DGX Spark · user@spark-2" in row for row in out["rows"])
    assert "GB300 · workstation · user@workstation" in out["options"] and "Head · spark-1 · DGX Spark" in out["options"]
    assert "GB300 · user@workstation" in out["unnamed"]  # a worker listed by its address is not named twice
    assert "GB300" in out["cards"] and "DGX Spark" in out["cards"]
    # a sentence about any worker does not call it a Spark
    assert "Add a worker" in out["page"] and "Add a Spark" not in out["page"]
    assert "DGX Spark or another Linux machine with an NVIDIA GPU, a Mac, or a Windows PC" in out["page"]
    for words in ("Unticked, this machine is given no clips", "Look at this machine:", "Take this machine off the list"):
        assert any(title.startswith(words) for title in out["titles"]), words
    assert not any("this Spark is given" in title or "Look at this Spark" in title or "Take this Spark" in title for title in out["titles"])
    # a GB300 among the workers: the count is of machines; with DGX Sparks alone (or one that has not said) it stays Sparks
    assert out["counts"] == ["3 of 3 available machines", "machine"]
    assert out["sparksOnly"] == ["3 of 3 available Sparks", "Spark"]
    assert out["redraw"] == [True, True, True]
    assert out["drawn"] == 1 and out["unit"] == "machine"  # a poll that only learns what a worker is redraws the page
