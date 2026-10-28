"""Cancellation and shutdown must drain owned work before releasing a GPU/RAM slot."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="local NVIDIA worker process ownership is Linux-only")


def isolated_script(tmp_path, code):
    env = dict(os.environ)
    env.update(STUDIO_HOME=str(tmp_path / "studio"), PREFIX=str(tmp_path / "prefix"),
               SPARK_MODELS=str(tmp_path / "models"), STUDIO_GPU_RUNTIME_DIR=str(tmp_path / "runtime"),
               STUDIO_GPU_RAM_DIR=str(tmp_path / "ram"), STUDIO_DISCOVER_DELAY="-1", BOLT_BACKEND="external")
    for key in ("STUDIO_LOCAL_GPU_CHILD", "STUDIO_LOCAL_GPU_KEY", "STUDIO_GPU_JOB_KEY", "STUDIO_PIDFILE"):
        env.pop(key, None)
    prefix = "import sys\nsys.path.insert(0, 'app')\nimport server\n"
    done = subprocess.run([sys.executable, "-c", prefix + code, str(tmp_path)], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=35)
    assert done.returncode == 0, done.stdout + done.stderr


def test_cancelled_task_never_launches_its_command(tmp_path):
    marker = tmp_path / "unexpected-execution"
    job = {"id": "already-cancelled", "status": "cancelled", "log": ""}
    result = server.run(job, [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)], {})
    assert result is False
    assert not marker.exists()


def test_cancellation_during_launch_cannot_miss_the_unpublished_process(tmp_path):
    isolated_script(tmp_path, r'''
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import subprocess, threading
root = Path(sys.argv[1])
gate, born = threading.Event(), threading.Event()
real_popen = subprocess.Popen

def delayed_publish(*args, **kwargs):
    process = real_popen(*args, **kwargs)
    born.set()
    assert gate.wait(5)
    return process

server.subprocess.Popen = delayed_publish
job = {'id':'launch-race','kind':'train','status':'running','log':'','params':{},'outputs':[]}
server.jobs[job['id']] = job
command = [sys.executable, '-c', "from pathlib import Path; import sys,time; p=Path(sys.argv[1]); end=time.monotonic()+15;\nwhile not (p/'go').exists() and time.monotonic()<end: time.sleep(.01)\n(p/'unexpected-execution').touch()", str(root)]
with ThreadPoolExecutor(max_workers=2) as pool:
    run = pool.submit(server.run, job, command, {})
    assert born.wait(5)
    cancelled = pool.submit(server.cancel, job['id'])
    try:
        cancelled.result(timeout=.2)
    except TimeoutError:
        pass
    finally:
        gate.set()
    assert cancelled.result(timeout=5)['status'] == 'cancelled'
    (root/'go').touch()
    assert run.result(timeout=5) is False
assert not (root/'unexpected-execution').exists(), 'cancel returned before its new process was stopped'
''')


@pytest.mark.parametrize("params", [
    {"total_seconds": 30},
    {"total_seconds": 16},
    {"total_seconds": 31, "segment_seconds": 5},
    {"total_seconds": 37, "segment_seconds": 10},
    {"total_seconds": 30, "frames": 22},
])
def test_long_job_reserves_the_largest_actual_segment(params):
    params = {"preset": "small", "quality": "fh4", **params}
    pieces = server.plan(params["total_seconds"], params.get("segment_seconds", server.LONGEST))
    budgets = []
    for seconds in pieces:
        frames = server.lengths.of({"seconds": seconds, "frames": params.get("frames")})
        room = server.memory_of(params, server.PRESETS["small"][2], frames, False)
        budgets.append(room["need_mb"] + room["error_mb"])
    assert server.local_ram_need({"kind": "long", "params": params}) == max(budgets)


def test_managed_shutdown_stops_detached_noncomfy_work_and_preserves_foreign_process(tmp_path):
    isolated_script(tmp_path, r'''
from pathlib import Path
import subprocess, threading, time
import local_gpus
root = Path(sys.argv[1])
server.hub.local.active = True
local_gpus.memory = lambda: (100000, 100000)
server.stop_session_comfy = lambda: None  # no Comfy server belongs to this CPU-only regression
external = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(25)'], start_new_session=True)
job = {'id':'shutdown-training','kind':'train','status':'running','params':{},'outputs':[],'log':'','finished':None}
server.jobs[job['id']] = job
child = "from pathlib import Path; import os,sys,time; p=Path(sys.argv[1]); part=p.with_suffix('.part'); part.write_text(str(os.getpid())); part.replace(p); time.sleep(20)"
runner = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],start_new_session=True); print('detached child ready',flush=True); time.sleep(20)"
server.train_free = lambda job: ""  # no model server belongs to this isolated runner
server.build = lambda *args, **kwargs: ([sys.executable, '-u', '-c', runner, child, str(root/'descendant.pid')], {}, [])
thread = threading.Thread(target=server.perform, args=(job,), daemon=True)
thread.start()
try:
    deadline = time.monotonic()+5
    while not (root/'descendant.pid').exists() and time.monotonic()<deadline:
        time.sleep(.01)
    assert (root/'descendant.pid').exists(), job['log']
    descendant = int((root/'descendant.pid').read_text())
    began = time.monotonic()
    server.stop_local_jobs()
    thread.join(timeout=2)
    assert not thread.is_alive(), 'owned queue work survived shutdown'
    assert time.monotonic()-began < 10, 'shutdown waited for completion instead of terminating owned work'
    assert local_gpus.process_identity(descendant) is None, 'detached training descendant survived shutdown'
    assert external.poll() is None, 'shutdown stopped a foreign process'
    assert job['status'] == 'cancelled' and job['finished'] is not None
    assert local_gpus.load(local_gpus.ram_dir()/'reservations.json') == {}
finally:
    external.terminate()
    external.wait(timeout=5)
''')


def test_successful_render_keeps_its_persistent_model_server_warm(tmp_path):
    isolated_script(tmp_path, r'''
from pathlib import Path
import signal, time
import local_gpus
root = Path(sys.argv[1])
server.hub.local.active = True
job = {'id':'warm-server','kind':'video','status':'running','log':''}
child = "from pathlib import Path; import os,sys,time; p=Path(sys.argv[1]); q=p.with_suffix('.part'); q.write_text(str(os.getpid())); q.replace(p); time.sleep(20)"
runner = "import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); p=Path(sys.argv[2]); end=time.monotonic()+5;\nwhile not p.exists() and time.monotonic()<end: time.sleep(.01)\nassert p.exists()"
try:
    assert server.run(job, [sys.executable, '-c', runner, child, str(root/'model-server.pid')], {})
    pid = int((root/'model-server.pid').read_text())
    identity = local_gpus.process_identity(pid)
    assert identity is not None, 'a successful render unloaded its warm model server'
finally:
    if (root/'model-server.pid').exists():
        pid = int((root/'model-server.pid').read_text())
        identity = local_gpus.process_identity(pid)
        if identity:
            local_gpus.signal_owned(pid, identity, signal.SIGTERM)
''')
