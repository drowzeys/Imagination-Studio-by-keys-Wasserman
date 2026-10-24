"""The DGX Spark memory guard (spark/memory_guard.py) and where the app and the scripts call it:
python3 tests/test_memory_guard.py. No GPU, no network; the tests that stop a process stop a `sleep` they started."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
os.environ["PREFIX"] = tempfile.mkdtemp()  # no ComfyUI server of this machine is looked at ...
os.environ["SPARK_COMFY_PORT"] = "1"       # ... nor found by its port
sys.path.insert(0, str(ROOT / "spark"))
sys.path.insert(0, str(ROOT / "app"))
import memory_guard as G  # noqa: E402

SPARK_MB, IDLE = 124546, 118700  # the machine the estimates were measured on: all of it, and what is available when idle
LINUX = bool(G.meminfo())


def assert_room_matches(alive_server: bool) -> None:
    """room_mb() against this machine. A Spark's room is its RAM. A discrete card's is the smaller of that and the card."""

    info = G.meminfo()
    room = G.room_mb()
    card = G.device_room_mb()
    ram = info["MemAvailable"]
    if alive_server:
        ram = max(ram, info["MemTotal"] - G.IDLE_MB)
    expect = ram if card is None else min(ram, card + G.floor_mb(info.get("MemTotal")))
    assert abs(room - expect) < 2000


def test_rows_are_the_models_own():
    # TensorFold's node logs 39546 rows for 1376x768, 124 frames (the video's 39216 and the text and sound rows)
    assert G.rows(1376, 768, 124) == 39216
    assert G.rows(864, 480, 124) == 15390
    assert G.rows(864, 480, 362) == 43740  # the README's "44.6K rows" with the other rows
    assert G.rows(864, 480, 124, pictures=2) == 15390 + 2 * 405
    assert G.rows(864, 480, 124, reference_frames=124) == 2 * 15390


def test_the_floor_is_a_tenth_of_the_machine():
    had = os.environ.pop("SPARK_MEMORY_FLOOR_MB", None)
    try:
        assert G.floor_mb(SPARK_MB) == 12454
        assert G.floor_mb(32000) == 8000  # never under 8 GB
        os.environ["SPARK_MEMORY_FLOOR_MB"] = "30000"
        assert G.floor_mb(SPARK_MB) == 30000
    finally:
        os.environ.pop("SPARK_MEMORY_FLOOR_MB", None)
        if had:
            os.environ["SPARK_MEMORY_FLOOR_MB"] = had


def test_the_clip_that_took_a_spark_down_is_refused():
    """1376x768, 124 frames, FastH3 8 passes, Camera Motion at 0.9: accepted on 2026-10-13 on ComfyUI's bfloat16
    blocks, and the machine was lost. Where the LoRA still goes there (an engine that cannot take one) it is refused."""

    found = G.check(1376, 768, 124, lora=True, engine=False, available_mb=IDLE, total_mb=SPARK_MB)
    assert found["fits"] is False and found["route"] == "bf16"
    assert "refused" in found["reason"] and "LoRA" in found["reason"] and "What fits" in found["reason"]
    near = found["nearest"]
    assert near["size"] == [1280, 736] and near["frames"] is None  # no length of a 1376x768 LoRA clip was ever measured
    assert "1280x736 at this length" in found["reason"]
    # what it offers does fit: it is the largest LoRA clip that was measured there
    assert G.check(*near["size"], 124, lora=True, engine=False, available_mb=IDLE, total_mb=SPARK_MB)["fits"]
    # on the engine, with the LoRA merged into its int8 weights, the same clip was measured and fits
    merged = G.check(1376, 768, 124, lora=True, available_mb=IDLE, total_mb=SPARK_MB)
    assert merged["fits"] is True and merged["route"] == "int8-lora"
    # the same clip without the LoRA is the film's own setting, rendered 45 times that day
    assert G.check(1376, 768, 124, available_mb=IDLE, total_mb=SPARK_MB)["fits"]
    assert G.check(1376, 768, 243, available_mb=IDLE, total_mb=SPARK_MB)["fits"]


def test_a_lora_clip_is_never_taken_above_what_was_measured():
    for engine, route, pixels in ((False, "bf16", 1280 * 736), (True, "int8-lora", 1376 * 768)):
        most = G.MEASURED_ROWS[route]
        for width, height in G.SIZES:
            for frames in G.counts():
                found = G.check(width, height, frames, lora=True, engine=engine, available_mb=IDLE, total_mb=SPARK_MB)
                assert found["route"] == route
                assert found["fits"] == (G.rows(width, height, frames) <= most and width * height <= pixels), (route, width, height, frames)
    # more memory does not change that: where it starts is not known
    assert not G.check(1376, 768, 124, lora=True, engine=False, available_mb=10 * IDLE, total_mb=10 * SPARK_MB)["fits"]
    over = G.check(1376, 768, 260, lora=True, available_mb=10 * IDLE, total_mb=10 * SPARK_MB)
    assert not over["fits"] and "1376x768, 243 frames" in over["reason"] and over["nearest"]["frames"] == 243


def test_which_route_a_lora_takes():
    assert G.route_of(lora=True) == "int8-lora" and G.route_of() == "int8"
    assert G.route_of(lora=True, engine=False) == "bf16"
    assert G.route_of(lora=True, references=True) == "bf16" and G.route_of(lora=True, base=True) == "bf16"
    with tempfile.TemporaryDirectory() as folder:
        old, new = Path(folder) / "old.so", Path(folder) / "new.so"
        old.write_bytes(b"\x7fELF tf_h3_open tf_h3_forward")
        new.write_bytes(b"\x7fELF tf_h3_open tf_h3_forward tf_h3_set_weight")
        assert not G.engine_takes_lora(old) and G.engine_takes_lora(new)
        assert not G.engine_takes_lora(Path(folder) / "missing.so")


def test_the_estimate_follows_the_measured_points():
    for route, points in G.MEASURED.items():
        for count, need in points:
            assert abs(G.need_mb(route, count) - need) <= 1700, (route, count, G.need_mb(route, count), need)
    # every size the int8 engine is offered at fits an idle Spark, as it did before the guard
    for width, height in G.SIZES:
        assert G.check(width, height, 362, available_mb=IDLE, total_mb=SPARK_MB)["fits"], (width, height)


def test_another_programs_memory_counts_against_the_clip():
    found = G.check(1376, 768, 243, available_mb=IDLE - 30000, total_mb=SPARK_MB)  # a model server holding 30 GB
    assert found["fits"] is False and "needs about" in found["reason"]
    near = found["nearest"]
    assert near["frames"] or near["size"]
    if near["size"]:
        assert G.check(*near["size"], 243, available_mb=IDLE - 30000, total_mb=SPARK_MB)["fits"]
    nothing = G.check(864, 480, 22, available_mb=30000, total_mb=SPARK_MB)
    assert nothing["fits"] is False and "No smaller size or shorter length" in nothing["reason"]


def test_a_machine_without_proc_meminfo_is_not_judged():
    assert G.check(1376, 768, 124, lora=True, total_mb=0)["fits"] is True  # the Mac build has its own limits


def test_when_the_floor_stops_the_server():
    floor = 12454
    assert G.verdict([(0.0, 50000), (0.25, 49000)], floor) == ""
    assert "under the floor" in G.verdict([(0.0, 13000), (0.05, 12400)], floor)
    # 30 GB available and falling at 30 GB a second is one second from nothing: stopped although far over the floor
    fast = G.verdict([(0.0, 37500), (0.25, 30000)], floor)
    assert "falling by 30000 MB a second" in fast
    # the fastest falls measured while models load (7.2 and 29.5 GB a second) do not stop a server with room: the
    # int8 engine's 13 GB in half a second came at 81 GB available
    assert G.verdict([(0.0, 61800), (0.25, 60000)], floor) == ""
    assert G.verdict([(0.0, 81000), (0.25, 73600)], floor) == ""
    assert "waiting on memory" in G.verdict([(0.0, 20000), (0.25, 20000)], floor, stalled=0.7)
    assert G.verdict([(0.0, 20000), (0.25, 20000)], floor, stalled=0.2) == ""
    # a cold start's model load stalls on the page cache with half the machine available: not a machine running out
    assert G.verdict([(0.0, 62000), (0.25, 61900)], floor, stalled=0.52) == ""
    assert G.verdict([(0.0, 2 * floor), (0.25, 2 * floor)], floor, stalled=0.9) == ""
    assert "waiting on memory" in G.verdict([(0.0, 2 * floor - 1), (0.25, 2 * floor - 1)], floor, stalled=0.5)
    assert "under the floor" in G.verdict([(0.0, 13000), (0.05, floor - 1)], floor, stalled=0.7)


def test_the_watch_stops_a_process_under_the_floor_and_leaves_one_above_it():
    if not LINUX:
        return
    said = []
    say = lambda text, flush=False: said.append(text)
    victim = subprocess.Popen(["sleep", "60"])
    try:
        started = time.time()
        # a floor over everything this machine has: the first look is under it
        assert G.watch(victim.pid, 10 * G.meminfo()["MemTotal"], say) == 3
        assert victim.wait(timeout=5) == -9 and time.time() - started < 5
        assert any("under the floor" in line and "stopping ComfyUI" in line for line in said), said
    finally:
        victim.poll() is None and victim.kill()
    said.clear()
    short = subprocess.Popen(["sleep", "0.6"])
    had = os.environ.get("SPARK_MEMORY_FREE_MB")
    os.environ["SPARK_MEMORY_FREE_MB"] = "0"  # this test gives no page cache back on the machine it runs on
    try:
        assert G.watch(short.pid, 1, say) == 0  # it ended by itself
        assert short.wait(timeout=5) == 0 and not any("stopping" in line for line in said)
    finally:
        os.environ.pop("SPARK_MEMORY_FREE_MB", None)
        if had is not None:
            os.environ["SPARK_MEMORY_FREE_MB"] = had
    # the guard wrote down what the machine had when the server started
    before, pid = Path(G.state_file()).read_text().split()
    assert int(before) > 0 and int(pid) == short.pid
    assert_room_matches(alive_server=False)  # that server is gone: what is available now
    # a server from before the guard left no note: the machine is taken as otherwise idle, not as full
    had_pid = G.server_pid
    G.server_pid = lambda: 1
    try:
        os.remove(G.state_file())
        assert_room_matches(alive_server=True)
    finally:
        G.server_pid = had_pid


def test_page_cache_of_a_file_is_given_back():
    if not LINUX:
        return
    with tempfile.NamedTemporaryFile(suffix=".safetensors") as handle:
        handle.write(b"\0" * (1 << 20))
        handle.flush()
        os.fsync(handle.fileno())
        assert G.release_cache([handle.name, "/no/such/file"]) == 1


def test_what_a_stopped_render_says():
    import spark_generate as sg

    log = sg.side("log")
    log.write_text("[INFO] got prompt\n[tensorfold] memory floor: 11800 MB available, under the floor of 12454 MB: stopping ComfyUI\n"
                   "[tensorfold] memory floor: the server is gone after 0.4 s, 96000 MB available\n")
    said = sg.stopped()
    assert said.startswith("the render was stopped to protect the machine: 11800 MB available, under the floor of 12454 MB")
    log.write_text("[INFO] got prompt\n")
    assert sg.stopped() == ""


def test_every_start_of_the_server_has_the_guard_beside_it():
    script = (ROOT / "spark/comfy.sh").read_text()
    assert 'memory_guard.py" watch --pid "$pid"' in script
    # with a workload governor, `run` hands the same command to the managed wrapper, which starts the same guard
    assert 'exec python3 "$HERE/managed_comfy.py" run "$PREFIX/venv/bin/python" main.py --listen' in script
    managed = (ROOT / "spark/managed_comfy.py").read_text()
    assert '"memory_guard.py"), "watch", "--pid", str(pid)' in managed and "guard(server.pid, pythons)" in managed
    assert "holding {floor_mb()} MB with a plain loop" in managed  # and the same plain floor when no Python can run it
    # the only places a server is started are comfy.sh's own `start`, which runs `run`
    for path in list((ROOT / "spark").glob("*.py")) + list((ROOT / "spark").glob("*.sh")) + list((ROOT / "scripts").glob("*.sh")) + list((ROOT / "app").glob("*.py")):
        text = path.read_text()
        assert "main.py --listen" not in text or path.name == "comfy.sh", path


def test_the_app_refuses_before_the_job_is_queued():
    import server
    from fastapi.testclient import TestClient

    if not server.SPARK:
        return
    client = TestClient(server.app)
    had = G.room_mb
    G.room_mb = lambda: IDLE  # an idle Spark, whatever this machine is doing now
    try:
        told = client.post("/api/estimate", json={"preset": "sharp", "quality": "fh8", "seconds": 5, "with_lora": True}).json()
        assert told["memory"]["fits"] is False and "What fits" in told["memory"]["reason"]
        fine = client.post("/api/estimate", json={"preset": "sharp", "quality": "fh8", "seconds": 5}).json()
        assert fine["memory"]["fits"] is True and fine["memory"]["need_mb"] > 50000
        # the job itself: the hub's LoRA is stood in for, the rest is the app's own path
        was = server.lora_resolve
        server.lora_resolve = lambda picked, target="video": [{"id": "camera-motion", "name": "Camera Motion", "path": "/x.safetensors", "strength": 0.9, "trigger": "", "lead": "", "comma": False}] if picked else []
        try:
            job = {"kind": "video", "params": {"prompt": "a walk", "preset": "sharp", "quality": "fh8", "seconds": 5, "qa": False,
                                               "loras": [{"id": "camera-motion", "strength": 0.9}]}}
            before = set(server.jobs)  # other tests' finished jobs stay in the queue's history
            refused = client.post("/api/jobs", json=job)
            assert refused.status_code == 400 and "refused" in refused.json()["error"] and "LoRA" in refused.json()["error"], refused.text
            assert set(server.jobs) == before  # nothing was queued
        finally:
            server.lora_resolve = was
    finally:
        G.room_mb = had



def test_a_refusal_names_memory_the_driver_holds():
    """Issue #59: drivers from 590 on keep a finished process's memory in their pools, where no counter shows it."""

    idle = "MemTotal: 127600524 kB\nMemFree: 113077964 kB\nBuffers: 485356 kB\nCached: 6624636 kB\nAnonPages: 1066736 kB\nSlab: 1993500 kB\n"
    pooled = "MemTotal: 127600524 kB\nMemFree: 43077964 kB\nBuffers: 485356 kB\nCached: 6624636 kB\nAnonPages: 1066736 kB\nSlab: 1993500 kB\n"
    assert G.unexplained_mb(G.meminfo(idle)) < G.UNEXPLAINED_IDLE_MB  # a worker Spark with nothing running
    held = G.unexplained_mb(G.meminfo(pooled))
    assert 70_000 < held < 76_000
    had = G.smi, G.processes_mb
    try:
        G.smi, G.processes_mb = (lambda *query: [["NVIDIA GB10"]]), (lambda: {})
        note = G.held_note(G.meminfo(pooled))
        assert f"About {held / 1000:.0f} GB" in note and "NVreg_EnableSystemMemoryPools=0" in note and "R580" in note
        assert G.held_note(G.meminfo(idle)) == ""
        G.processes_mb = lambda: {1234: 60_000}  # a program on the GPU: its memory is its own, not the driver's
        assert G.held_note(G.meminfo(pooled)) == ""
        G.smi, G.processes_mb = (lambda *query: []), (lambda: {})  # no nvidia-smi answer: nothing can be told
        assert G.held_note(G.meminfo(pooled)) == ""
    finally:
        G.smi, G.processes_mb = had


