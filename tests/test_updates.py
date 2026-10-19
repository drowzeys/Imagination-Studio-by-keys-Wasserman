"""Exercise real temporary Git histories and the update API without touching an installed Studio."""
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import updates


def command(folder, *args):
    result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def no_github_api(monkeypatch):
    # The release lookup is best-effort; tests never reach api.github.com.
    def offline(*args, **kwargs):
        raise OSError("no network in tests")
    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    monkeypatch.delenv("STUDIO_UPDATE_BRANCH", raising=False)
    updates.releases.clear()  # each test asks GitHub afresh (the answers are kept an hour)


@pytest.fixture
def history(tmp_path, monkeypatch):
    upstream, clone = tmp_path / "upstream", tmp_path / "installation"
    upstream.mkdir()
    command(upstream, "init", "-b", "main")
    command(upstream, "config", "user.name", "Update tests")
    command(upstream, "config", "user.email", "tests@example.invalid")
    (upstream / "VERSION").write_text("1.0\n")
    (upstream / "code.py").write_text("print('old')\n")
    command(upstream, "add", ".")
    command(upstream, "commit", "-m", "initial")
    command(tmp_path, "clone", str(upstream), str(clone))
    command(clone, "config", "user.name", "Update tests")
    command(clone, "config", "user.email", "tests@example.invalid")
    # Git rewrites this declared GitHub source to our disposable local transport. No network or mocks.
    command(clone, "remote", "set-url", "origin", "https://github.com/test-owner/studio.git")
    command(clone, "config", "url." + str(upstream) + ".insteadOf", "https://github.com/test-owner/studio.git")
    return upstream, clone


def publish(upstream, text="print('new')\n"):
    (upstream / "code.py").write_text(text)
    (upstream / "VERSION").write_text("1.1\n")
    command(upstream, "add", ".")
    command(upstream, "commit", "-m", "update")
    return command(upstream, "rev-parse", "HEAD")


