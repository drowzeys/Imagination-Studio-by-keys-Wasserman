"""An optional title for a take's folder from a local model (issue #293)."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
import take_names  # noqa: E402
import title_suggest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app)
settings = server.settings


def test_an_answer_is_cut_down_to_a_few_plain_words():
    assert title_suggest.clean('"Neon Harbor at Dawn"') == "Neon Harbor at Dawn"
    assert title_suggest.clean("<think>the user wants...</think>\n\n**Title:** Rain on the Pier.\nBecause it rains.") == "Rain on the Pier"
    assert title_suggest.clean("1. Fox in Snow") == "Fox in Snow"
    assert title_suggest.clean("one two three four five six seven eight") == "one two three four five six"
    assert len(title_suggest.clean("supercalifragilistic " * 6)) <= take_names.LIMIT
    assert title_suggest.clean("<think>never closed") == ""
    assert title_suggest.clean("  \n ***\n") == ""


def test_what_a_model_says_is_only_ever_a_name():
    said = title_suggest.suggest("a boat", ask=lambda words, seconds: "../../etc/passwd; rm -rf ~")
    assert said["ok"] and said["title"] == "etc passwd rm rf"
    assert said["name"] == "etc-passwd-rm-rf"  # the part of the folder's name: take_names.slug, as any title


def test_a_model_that_fails_or_says_nothing_leaves_the_usual_name():
    def broken(words, seconds):
        raise RuntimeError("no local model server answers")

    assert title_suggest.suggest("a boat", ask=broken) == {"ok": False, "title": "", "name": "", "error": "no local model server answers"}
    assert not title_suggest.suggest("a boat", ask=lambda words, seconds: "  ")["ok"]
    assert title_suggest.suggest("   ", ask=lambda words, seconds: "x")["error"] == "Describe the clip first."


def test_a_slow_model_is_not_waited_for():
    started = time.time()
    said = title_suggest.suggest("a boat", ask=lambda words, seconds: time.sleep(30), seconds=0)
    assert not said["ok"] and "did not answer" in said["error"] and time.time() - started < 5


def test_only_the_start_of_a_long_prompt_is_sent():
    seen = []
    title_suggest.suggest("word " * 2000, ask=lambda words, seconds: seen.append(words) or "A Title")
    assert len(seen[0]) <= title_suggest.PROMPT_LIMIT


def test_it_is_off_until_switched_on_and_no_model_is_asked_while_off(monkeypatch):
    asked = []
    monkeypatch.setattr(title_suggest, "_ask", lambda words, seconds: asked.append(words) or "Harbor at Dawn")
    assert settings.DEFAULTS["presets"]["title_suggest"] is False
    refused = client.post("/api/title/suggest", json={"text": "a harbor at dawn"})
    assert refused.status_code == 409 and not asked
    assert client.post("/api/settings", json={"presets": {"title_suggest": True}}).status_code == 200
    assert client.post("/api/title/suggest", json={"text": 5}).status_code == 400
    said = client.post("/api/title/suggest", json={"text": "a harbor at dawn"}).json()
    assert said == {"ok": True, "title": "Harbor at Dawn", "name": "harbor-at-dawn", "error": ""} and asked == ["a harbor at dawn"]
    assert client.post("/api/settings", json={"presets": {"title_suggest": False}}).status_code == 200
    assert client.post("/api/title/suggest", json={"text": "a harbor at dawn"}).status_code == 409 and len(asked) == 1


def test_a_cli_brain_is_never_asked(monkeypatch):
    import agents

    calls = []
    monkeypatch.setattr(agents, "default_brain", lambda: {"backend": "claude", "endpoint": "", "model": ""})
    monkeypatch.setattr(agents, "saved_settings", lambda: {"local_endpoint": "http://127.0.0.1:11434", "local_model": "m"})
    monkeypatch.setattr(agents, "ask", lambda backend, *rest, **more: calls.append((backend, rest[3], rest[4])) or "A Title")
    assert title_suggest.suggest("a boat")["ok"] and calls == [("local", "http://127.0.0.1:11434", "m")]
