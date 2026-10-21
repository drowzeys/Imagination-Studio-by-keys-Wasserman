"""Real update/restart/recovery cycles in disposable installations, with a local Git transport and no GPU."""
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
OPENER = build_opener(ProxyHandler({}))


def git(folder, *args):
    result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def installation(folder):
    upstream, clone, home = folder / "upstream", folder / "installed", folder / "home"
    upstream.mkdir(parents=True)
    for directory in ("app", "spark", "scripts"):
        shutil.copytree(ROOT / directory, upstream / directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for source in [*ROOT.glob("*.py"), ROOT / ".gitignore"]:
        shutil.copy2(source, upstream / source.name)
    (upstream / "VERSION").write_text("updater-test-1\n")
    git(upstream, "init", "-b", "main")
    git(upstream, "config", "user.name", "Studio update test")
    git(upstream, "config", "user.email", "tests@example.invalid")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "working Studio")
    git(folder, "clone", str(upstream), str(clone))
    git(clone, "remote", "set-url", "origin", "https://github.com/test-owner/studio.git")
    git(clone, "config", "url." + str(upstream) + ".insteadOf", "https://github.com/test-owner/studio.git")
    return upstream, clone, home


@contextlib.contextmanager
def running(folder, clone, home):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port), "STUDIO_HOME": str(home),
           "PREFIX": str(folder / "runtime"), "SPARK_MODELS": str(folder / "models"),
           "STUDIO_GPU_LOCK": str(folder / "gpu.lock"), "DRAMA3_VENV": str(folder / "no-drama3"),
           "DRAMA3_CUDA_BIN": str(folder / "no-drama3"), "MUSIC3_VENV": str(folder / "no-music")}
    env.pop("STUDIO_PIDFILE", None)
    env.pop("STUDIO_UPDATE_SESSION", None)
    with (folder / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(clone / "app/launcher.py")], env=env, stdout=log, stderr=log)
        url = f"http://127.0.0.1:{port}"
        try:
            wait_for(lambda: api(url, "/api/updates/health").get("session"), process=process)
            yield url, process, env
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def api(url, path="/api/updates", body=None):
    request = Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Content-Type": "application/json"} if body is not None else {})
    with OPENER.open(request, timeout=3) as response:
        return json.load(response)


def wait_for(check, timeout=30, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process:
            assert process.poll() is None, "Studio launcher exited"
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError("Studio did not reach the expected state")


def publish(upstream, broken=False):
    (upstream / "VERSION").write_text("updater-test-2\n")
    with (upstream / "zig_fasth3.py").open("a") as output:  # a renderer setup copies into PREFIX
        output.write("\n# updater-test-2\n")
    if broken:
        with (upstream / "app/server.py").open("a") as output:
            output.write("\nthis is deliberately invalid Python !!!\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "broken candidate" if broken else "working candidate")
    return git(upstream, "rev-parse", "HEAD")


def prefix_copies(folder, clone):
    """PREFIX as oneshot-setup.sh leaves it: the renderers' copies; qwen_image_generate.py edited by hand there."""
    runtime = folder / "runtime"
    runtime.mkdir(exist_ok=True)
    shutil.copy2(clone / "zig_fasth3.py", runtime / "zig_fasth3.py")
    (runtime / "qwen_image_generate.py").write_text("# a person's own edit\n")
    return runtime


def test_real_install_restarts_and_keeps_projects_and_settings(tmp_path):
    upstream, clone, home = installation(tmp_path)
    runtime = prefix_copies(tmp_path, clone)
    installed = (runtime / "zig_fasth3.py").read_bytes()
    with running(tmp_path, clone, home) as (url, process, _env):
        assert api(url)["automatic"]  # on by default
        api(url, "/api/updates/settings", {"automatic": True})
        project = home / "projects/keep-this.json"
        project.write_text('{"a creative project": true}')
        original = project.read_bytes()
        target = publish(upstream)
        api(url, "/api/updates/check", {})
        wait_for(lambda: api(url).get("available"))
        before_session = api(url)["session"]
        api(url, "/api/updates/install", {})
        state = wait_for(lambda: (s if (s := api(url)).get("last_result", {}) and s["last_result"].get("ok") else None), process=process)
        assert state["session"] != before_session and state["automatic"]
        assert git(clone, "rev-parse", "HEAD") == target
        assert project.read_bytes() == original
        assert git(clone, "rev-parse", state["last_result"]["backup"]) != target
        assert api(url, "/api/state")["version"] == "updater-test-2"
        # the renderer copy in PREFIX follows the code; the one edited by hand is left alone
        assert (runtime / "zig_fasth3.py").read_bytes() == (clone / "zig_fasth3.py").read_bytes() != installed
        assert (runtime / "zig_fasth3.py.before-update").read_bytes() == installed
        assert (runtime / "qwen_image_generate.py").read_text() == "# a person's own edit\n"
        assert "zig_fasth3.py" in state["last_result"]["message"]


def test_broken_update_restores_running_version_and_turns_automatic_off(tmp_path):
    upstream, clone, home = installation(tmp_path)
    runtime = prefix_copies(tmp_path, clone)
    installed = (runtime / "zig_fasth3.py").read_bytes()
    old = git(clone, "rev-parse", "HEAD")
    with running(tmp_path, clone, home) as (url, process, _env):
        api(url, "/api/updates/settings", {"automatic": True})
        publish(upstream, broken=True)
        api(url, "/api/updates/check", {})
        wait_for(lambda: api(url).get("available"))
        api(url, "/api/updates/install", {})
        state = wait_for(lambda: (s if (s := api(url)).get("last_result") else None), process=process)
        assert not state["last_result"]["ok"] and "restored" in state["last_result"]["message"]
        assert not state["automatic"] and git(clone, "rev-parse", "HEAD") == old
        assert api(url, "/api/state")["version"] == "updater-test-1"
        assert (runtime / "zig_fasth3.py").read_bytes() == installed  # its PREFIX copy is put back too


def test_second_launcher_cannot_share_a_running_checkout(tmp_path):
    _upstream, clone, home = installation(tmp_path)
    with running(tmp_path, clone, home) as (url, _process, env):
        other = subprocess.run([sys.executable, str(clone / "app/launcher.py")], env=env,
                               capture_output=True, text=True, timeout=10)
        assert other.returncode == 1 and "already has a running Studio" in other.stderr
        assert api(url, "/api/updates/health")["session"]


def test_a_mac_engine_built_from_another_commit_is_stale(tmp_path):
    sys.path.insert(0, str(ROOT / "app"))
    import updates
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/build-zig-engine.sh").write_text('ZIG_COMMIT="${ZIG_COMMIT:-' + "b" * 40 + '}"\n')
    (tmp_path / "prefix/zig-engine").mkdir(parents=True)
    (tmp_path / "prefix/zig-engine/COMMIT").write_text("a" * 40 + "\n")
    assert updates.engine_stale(tmp_path, tmp_path / "prefix") == "b" * 40
    (tmp_path / "prefix/zig-engine/COMMIT").write_text("b" * 40 + "\n")
    assert updates.engine_stale(tmp_path, tmp_path / "prefix") is None
    assert updates.engine_stale(tmp_path, tmp_path / "nowhere") is None  # no engine installed: nothing to rebuild
