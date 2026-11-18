"""A clip too big for this Spark's memory waits for a worker that may render it (busy with another app's job, not
checked yet, or down for a while) instead of being taken here and refused by the memory guard at its start; it fails
with its reason once no worker may take it again. No other machine: the hub's scheduling alone, its workers' state
set by hand."""
import threading
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import local_gpus
import sparks

STATION = "studio@station.invalid"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_GPU_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("STUDIO_GPU_RAM_DIR", str(tmp_path / "ram"))
    monkeypatch.setenv("STUDIO_GPU", "0")
    monkeypatch.delenv("STUDIO_LOCAL_GPU_CHILD", raising=False)
    monkeypatch.delenv("STUDIO_LOCAL_GPU_UUID", raising=False)
    monkeypatch.setattr(local_gpus, "inventory", lambda **kw: {"supported": True, "devices": []})
    info = {"guard": True, "code": {"VERSION": "synthetic"}, "version": "0.0.0", "active": 0,
            "fasth3": True, "ref2va": True, "ref2va_turbo": True, "ffmpeg": True,
            "loras": [], "text_encoders": [], "presets": ["small", "sharp"], "qualities": ["fh8"],
            "memory": {"available_mb": 200000}, "disk_gb": 500}
    h = sparks.Hub(tmp_path / "studio", tmp_path / "pack", True, {}, [], threading.RLock(), threading.Event(),
                   lambda: info.copy(), lambda p: tmp_path / p, lambda j, w: None)
    h.mine = info["code"]
    h.keep({"workers": [{"address": STATION, "name": "Station", "enabled": True}], "sparks": 2})
    h.state(STATION).update(state="ready", info=info.copy(), seen=info.copy())
    h.hang_up = lambda address: None
    h.fits_here = lambda job: False
    yield h
    for state in h.live.values():
        sparks.KEYS.pop(state.get("base"), None)


def clip(hub, **more):
    found = {"id": str(len(hub.jobs)), "kind": "video", "params": {"preset": "sharp", "quality": "fh8"},
             "status": "queued", "title": "Reference clip", "sparks": {"chosen": 2}, "log": "", "placement": None, **more}
    hub.jobs[found["id"]] = found
    hub.order.append(found["id"])
    return found


def head(hub):
    """This Spark's next job, as the server's queue thread asks for it: the fits measured, then the claim."""

    hub.measure()
    return hub.claim()


def busy_elsewhere(hub):
    hub.state(STATION).update(state="busy", job=None)
    assert hub.usable(hub.workers()[0]) == sparks.BUSY_ELSEWHERE


def fail_there(hub, job, why, down=False):
    """``job`` handed to the worker, then taken back from it."""

    assert hub.claim(STATION) is job
    with pytest.raises(sparks.Moved):
        hub.move(job, hub.workers()[0], why, down=down)
    live = hub.state(STATION)
    live.update(job=None, **({"state": "ready"} if live["state"] == "busy" else {}))  # as Hub.run leaves it
    assert job["status"] == "queued"


def test_too_big_here_waits_for_a_worker_busy_elsewhere(hub):
    busy_elsewhere(hub)
    one = clip(hub)
    assert hub.team(one) == [] and hub.later(one) == [STATION]
    assert head(hub) is None
    assert one["status"] == "queued" and "Waiting for Station" in one["progress"]["stage"] and "too big" in one["progress"]["stage"]


def test_what_fits_here_is_still_taken_while_the_worker_is_busy(hub):
    busy_elsewhere(hub)
    hub.fits_here = lambda job: True
    one = clip(hub)
    assert head(hub) is one


def test_no_worker_that_could_take_it_keeps_the_old_way(hub):
    config = hub.config()
    config["workers"][0]["enabled"] = False
    hub.keep(config)
    one = clip(hub)
    assert hub.later(one) == []
    assert head(hub) is one  # this Spark's guard then refuses it with its own reason, as before


def test_a_job_at_a_count_of_one_never_waits(hub):
    busy_elsewhere(hub)
    one = clip(hub, sparks={"chosen": 1})
    assert hub.later(one) == [] and head(hub) is one


def test_an_idle_worker_gets_it_not_this_spark(hub):
    one = clip(hub)
    assert hub.team(one) == [STATION]
    assert head(hub) is None
    assert hub.claim(STATION) is one and not one.get("progress")


