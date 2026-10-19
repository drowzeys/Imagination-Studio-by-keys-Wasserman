"""Motion Previs from a link (app/motion.py, /api/motion/links): python3 -m pytest tests/test_motion_links.py.
A video file is read as it is; a video page goes through yt-dlp, here a stand-in package on PYTHONPATH."""
import functools
import http.server
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "ImaginationStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import motion  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
SAMPLE = ROOT / "samples" / "baker_studio_864x480_5s.mp4"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffprobe") or not SAMPLE.is_file(), reason="ffprobe and the sample clip")

YT = r'''
import shutil, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
url = args[-1]
if "broken" in url:
    print("ERROR: [generic] Unsupported URL: " + url); sys.exit(1)
for p in (12.5, 50.0, 100.0):
    print("[download]  %.1f%% of 1.20MiB at 2.00MiB/s ETA 00:00" % p)
shutil.copy(sys.argv[0].rsplit("/yt_dlp/", 1)[0] + "/sample.mp4", out.replace("%(ext)s", "mp4"))
'''


def until(link_id, seconds=30):
    ends = time.time() + seconds
    while time.time() < ends:
        item = client.get(f"/api/motion/links/{link_id}").json()
        if item["state"] != "running":
            return item
        time.sleep(0.1)
    raise AssertionError(item)


def test_only_an_address_out_on_the_internet_is_read():
    for url in ("file:///etc/passwd", "http://127.0.0.1:7870/api/state", "http://localhost/x.mp4", "http://10.0.0.5/clip.mp4",
                "http://169.254.169.254/latest/meta-data", "ftp://example.com/clip.mp4", "https://"):
        assert client.post("/api/motion/links", json={"url": url}).status_code == 400, url


@needs_ffmpeg
def test_a_video_files_address_is_read_into_the_studio_folder_and_can_be_analysed(monkeypatch, tmp_path):
    shutil.copy(SAMPLE, tmp_path / "clip.mp4")
    (tmp_path / "page.html").write_text("<html>not a video</html>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(motion, "public_link", lambda url: url)  # the test's server is on this machine
    monkeypatch.setattr(motion, "link_reader", lambda python: None)
    try:
        item = until(client.post("/api/motion/links", json={"url": base + "/clip.mp4"}).json()["id"])
        assert item["state"] == "done" and item["path"].startswith("uploads/links/link-") and item["path"].endswith(".mp4")
        assert 4.5 < item["duration"] < 5.5 and (server.HOME / item["path"]).is_file()
        assert any(s["path"] == item["path"] for s in client.get("/api/motion/sources").json())  # a source like an upload
        page = until(client.post("/api/motion/links", json={"url": base + "/page.html"}).json()["id"])
        assert page["state"] == "failed" and "yt-dlp" in page["error"] and "setup-motion.sh" in page["error"]
    finally:
        httpd.shutdown()


@needs_ffmpeg
def test_a_video_page_goes_through_yt_dlp_and_its_error_is_shown(monkeypatch, tmp_path):
    (tmp_path / "yt_dlp").mkdir()
    (tmp_path / "yt_dlp" / "__init__.py").write_text("")
    (tmp_path / "yt_dlp" / "__main__.py").write_text(YT)
    shutil.copy(SAMPLE, tmp_path / "sample.mp4")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(motion, "public_link", lambda url: url)
    monkeypatch.setattr(motion, "link_reader", lambda python: sys.executable)
    item = until(client.post("/api/motion/links", json={"url": "https://video.example/watch?v=abc"}).json()["id"])
    assert item["state"] == "done" and item["progress"] == 100.0 and item["path"].endswith(".mp4")
    bad = until(client.post("/api/motion/links", json={"url": "https://video.example/broken"}).json()["id"])
    assert bad["state"] == "failed" and bad["error"].startswith("[generic] Unsupported URL")
    assert not list((server.HOME / "uploads/links").glob(bad["id"] + ".*"))  # nothing half-read is left


def test_a_redirect_into_this_machine_is_refused(monkeypatch):
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "/inside.mp4")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    first = f"http://127.0.0.1:{httpd.server_address[1]}/clip.mp4"
    checked = motion.public_link

    def public(url):  # the pasted address passes as if it were public; where it redirects is checked for real
        return url if url == first else checked(url)
    monkeypatch.setattr(motion, "public_link", public)
    monkeypatch.setattr(motion, "link_reader", lambda python: None)
    try:
        item = until(client.post("/api/motion/links", json={"url": first}).json()["id"])
        assert item["state"] == "failed" and "redirected to 127.0.0.1" in item["error"], item
    finally:
        httpd.shutdown()