def test_studio_room_mb_stands_in_for_the_cards_room_and_never_beyond_the_ram():
    """A card smaller than the video models: STUDIO_ROOM_MB is the budget, capped by the machine's RAM room."""

    had = G.meminfo, G.server_pid, G.device_room_mb, os.environ.get("STUDIO_ROOM_MB")
    G.meminfo = lambda text=None: {"MemAvailable": 80_000, "MemTotal": 91_000}
    G.server_pid = lambda: None
    try:
        G.device_room_mb = lambda: 25_000  # a 32 GB card
        os.environ.pop("STUDIO_ROOM_MB", None)
        unset = G.room_mb()
        assert unset == 25_000 + G.floor_mb(91_000)  # as before
        os.environ["STUDIO_ROOM_MB"] = "70000"
        assert G.room_mb() == 70_000  # used instead of the card's room
        os.environ["STUDIO_ROOM_MB"] = "200000"
        assert G.room_mb() == 80_000  # never more than the RAM has
        for bad in ("", "  ", "abc", "0", "-5", "7.5"):
            os.environ["STUDIO_ROOM_MB"] = bad
            assert G.room_mb() == unset, bad  # ignored
        G.device_room_mb = lambda: None  # a Spark has no card to stand in for
        os.environ["STUDIO_ROOM_MB"] = "70000"
        assert G.room_mb() == 80_000
    finally:
        G.meminfo, G.server_pid, G.device_room_mb = had[:3]
        if had[3] is None:
            os.environ.pop("STUDIO_ROOM_MB", None)
        else:
            os.environ["STUDIO_ROOM_MB"] = had[3]


def test_the_installer_has_an_escape_for_a_card_under_90_gb():
    setup = (ROOT / "oneshot-setup-cuda.sh").read_text()
    line = next(l for l in setup.splitlines() if 'die "video needs a card of about 96 GB' in l)
    assert 'STUDIO_ALLOW_SMALL_VRAM:-0}" = 1' in line and "docs/rtx-5090.md" in line
    assert '[ "$VRAM_GB" -ge 90 ]' in line  # the default is still the 90 GB floor


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