def test_required_on_this_spark_never_waits_for_a_worker(hub):
    busy_elsewhere(hub)
    one = clip(hub, placement={"worker": "head", "policy": "require"})
    assert hub.later(one) == []
    assert head(hub) is one


def test_a_preferred_busy_worker_is_waited_for_at_any_count(hub):
    busy_elsewhere(hub)
    one = clip(hub, sparks={"chosen": 1}, placement={"worker": STATION, "policy": "prefer"})
    assert hub.later(one) == [STATION]
    assert head(hub) is None


def test_a_worker_that_goes_down_keeps_the_clips_too_big_here_queued(hub):
    """The worker stops answering while it renders one clip; that clip and the clip queued behind it wait for it (it
    was seen able to render them) instead of being taken here, where the guard would refuse them."""

    one, two = clip(hub), clip(hub)
    fail_there(hub, one, "it stopped answering for 60 s while rendering", down=True)
    live = hub.state(STATION)
    assert live["state"] == "down" and live["info"] is None and live["seen"]
    assert STATION not in one["sparks"].get("not", {}) and one["sparks"]["moves"] == 1
    assert hub.later(one) == [STATION] and hub.later(two) == [STATION]
    assert head(hub) is None
    assert one["status"] == two["status"] == "queued"
    assert "Station (stopped answering)" in one["progress"]["stage"] and "Station (stopped answering)" in two["progress"]["stage"]
    # checked again and answering: it takes the clip
    live.update(state="ready", info=live["seen"])
    assert hub.claim(STATION) is one


@pytest.mark.parametrize("state", ["unreachable", "stopped", "update"])
def test_a_failed_check_does_not_end_the_wait(hub, state):
    """Checked while still away, the worker says why (ssh does not reach it, its app is not running, its code is
    behind): it was seen able to render the clip, so the clip still waits for it rather than failing here."""

    one, two = clip(hub), clip(hub)
    fail_there(hub, one, "it stopped answering for 60 s while rendering", down=True)
    hub.state(STATION).update(state=state, info=None)
    assert hub.later(one) == [STATION] and hub.later(two) == [STATION]
    assert head(hub) is None and one["status"] == two["status"] == "queued"
    assert f"Station ({sparks.STATES[state]})" in two["progress"]["stage"]


def test_a_worker_down_and_never_seen_is_not_waited_for(hub):
    hub.state(STATION).update(state="down", info=None, seen=None)
    one = clip(hub)
    assert hub.later(one) == []
    assert head(hub) is one


def test_a_worker_not_checked_yet_is_waited_for(hub):
    """Just after this app starts its workers are being looked at: a clip too big here is not taken meanwhile."""

    hub.state(STATION).update(state="new", info=None, seen=None, working="checking")
    one = clip(hub)
    assert hub.later(one) == [STATION] and head(hub) is None


def test_a_clip_whose_retries_are_spent_fails_with_its_reason(hub):
    one = clip(hub)
    for _ in range(sparks.MOVES):
        fail_there(hub, one, "its render failed: ComfyUI stopped")
    assert one["sparks"]["moves"] == sparks.MOVES and hub.team(one) == [] and hub.later(one) == []
    assert head(hub) is None
    assert one["status"] == "failed" and "Too big for this Spark's memory" in one["error"]
    assert f"came back {sparks.MOVES} times" in one["error"] and "ComfyUI stopped" in one["error"]


def test_a_clip_whose_retries_are_spent_runs_here_when_it_fits(hub):
    one = clip(hub)
    for _ in range(sparks.MOVES):
        fail_there(hub, one, "its render failed: ComfyUI stopped")
    hub.fits_here = lambda job: True
    hub.fitted.clear()
    assert head(hub) is one


def test_a_refusal_bars_the_worker_and_the_clip_fails_when_none_is_left(hub):
    one = clip(hub)
    fail_there(hub, one, sparks.REFUSED + ": a clip with references needs about 78 GB; this machine has 60 GB for it")
    assert STATION in one["sparks"]["not"] and hub.team(one) == [] and hub.later(one) == []
    assert head(hub) is None
    assert one["status"] == "failed" and "78 GB" in one["error"]


def test_a_clip_that_fits_here_bars_the_worker_as_before(hub):
    hub.fits_here = lambda job: True
    one = clip(hub)
    fail_there(hub, one, "its render failed: out of disk")
    assert STATION in one["sparks"]["not"]
    assert head(hub) is one


