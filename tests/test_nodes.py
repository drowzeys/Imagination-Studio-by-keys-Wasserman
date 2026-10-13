"""Render nodes (app/nodes.py): two real studio servers on this machine, one handing jobs to the other.

The servers run from a copy of the pack whose render scripts are stand-ins (a few seconds of "passes", then a tiny
clip made by ffmpeg), so the test needs no GPU and no models. Run: python3 tests/test_nodes.py (the interpreter must
have fastapi and uvicorn: the studio's own venv does).
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK / "app"))
import nodes  # noqa: E402

STUB = """#!/bin/bash
# stand-in for a render: "$1" prompt, "$2" output (studio.sh: "$1" image prompt, "$2" prompt, "$3" output)
out="${@: -1}"; n=${STUB_PASSES:-4}
echo "[tensorfold] engine: stub"; echo "[stub] rendered by port $PORT"
[ -n "${IMAGE_FILE:-}" ] && { [ -s "$IMAGE_FILE" ] || { echo "the first frame is not here: $IMAGE_FILE" >&2; exit 3; }; echo "[stub] first frame $(wc -c < "$IMAGE_FILE") bytes"; }
case "$1$2" in *FAILHERE*) [ "$PORT" = "${STUB_FAIL_PORT:-0}" ] && { echo "the stand-in was told to fail on this machine" >&2; exit 4; };; esac
for i in $(seq $n); do echo "[tensorfold] step $i/$n"; sleep ${STUB_STEP:-0.5}; done
ffmpeg -v error -y -f lavfi -i "color=c=gray:s=160x96:d=1" -f lavfi -i "anullsrc=r=16000:cl=mono" -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac "$out"
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def api(port: int, path: str, body=None, method=None, token=None, timeout=20):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data, method=method or ("POST" if data is not None else "GET"))
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Studio-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


class Studio:
    def __init__(self, pack: Path, name: str, token: str = "", **env):
        self.home = Path(tempfile.mkdtemp(prefix=f"studio-{name}-")) / "TensorFoldStudio"
        for folder in ("renders", "uploads", "scouts", "projects", "loras"):
            (self.home / folder).mkdir(parents=True)
        self.port, self.pack, self.token, self.env, self.process = free_port(), pack, token, env, None
        self.start()

    def start(self) -> None:
        env = {**os.environ, "STUDIO_HOME": str(self.home), "PORT": str(self.port), "HOST": "127.0.0.1",
               "STUDIO_NODE_POLL": "0.2", "STUDIO_NODE_LOST": "3", "PREFIX": str(self.home / "no-prefix"), **self.env}
        if self.token:
            env["STUDIO_TOKEN"] = self.token
        self.log = open(self.home / "server.log", "ab")
        self.process = subprocess.Popen([sys.executable, str(self.pack / "app/server.py")], env=env, stdout=self.log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):
            try:
                if api(self.port, "/api/state")[0] == 200:
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("the studio did not start: " + (self.home / "server.log").read_text()[-600:])

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()

    def job(self, job_id: str) -> dict:
        return api(self.port, f"/api/jobs/{job_id}")[1]

    def wait(self, job_id: str, states=("done", "failed", "cancelled"), seconds: float = 40) -> dict:
        until = time.time() + seconds
        while time.time() < until:
            job = self.job(job_id)
            if job["status"] in states:
                return job
            time.sleep(0.1)
        raise AssertionError(f"job {job_id} is still {self.job(job_id)}")


def stub_pack() -> Path:
    """The pack, with stand-in render scripts."""

    target = Path(tempfile.mkdtemp(prefix="pack-")) / "pack"
    shutil.copytree(PACK, target, ignore=shutil.ignore_patterns(".git", "samples", "__pycache__", "*.mp4", "docs"))
    for name in ("scripts/video.sh", "scripts/studio.sh"):
        (target / name).write_text(STUB)
    return target