def checked(manager):
    manager.check()
    deadline = time.monotonic() + 10
    while manager.status()["checking"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not manager.status()["checking"]
    return manager.status()


def test_update_fetches_without_changing_files_then_fast_forwards_and_recovers(history):
    upstream, clone = history
    old = command(clone, "rev-parse", "HEAD")
    target = publish(upstream)
    found = updates.inspect(clone, fetch=True)
    assert found["available"] and found["target"] == target and found["latest_version"] == "1.1"
    assert command(clone, "rev-parse", "HEAD") == old
    old_again, backup = updates.apply_stopped(clone, found)
    assert old_again == old and command(clone, "rev-parse", backup) == old
    assert command(clone, "rev-parse", "HEAD") == target
    updates.rollback(clone, old, target)
    assert command(clone, "rev-parse", "HEAD") == old and "old" in (clone / "code.py").read_text()


@pytest.mark.parametrize("change", ["tracked", "untracked", "staged", "branch", "detached", "own-commit", "merge"])
def test_local_work_blocks_updates_and_is_preserved(history, change):
    upstream, clone = history
    publish(upstream)
    if change == "tracked":
        (clone / "code.py").write_text("my custom code")
    elif change == "untracked":
        (clone / "my-script.py").write_text("my custom code")
    elif change == "staged":
        (clone / "code.py").write_text("my custom code")
        command(clone, "add", ".")
    elif change == "branch":
        command(clone, "switch", "-c", "my-feature")
    elif change == "detached":
        command(clone, "checkout", "--detach")
    elif change == "own-commit":
        (clone / "custom.txt").write_text("custom")
        command(clone, "add", ".")
        command(clone, "commit", "-m", "custom")
    else:
        (clone / ".git/MERGE_HEAD").write_text(command(clone, "rev-parse", "HEAD") + "\n")
    head = command(clone, "rev-parse", "HEAD")
    before = command(clone, "status", "--porcelain")
    with pytest.raises(updates.UpdateError):
        updates.inspect(clone, fetch=True)
    assert command(clone, "rev-parse", "HEAD") == head
    assert command(clone, "status", "--porcelain") == before


def test_dependencies_require_setup_instead_of_changing_runtime(history):
    upstream, clone = history
    (upstream / "requirements.lock").write_text("different-dependency==1\n")
    publish(upstream)
    with pytest.raises(updates.UpdateError, match="dependencies"):
        updates.inspect(clone, fetch=True)
    assert not (clone / "requirements.lock").exists()


def test_a_new_upstream_commit_requires_a_fresh_check(history):
    upstream, clone = history
    publish(upstream)
    asked = updates.inspect(clone, fetch=True)
    publish(upstream, "print('even newer')\n")
    with pytest.raises(updates.UpdateError, match="changed"):
        updates.apply_stopped(clone, asked)
    assert command(clone, "rev-parse", "HEAD") == asked["current"]


def test_default_branch_is_read_fresh_from_origin_when_nothing_is_tracked(history):
    upstream, clone = history
    publish(upstream)
    command(upstream, "checkout", "-b", "stable")
    command(clone, "branch", "--unset-upstream")
    assert command(clone, "symbolic-ref", "refs/remotes/origin/HEAD") == "refs/remotes/origin/main"
    with pytest.raises(updates.UpdateError, match="default branch \\(stable\\)") as error:
        updates.inspect(clone, fetch=True)
    assert error.value.info["following"] == "stable" and error.value.info["source"] == "test-owner/studio"
    assert command(clone, "symbolic-ref", "--short", "HEAD") == "main"


def test_untracked_default_branch_still_updates(history):
    upstream, clone = history
    target = publish(upstream)
    command(clone, "branch", "--unset-upstream")
    found = updates.inspect(clone, fetch=True)
    assert found["available"] and found["target"] == target
    assert found["following"] == found["default_branch"] == "main"


def feature(upstream, clone, name="development/feature"):
    command(upstream, "switch", "-c", name)
    target = publish(upstream, "print('feature')\n")
    command(upstream, "switch", "main")
    command(clone, "fetch", "origin")
    command(clone, "switch", "-c", name, "--track", "origin/" + name)
    command(upstream, "switch", name)
    target = publish(upstream, "print('newer feature')\n")
    command(upstream, "switch", "main")
    return target


def test_tracked_feature_branch_fast_forwards(history):
    upstream, clone = history
    target = feature(upstream, clone)
    old = command(clone, "rev-parse", "HEAD")
    found = updates.inspect(clone, fetch=True)
    assert found["available"] and found["target"] == target
    assert found["following"] == found["branch"] == "development/feature" and found["default_branch"] == "main"
    assert updates.inspect(clone)["following"] == "development/feature"
    assert updates.apply_stopped(clone, {**found, "following": "development/feature"})[0] == old
    assert command(clone, "rev-parse", "HEAD") == target and command(clone, "symbolic-ref", "--short", "HEAD") == "development/feature"


def test_tracked_branch_with_own_commits_is_still_refused(history):
    upstream, clone = history
    feature(upstream, clone)
    (clone / "custom.txt").write_text("custom")
    command(clone, "add", ".")
    command(clone, "commit", "-m", "custom")
    with pytest.raises(updates.UpdateError, match="own commits"):
        updates.inspect(clone, fetch=True)


def test_an_upstream_on_another_remote_is_refused(history, tmp_path):
    upstream, clone = history
    command(clone, "remote", "add", "elsewhere", str(upstream))
    command(clone, "fetch", "elsewhere")
    command(clone, "branch", "--set-upstream-to", "elsewhere/main")
    with pytest.raises(updates.UpdateError, match="tracks elsewhere, not a branch on origin"):
        updates.inspect(clone, fetch=True)
    command(clone, "switch", "-c", "local-tracking", "--track", "main")
    with pytest.raises(updates.UpdateError, match="tracks a local branch"):
        updates.inspect(clone)


def test_studio_update_branch_overrides_the_upstream(history, monkeypatch):
    upstream, clone = history
    target = feature(upstream, clone, "release/next")
    command(clone, "branch", "--unset-upstream")
    monkeypatch.setenv("STUDIO_UPDATE_BRANCH", "release/next")
    found = updates.inspect(clone, fetch=True)
    assert found["available"] and found["target"] == target and found["following"] == "release/next"
    command(clone, "switch", "main")
    with pytest.raises(updates.UpdateError, match="STUDIO_UPDATE_BRANCH"):
        updates.inspect(clone, fetch=True)
    monkeypatch.setenv("STUDIO_UPDATE_BRANCH", "-bad..name")
    with pytest.raises(updates.UpdateError, match="not a valid branch"):
        updates.inspect(clone)


def test_a_check_reports_release_and_installed_version(history, tmp_path, monkeypatch):
    upstream, clone = history
    publish(upstream)
    asked = []
    def github(request, timeout):
        asked.append((request.full_url, request.get_header("User-agent"), timeout))
        return io.BytesIO(json.dumps({"tag_name": "v1.1", "name": "Studio 1.1", "published_at": "2026-10-01T00:00:00Z",
                                      "html_url": "https://github.com/test-owner/studio/releases/tag/v1.1"}).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", github)
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False)
    status = checked(manager)
    assert asked == [("https://api.github.com/repos/test-owner/studio/releases/latest", "Studio-updater", 10)]
    assert status["latest_release"] == {"tag": "v1.1", "name": "Studio 1.1", "published_at": "2026-10-01T00:00:00Z",
                                        "url": "https://github.com/test-owner/studio/releases/tag/v1.1",
                                        "repository": "test-owner/studio"}
    assert status["available"] and status["following"] == "main" and status["latest_version"] == "1.1"
    assert status["installed_version"] == "1.0" and status["installed_commit"] == command(clone, "rev-parse", "--short", "HEAD")


def answers(monkeypatch, replies):
    asked = []
    def github(request, timeout):
        asked.append(request.full_url.removeprefix("https://api.github.com/repos/"))
        reply = replies.get(asked[-1])
        if reply is None:
            raise updates.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(reply).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", github)
    return asked


def test_a_fork_without_releases_shows_its_parents(monkeypatch):
    asked = answers(monkeypatch, {"fork-owner/studio": {"fork": True, "parent": {"full_name": "test-owner/studio"}},
                                  "test-owner/studio/releases/latest": {"tag_name": "v2.9.3", "name": "2.9.3",
                                      "published_at": "2026-10-01T00:00:00Z",
                                      "html_url": "https://github.com/test-owner/studio/releases/tag/v2.9.3"}})
    assert updates.latest_release("fork-owner/studio") == {
        "tag": "v2.9.3", "name": "2.9.3", "published_at": "2026-10-01T00:00:00Z",
        "url": "https://github.com/test-owner/studio/releases/tag/v2.9.3", "repository": "test-owner/studio"}
    assert asked == ["fork-owner/studio/releases/latest", "fork-owner/studio", "test-owner/studio/releases/latest"]


def test_no_releases_and_no_parent_is_left_out(monkeypatch):
    asked = answers(monkeypatch, {"solo-owner/studio": {"fork": False}})
    assert updates.latest_release("solo-owner/studio") is None
    assert asked == ["solo-owner/studio/releases/latest", "solo-owner/studio"]
    answers(monkeypatch, {"fork-owner/studio": {"parent": {"full_name": "../../evil"}}})
    assert updates.latest_release("fork-owner/studio") is None


def limited(monkeypatch, replies, refused):
    """GitHub's answers, with the paths in ``refused`` answered 403 (the hourly allowance used up)."""
    asked = []
    def github(request, timeout):
        asked.append(request.full_url.removeprefix("https://api.github.com/repos/"))
        if asked[-1] in refused:
            raise updates.urllib.error.HTTPError(request.full_url, 403, "rate limit exceeded", {}, io.BytesIO(b"{}"))
        reply = replies.get(asked[-1])
        if reply is None:
            raise updates.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(reply).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", github)
    return asked


PARENT = {"test-owner/studio/releases/latest": {"tag_name": "v2.9.3", "name": "2.9.3", "published_at": "2026-10-01T00:00:00Z",
                                                 "html_url": "https://github.com/test-owner/studio/releases/tag/v2.9.3"},
          "fork-owner/studio": {"fork": True, "parent": {"full_name": "test-owner/studio"}}}


def test_a_rate_limited_release_lookup_falls_back_to_the_parent(monkeypatch):
    asked = limited(monkeypatch, PARENT, {"fork-owner/studio/releases/latest"})
    release, note = updates.release_lookup("fork-owner/studio")
    assert release["tag"] == "v2.9.3" and release["repository"] == "test-owner/studio" and note is None
    assert asked == ["fork-owner/studio/releases/latest", "fork-owner/studio", "test-owner/studio/releases/latest"]


def test_when_github_refuses_every_lookup_the_page_is_told_why(history, tmp_path, monkeypatch):
    upstream, clone = history
    publish(upstream)
    refused = {"test-owner/studio/releases/latest", "test-owner/studio"}
    asked = limited(monkeypatch, {}, refused)
    status = checked(updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False))
    assert "latest_release" not in status and "rate limit" in status["release_note"]
    assert status["message"].startswith("An update is ready.") and "rate limit" in status["message"]
    asked.clear()
    assert updates.release_lookup("test-owner/studio") == (None, updates.RATE_NOTE) and asked == []  # kept: GitHub is not asked again