def test_a_preferred_worker_is_retried_at_most_moves_times(hub):
    one = clip(hub, sparks={"chosen": 1}, placement={"worker": STATION, "policy": "prefer"})
    for _ in range(sparks.MOVES):
        fail_there(hub, one, "its render failed: ComfyUI stopped")
    assert hub.team(one) == [] and hub.later(one) == []
    assert hub.claim(STATION) is None
    assert head(hub) is None and one["status"] == "failed"


def test_a_required_worker_failure_still_forbids_fallback(hub):
    one = clip(hub, placement={"worker": STATION, "policy": "require"})
    assert hub.claim(STATION) is one
    with pytest.raises(RuntimeError, match="forbids fallback"):
        hub.move(one, hub.workers()[0], "its render failed: ComfyUI stopped", down=False)


def test_a_fit_that_cannot_be_read_does_not_stop_the_queue(hub):
    busy_elsewhere(hub)

    def broken(job):
        raise ValueError("a reference picture is missing")

    hub.fits_here = broken
    one = clip(hub)
    assert head(hub) is one  # taken here: its own build then says what is wrong


def test_fits_are_measured_outside_the_queues_lock(hub):
    """measure() reads the fits claim will ask for without holding the queue's lock (another thread can take it
    meanwhile); claim() then builds nothing."""

    busy_elsewhere(hub)
    held = []

    def fits_here(job):
        taken = []
        other = threading.Thread(target=lambda: taken.append(hub.lock.acquire(timeout=1) and (hub.lock.release() or True)))
        other.start()
        other.join()
        held.append(not taken[0])
        return False

    hub.fits_here = fits_here
    waiting = [clip(hub) for _ in range(3)]
    hub.measure()
    assert held == [False] * len(waiting)
    hub.fits_here = lambda job: pytest.fail("read under the lock")
    assert hub.claim() is None and all("too big" in job["progress"]["stage"] for job in waiting)


def test_a_slow_measure_is_not_read_again_under_the_lock(hub, monkeypatch):
    """Fits that took long to read (reference media probed) are not stale by the time claim looks them up: claim
    never builds one again, however old the reading is."""

    busy_elsewhere(hub)
    clock = [1000.0]
    monkeypatch.setattr(sparks.time, "time", lambda: clock[0])

    def slow(job):
        clock[0] += sparks.FITTED / 2
        return False

    hub.fits_here = slow
    waiting = [clip(hub) for _ in range(4)]
    hub.measure()
    clock[0] += sparks.FITTED * 3
    hub.fits_here = lambda job: pytest.fail("read under the lock")
    assert hub.claim() is None and all(job["status"] == "queued" for job in waiting)


def test_a_clip_queued_after_the_measure_waits_for_the_next(hub):
    busy_elsewhere(hub)
    hub.measure()
    late = clip(hub)
    hub.fits_here = lambda job: pytest.fail("read under the lock")
    assert hub.claim() is None and late["status"] == "queued" and hub.unmeasured
    hub.fits_here = lambda job: True
    assert head(hub) is late and not hub.unmeasured


def test_the_fit_is_read_once_per_clip_until_this_spark_ends_a_job(hub):
    """claim runs under the queue's lock: a waiting clip's fit (its command built) is not read again on every pass,
    and a clip with no worker in question is not read at all."""

    busy_elsewhere(hub)
    asked = []
    hub.fits_here = lambda job: asked.append(job["id"]) or False
    waiting = [clip(hub) for _ in range(5)]
    alone = clip(hub, sparks={"chosen": 1})
    assert head(hub) is alone
    assert sorted(asked) == sorted(job["id"] for job in waiting)
    assert head(hub) is None and head(hub) is None
    assert len(asked) == len(waiting)
    hub.fitted.clear()  # what the server does after each job this Spark ends
    assert head(hub) is None and len(asked) == 2 * len(waiting)


def test_a_long_video_goes_to_a_spark_or_linux_worker_only():
    base = {"fasth3": True, "presets": ["small"], "qualities": ["fh8"]}
    params = {"prompt": "x", "preset": "small", "quality": "fh8", "total_seconds": 30}
    assert sparks.capable("long", params, {**base, "platform": "spark"}) == ""
    assert sparks.capable("long", params, {**base, "platform": "windows"})
    assert sparks.capable("long", params, {**base, "platform": "mac"})
