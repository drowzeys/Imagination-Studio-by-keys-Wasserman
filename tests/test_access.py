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
    monkeypatch.setattr(remote.OPENER, "open", lambda request, timeout=None: Answer(request))  # remote.py's opener refuses redirects
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


def test_this_computer_itself_needs_no_key_but_tailscale_and_the_network_do(keyed, monkeypatch):
    """The key is for remote access: a loopback client naming localhost passes; `tailscale serve` (a loopback client
    under a .ts.net name) and a LAN address are asked."""

    here = TestClient(server.app, client=("127.0.0.1", 50000), follow_redirects=False)
    assert here.get("/api/state", headers={"host": "localhost:7870"}).status_code == 200
    assert here.get("/api/state", headers={"host": "127.0.0.1:7870"}).status_code == 200
    assert here.get("/api/key", headers={"host": "localhost:7870"}).json() == {"needed": False, "signed_in": True, "local": True}
    served = here.get("/api/state", headers={"host": "studio-mac.tail1234.ts.net"})
    assert served.status_code == 401 and served.json()["access"] == "key"
    lan = TestClient(server.app, client=("192.168.1.50", 50000))
    assert lan.get("/api/state", headers={"host": "192.168.1.10:7870"}).status_code == 401
    assert lan.get("/api/key", headers={"host": "192.168.1.10:7870"}).json()["needed"] is True
    assert access.local("127.0.0.1", "[::1]:7870") and not access.local("192.168.1.50", "localhost:7870")


def test_this_computer_by_its_network_address_or_name_needs_no_key(keyed, monkeypatch):
    """HOST=0.0.0.0: a browser on this computer opening http://<its address>:7870 or http://<name>.local:7870 comes
    from the computer's own address and passes; another computer, or a proxy here naming a .ts.net Host, is asked."""

    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setattr(server, "network_addresses", lambda: ["192.168.1.10", "10.0.10.4"])
    monkeypatch.setitem(server.OWN, "labels", {"studio-mac"})
    itself = TestClient(server.app, client=("192.168.1.10", 50000))
    for host in ("192.168.1.10:7870", "10.0.10.4:7870", "studio-mac.local:7870", "Studio-Mac:7870"):
        assert itself.get("/api/state", headers={"host": host}).status_code == 200, host
    assert itself.get("/api/key", headers={"host": "studio-mac.local:7870"}).json()["local"] is True
    assert itself.get("/api/state", headers={"host": "studio-mac.tail1234.ts.net"}).status_code == 401
    assert TestClient(server.app, client=("192.168.1.50", 50000)).get(
        "/api/state", headers={"host": "192.168.1.10:7870"}).status_code == 401
    monkeypatch.setenv("HOST", "127.0.0.1")  # listening on loopback only: nothing but loopback is this computer
    assert itself.get("/api/state", headers={"host": "192.168.1.10:7870"}).status_code == 401
    assert not access.local("192.168.1.10", "evil.example:7870", ["192.168.1.10"], ["studio-mac"])


def test_this_computer_by_its_local_name_through_loopback_needs_no_key(keyed, monkeypatch):
    """On a Mac http://<name>.local:7870 resolves to 127.0.0.1 on the machine itself: that passes. A loopback client
    under a one-word tailnet name or a .ts.net name (a `tailscale serve` proxy) is still asked."""

    monkeypatch.setattr(server, "network_addresses", lambda: ["192.168.1.10"])
    monkeypatch.setitem(server.OWN, "labels", {"studio-mac"})
    here = TestClient(server.app, client=("127.0.0.1", 50000))
    for host in ("studio-mac.local:7870", "192.168.1.10:7870"):
        assert here.get("/api/state", headers={"host": host}).status_code == 200, host
    for host in ("studio-mac:7870", "studio-mac.tail1234.ts.net"):
        assert here.get("/api/state", headers={"host": host}).status_code == 401, host
    assert here.get("/api/state", headers={"host": "other-mac.local:7870"}).status_code == 403  # not this computer's name


def test_the_settings_panel_shares_the_key_and_makes_a_new_one(keyed, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "HOME", tmp_path)
    away = TestClient(server.app, client=("192.168.1.50", 50000))
    assert away.get("/api/key/share").status_code == 401  # the key guards the key
    shared = away.get("/api/key/share", headers={"X-Studio-Key": KEY}).json()
    assert shared["key"] == KEY and any(link["url"].endswith("/?key=" + KEY) for link in shared["links"])
    made = away.post("/api/key/new", json={}, headers={"X-Studio-Key": KEY})
    assert made.status_code == 200 and server.ACCESS_KEY != KEY and access.read(tmp_path) == server.ACCESS_KEY
    assert made.headers["set-cookie"].startswith(f"studio_key_{server.APP_PORT}={server.ACCESS_KEY};")
    fresh = TestClient(server.app, client=("192.168.1.51", 50000))  # without the new cookie this client just got
    assert fresh.get("/api/state", headers={"X-Studio-Key": KEY}).status_code == 401  # the old key is signed out


def test_a_studio_open_to_the_network_offers_a_sign_in_link_for_it(keyed, monkeypatch, tmp_path):
    """HOST=0.0.0.0: Settings → Access key links (and QR-codes) this computer's network address for another computer;
    on loopback only, no network link."""

    monkeypatch.setattr(server, "HOME", tmp_path)
    monkeypatch.setattr(server, "network_addresses", lambda: ["192.168.1.10", "10.0.10.4"])
    here = TestClient(server.app, client=("127.0.0.1", 50000))
    monkeypatch.setenv("HOST", "0.0.0.0")
    said = here.get("/api/key/share", headers={"host": "localhost:7870"}).json()
    network = [link for link in said["links"] if link.get("kind") == "network"]
    assert said["on_network"] and [link["url"] for link in network] == [
        f"http://192.168.1.10:{server.APP_PORT}/?key={KEY}", f"http://10.0.10.4:{server.APP_PORT}/?key={KEY}"]
    monkeypatch.setenv("HOST", "127.0.0.1")
    said = here.get("/api/key/share", headers={"host": "localhost:7870"}).json()
    assert not said["on_network"] and not [link for link in said["links"] if link.get("kind") == "network"]


def test_network_addresses_skip_containers_tunnels_and_tailscale(monkeypatch):
    shown = "\n".join(["1: lo    inet 127.0.0.1/8 scope host lo",
                       "2: enP7s7    inet 10.0.10.4/24 brd 10.0.10.255 scope global enP7s7",
                       "3: eth0    inet 192.168.1.10/22 brd 192.168.3.255 scope global eth0",
                       "4: tailscale0    inet 100.86.1.2/32 scope global tailscale0",
                       "5: docker0    inet 172.17.0.1/16 scope global docker0",
                       "6: br-0a1b2c3d4e5f    inet 172.18.0.1/16 scope global br-0a1b2c3d4e5f",
                       "7: wlan0    inet 8.8.4.4/24 scope global wlan0"])

    class Done:
        stdout = shown
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: Done())
    monkeypatch.setattr(server, "_NETWORK", {"at": float("-inf"), "value": []})  # stale on any clock (a new runner)
    assert server.network_addresses() == ["192.168.1.10", "10.0.10.4"]
