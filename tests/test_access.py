"""The studio's access key (app/access.py): every request for the studio's data carries it, from anywhere; the page
loads without it and asks; /?key= and the page's prompt keep it as this app's cookie; the studio's own tools send it.
Run: STUDIO_HOME=$(mktemp -d) python3 -m pytest tests/test_access.py"""
import os
import stat
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import access  # noqa: E402
import remote  # noqa: E402
import server  # noqa: E402
import sparks  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

KEY = "k3y-for-the-tests-0123456789"


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("STUDIO_ACCESS_KEY", "on")
    monkeypatch.setattr(server, "ACCESS_KEY", KEY)
    return TestClient(server.app, follow_redirects=False)


def test_the_studios_data_asks_for_the_key_from_anywhere(keyed):
    for path in ("/api/state", "/api/jobs", "/files/renders/x.mp4", "/api/updates"):
        said = keyed.get(path)
        assert said.status_code == 401 and said.json()["access"] == "key", path
    assert keyed.post("/api/jobs", json={"kind": "video"}).status_code == 401  # before anything else looks at it
    assert keyed.get("/api/state", headers={"X-Studio-Key": "wrong"}).status_code == 401
    # a launcher from before the key checks a new server's start here: answered, with nothing but the session
    assert keyed.get("/api/updates/health").status_code == 200 and set(keyed.get("/api/updates/health").json()) == {"session"}


def test_the_key_is_taken_as_a_header_a_bearer_token_or_this_apps_cookie(keyed):
    assert keyed.get("/api/state", headers={"X-Studio-Key": KEY}).status_code == 200
    assert keyed.get("/api/state", headers={"Authorization": "Bearer " + KEY}).status_code == 200
    assert keyed.get("/api/state", headers={"Cookie": f"studio_key_{server.APP_PORT}={KEY}"}).status_code == 200
    assert keyed.get("/api/state", headers={"Cookie": f"studio_key_1{server.APP_PORT}={KEY}"}).status_code == 401  # another app's


def test_the_page_loads_without_the_key_and_asks_for_it(keyed):
    page = keyed.get("/")
    assert page.status_code == 200 and "askKey" in page.text and "set-cookie" not in page.headers


def test_opening_the_page_with_the_key_keeps_it_and_drops_it_from_the_address(keyed):
    said = keyed.get(f"/?key={KEY}")
    assert said.status_code == 303 and said.headers["location"] == "/"
    cookie = said.headers["set-cookie"]
    assert cookie.startswith(f"studio_key_{server.APP_PORT}={KEY};") and "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie
    assert keyed.get(f"/?tab=Video&key={KEY}").headers["location"] == "/?tab=Video"
    assert "Secure" in keyed.get(f"/?key={KEY}", headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"]  # tailscale serve
    wrong = keyed.get("/?key=nope")
    assert wrong.status_code == 200 and "set-cookie" not in wrong.headers


def test_the_key_typed_on_the_page(keyed):
    good = keyed.post("/api/key", json={"key": KEY})
    assert good.status_code == 200 and good.headers["set-cookie"].startswith(f"studio_key_{server.APP_PORT}={KEY};")
    bad = keyed.post("/api/key", json={"key": "nope"})
    assert bad.status_code == 401 and "set-cookie" not in bad.headers
    assert keyed.post("/api/key", json={"key": KEY}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_off_when_asked(monkeypatch):
    monkeypatch.setenv("STUDIO_ACCESS_KEY", "off")
    assert TestClient(server.app).get("/api/state").status_code == 200


def test_the_key_file_is_made_once_private_and_kept(tmp_path):
    made = access.ensure(tmp_path)
    assert len(made) >= 30 and access.read(tmp_path) == made == access.ensure(tmp_path)
    assert stat.S_IMODE(access.path(tmp_path).stat().st_mode) == 0o600
    access.path(tmp_path).chmod(0o644)
    assert access.ensure(tmp_path) == made and stat.S_IMODE(access.path(tmp_path).stat().st_mode) == 0o600
    assert access.headers_for(tmp_path) == {"X-Studio-Key": made} and access.headers_for(tmp_path / "none") == {}
    assert not access.matches("", "") and not access.matches(made, "")


def test_the_studios_own_clients_send_it(monkeypatch):
    seen = []

    class Answer:
        def __init__(self, request):
            seen.append(request)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, *_):
            return b'{"id": "1", "status": "done"}' if not seen[-1].full_url.endswith(".mp4") else b""

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: Answer(request))
    sparks.KEYS["http://127.0.0.1:41001"] = "worker-key"
    try:
        sparks.call("http://127.0.0.1:41001", "/api/spark/self")
        assert seen[-1].get_header("X-studio-key") == "worker-key"
    finally:
        sparks.KEYS.clear()
    remote.remember("spark-5:7870", "their-key")
    remote.job("http://spark-5:7870", "1013-131100-5eb633")
    assert seen[-1].get_header("X-studio-key") == "their-key"
    remote.job("http://spark-6:7870", "1013-131100-5eb633")
    assert seen[-1].get_header("X-studio-key") is None