def test_a_release_answer_is_kept_for_an_hour(monkeypatch):
    asked = limited(monkeypatch, PARENT, set())
    first = updates.release_lookup("test-owner/studio")
    assert updates.release_lookup("test-owner/studio") == first and len(asked) == 1
    kept_at, release, note = updates.releases["test-owner/studio"]
    updates.releases["test-owner/studio"] = (kept_at - updates.RELEASE_CACHE_S - 1, release, note)
    updates.release_lookup("test-owner/studio")
    assert len(asked) == 2


def test_a_lookup_that_never_reached_github_is_tried_again(monkeypatch):
    assert updates.release_lookup("test-owner/studio") == (None, None)  # offline (the fixture)
    asked = limited(monkeypatch, PARENT, set())
    assert updates.release_lookup("test-owner/studio")[0]["tag"] == "v2.9.3" and len(asked) == 1


def test_a_failed_release_lookup_is_left_out(history, tmp_path):
    upstream, clone = history
    publish(upstream)
    status = checked(updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False))
    assert status["available"] and "latest_release" not in status


def test_a_failed_check_keeps_source_and_branch(history, tmp_path):
    _, clone = history
    (clone / "mine.txt").write_text("mine")
    status = checked(updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False))
    assert not status["available"] and "Local code changes" in status["message"]
    assert status["source"] == "test-owner/studio" and status["following"] == status["branch"] == "main"