def clip(prompt: str = "a test clip", **more) -> dict:
    return {"kind": "video", "title": prompt[:20], "params": {"prompt": prompt, "preset": "small", "quality": "fh8", "seconds": 5, "seed": 1, **more}}


def test_referenced():
    home = Path(tempfile.mkdtemp()) / "s"
    (home / "uploads").mkdir(parents=True)
    (home / "motion/p1").mkdir(parents=True)
    (home / "uploads/a.png").write_bytes(b"x")
    (home / "motion/p1/depth.mp4").write_bytes(b"y")
    outside = Path(tempfile.mkdtemp()) / "secret.txt"
    outside.write_text("no")
    found = nodes.referenced({"image": "uploads/a.png", "references": [str(home / "uploads/a.png")], "motion": {"pack": "motion/p1"},
                              "prompt": "uploads/a.png is named in words too", "other": str(outside), "up": "../x", "n": 3}, home)
    assert found == ["uploads/a.png", "motion/p1/depth.mp4"], found


def test_farm():
    pack = stub_pack()
    token = "t-" + os.urandom(6).hex()
    head = Studio(pack, "head")
    node = Studio(pack, "node", token=token)
    try:
        # the node API wants the token; a studio with no token is not a node at all
        assert api(node.port, "/api/node/info")[0] == 401
        assert api(node.port, "/api/node/info", token="wrong")[0] == 401
        assert api(node.port, "/api/node/info", token=token)[1]["version"] == (pack / "VERSION").read_text().strip()
        assert api(head.port, "/api/node/info", token="x")[0] == 403
        status, said = api(node.port, "/api/node/file?path=../../etc/x", method="PUT", token=token)
        assert status == 400, (status, said)

        # adding a node: a wrong address is refused, a right one shows as free
        assert api(head.port, "/api/nodes", {"url": "http://127.0.0.1:1"})[0] == 400
        status, added = api(head.port, "/api/nodes", {"url": f"http://127.0.0.1:{node.port}", "token": token, "name": "second"})
        assert status == 200 and added["state"] == "free", added
        assert api(head.port, "/api/nodes", {"url": f"http://127.0.0.1:{node.port}", "token": token})[0] == 400  # twice

        # four clips: this machine and the node render side by side, and the node's clips come back here
        (head.home / "uploads/first.png").write_bytes(b"\x89PNG" + b"0" * 2000)
        started = time.time()
        ids = [api(head.port, "/api/jobs", clip(f"clip {i}", **({"image": "uploads/first.png"} if i == 1 else {})))[1]["id"] for i in range(4)]
        done = [head.wait(i) for i in ids]
        took = time.time() - started
        assert all(j["status"] == "done" for j in done), [(j["status"], j["error"]) for j in done]
        where = [j["node"] or "here" for j in done]
        assert where.count("second") == 2 and where.count("here") == 2, where
        for job in done:
            assert (head.home / job["outputs"][0]).stat().st_size > 500, job["outputs"]
            assert f"rendered by port {node.port if job['node'] else head.port}" in job["log"], job["log"][-300:]
        sent = next(j for j in done if j["params"].get("image"))
        if sent["node"]:  # the first frame travelled to the node
            assert (node.home / "uploads/first.png").stat().st_size == 2004 and "first frame 2004 bytes" in sent["log"]
        serial = 4 * 4 * 0.5
        assert took < serial * 0.8, f"four clips took {took:.1f} s; one machine would take about {serial:.0f} s"

        # a job the node fails goes to another machine (here), once
        time.sleep(1.2)  # the queue is saved twice a second
        head.stop(); node.stop()
        node.env["STUB_FAIL_PORT"] = str(node.port); head.start(); node.start()
        time.sleep(1.5)
        blocker = api(head.port, "/api/jobs", clip("keeps this machine busy"))[1]["id"]
        moved = api(head.port, "/api/jobs", clip("FAILHERE on the node"))[1]["id"]
        job = head.wait(moved)
        assert job["status"] == "done" and not job["node"] and job["tries"] == 1 and "goes back to the queue" in job["log"], (job, head.job(blocker), api(head.port, "/api/nodes")[1])
        head.wait(blocker)

        # cancel reaches the node
        node.env["STUB_STEP"] = head.env["STUB_STEP"] = "3"
        time.sleep(1.2); head.stop(); node.stop(); head.start(); node.start()
        time.sleep(1.5)
        a = api(head.port, "/api/jobs", clip("long a"))[1]["id"]
        b = api(head.port, "/api/jobs", clip("long b"))[1]["id"]
        time.sleep(2.5)
        remote = next(j for j in (head.job(a), head.job(b)) if j["node"])
        assert remote["status"] == "running" and remote["progress"]["stage"].startswith("second"), remote
        there = api(node.port, "/api/jobs")[1][0]
        api(head.port, f"/api/jobs/{remote['id']}/cancel", {})
        time.sleep(1.5)
        assert head.job(remote["id"])["status"] == "cancelled" and node.job(there["id"])["status"] == "cancelled"
        other = a if remote["id"] == b else b
        api(head.port, f"/api/jobs/{other}/cancel", {})

        # the queue outlives a restart: a job on the node is picked up again, finished cards stay
        c = api(head.port, "/api/jobs", clip("survives a restart"))[1]["id"]
        d = api(head.port, "/api/jobs", clip("on the node"))[1]["id"]
        time.sleep(3)
        on_node = next(i for i in (c, d) if head.job(i)["node"])
        head.stop(); head.start()
        kept = {j["id"]: j for j in api(head.port, "/api/jobs")[1]}
        assert ids[0] not in kept or kept[ids[0]]["status"] == "done"
        assert kept[on_node]["status"] == "running" and kept[on_node]["node"] == "second", kept[on_node]
        here = c if on_node == d else d
        assert kept[here]["status"] == "failed" and "restarted" in kept[here]["error"], kept[here]
        assert head.wait(on_node, seconds=30)["status"] == "done"

        # a node that dies loses its job to this machine
        e = api(head.port, "/api/jobs", clip("blocker"))[1]["id"]
        f = api(head.port, "/api/jobs", clip("orphan"))[1]["id"]
        time.sleep(2.5)
        orphan = next(i for i in (e, f) if head.job(i)["node"])
        node.stop()
        job = head.wait(orphan, seconds=60)
        assert job["status"] == "done" and not job["node"] and job["tries"] == 1, job
        shown = api(head.port, "/api/nodes")[1]["nodes"][0]
        assert shown["state"] == "offline", shown

        # drain and remove
        assert api(head.port, "/api/nodes/second", {"drain": True})[1]["drain"] is True
        assert api(head.port, "/api/nodes/second", method="DELETE")[1] == {"removed": "second"}
        assert api(head.port, "/api/nodes")[1]["nodes"] == []
    finally:
        head.stop(); node.stop()


def test_hand_out_only():
    """`local.render` off: this machine only hands jobs out, but still takes what no node can."""

    pack = stub_pack()
    token = "t-" + os.urandom(6).hex()
    head, node = Studio(pack, "head"), Studio(pack, "node", token=token)
    try:
        api(head.port, "/api/nodes", {"url": f"http://127.0.0.1:{node.port}", "token": token, "name": "second"})
        assert api(head.port, "/api/nodes/local", {"render": False})[1] == {"local": {"render": False}}
        ids = [api(head.port, "/api/jobs", clip(f"clip {i}"))[1]["id"] for i in range(2)]
        assert [head.wait(i)["node"] for i in ids] == ["second", "second"]
        node.stop()
        left = api(head.port, "/api/jobs", clip("nobody else"))[1]["id"]
        job = head.wait(left, seconds=60)
        assert job["status"] == "done" and not job["node"], job
    finally:
        head.stop(); node.stop()


if __name__ == "__main__":
    test_referenced(), test_farm(), test_hand_out_only()
    print("ok")