def test_recovery_never_discards_changes_made_during_restart(history):
    upstream, clone = history
    publish(upstream)
    found = updates.inspect(clone, fetch=True)
    old, _ = updates.apply_stopped(clone, found)
    (clone / "code.py").write_text("a person's new change")
    with pytest.raises(updates.UpdateError, match="preserve"):
        updates.rollback(clone, old, found["target"])
    assert (clone / "code.py").read_text() == "a person's new change"


def test_preferences_are_per_checkout_persistent_and_default_off(history, tmp_path, monkeypatch):
    _, clone = history
    monkeypatch.setenv("STUDIO_UPDATE_SESSION", "test-session")
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False)
    assert not manager.automatic()
    manager.set_automatic(True)
    assert updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False).automatic()
    assert not updates.Manager(tmp_path / "another-clone", tmp_path / "home", threading.Lock(), lambda: False).automatic()
    manager.set_automatic(False)
    assert not manager.automatic()


def test_busy_jobs_do_not_restart_and_new_work_is_gated_atomically(history, tmp_path, monkeypatch):
    upstream, clone = history
    publish(upstream)
    monkeypatch.setenv("STUDIO_UPDATE_SESSION", "test-session")
    restarts = []
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: True, lambda: restarts.append(True))
    assert checked(manager)["available"]
    with pytest.raises(updates.UpdateError, match="Waiting"):
        manager.apply()
    assert not manager.maintenance.is_set() and not manager.request_path.exists() and not restarts
    manager.busy = lambda: False
    assert manager.apply()["updating"]
    assert json.loads(manager.request_path.read_text())["session"] == "test-session"
    assert manager.maintenance.is_set()
    deadline = time.monotonic() + 3
    while not restarts and time.monotonic() < deadline:
        time.sleep(0.01)
    assert restarts == [True]
    assert command(clone, "rev-parse", "HEAD") != command(upstream, "rev-parse", "HEAD")


def test_managed_workers_cannot_self_update(history, tmp_path, monkeypatch):
    _, clone = history
    monkeypatch.setenv("STUDIO_UPDATE_SESSION", "test-session")
    monkeypatch.setenv("STUDIO_PIDFILE", "/worker/app.pid")
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False)
    with pytest.raises(updates.UpdateError, match="workers"):
        manager.set_automatic(True)


def test_automatic_updates_wait_for_idle_and_respect_opt_out(history, tmp_path, monkeypatch):
    upstream, clone = history
    publish(upstream)
    monkeypatch.setenv("STUDIO_UPDATE_SESSION", "test-session")
    busy = [True]
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: busy[0], lambda: None)
    assert checked(manager)["available"]
    manager.tick()  # off by default, even with a known update
    assert not manager.maintenance.is_set()
    manager.set_automatic(True)
    manager.tick()
    assert "Waiting" in manager.status()["message"] and not manager.maintenance.is_set()
    manager.set_automatic(False)
    busy[0] = False
    manager.tick()
    assert not manager.maintenance.is_set()
    manager.set_automatic(True)
    manager.tick()
    assert manager.maintenance.is_set() and manager.request_path.exists()


def test_api_blocks_cross_origin_and_nonlocal_update_requests(history, tmp_path, monkeypatch):
    _, clone = history
    monkeypatch.setenv("STUDIO_UPDATE_SESSION", "test-session")
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False)
    app = FastAPI()
    app.include_router(manager.router())
    client = TestClient(app)
    assert client.get("/api/updates").status_code == 200
    assert client.post("/api/updates/settings", json={"automatic": True}).status_code == 200
    assert client.post("/api/updates/settings", json={"automatic": "false"}).status_code == 400
    assert client.post("/api/updates/settings", json={"automatic": False}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/updates/check", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/updates", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/updates/check", content="{}").status_code in (415, 422)
    remote = TestClient(app, client=("192.168.1.2", 5555))
    assert remote.get("/api/updates").status_code == 403


def test_offline_check_reports_failure_without_touching_installation(history, tmp_path, monkeypatch):
    _, clone = history
    old = command(clone, "rev-parse", "HEAD")
    real = updates.git
    def offline(pack, *args, **kwargs):
        if args[0] == "fetch":
            raise updates.UpdateError("Offline. Try again later.")
        return real(pack, *args, **kwargs)
    monkeypatch.setattr(updates, "git", offline)
    manager = updates.Manager(clone, tmp_path / "home", threading.Lock(), lambda: False)
    status = checked(manager)
    assert not status["available"] and "Offline" in status["message"]
    assert command(clone, "rev-parse", "HEAD") == old


@pytest.mark.parametrize("url", ["https://github.com/owner/studio.git", "git@github.com:owner/studio.git", "ssh://git@github.com/owner/studio.git"])
def test_github_sources(url):
    assert updates.repository(url) == "owner/studio"


@pytest.mark.parametrize("url", ["/local/repo", "https://evil.example/owner/studio", "ext::run command", "https://github.com/owner/studio.git?bad"])
def test_other_sources_are_not_update_channels(url):
    with pytest.raises(updates.UpdateError):
        updates.repository(url)
