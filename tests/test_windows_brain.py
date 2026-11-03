"""Small Windows writing brain: file verification and the text-only API gate."""
import hashlib
import io
import json

import pytest

from test_windows_ui import agents, client, server  # noqa: F401
import windows_brain


def test_weight_verification_checks_size_and_sha256(tmp_path):
    path = tmp_path / "model.gguf"
    data = b"small model stand-in"
    path.write_bytes(data)
    windows_brain.verify_file(path, len(data), hashlib.sha256(data).hexdigest())
    with pytest.raises(RuntimeError, match="wrong-sized"):
        windows_brain.verify_file(path, len(data) + 1, hashlib.sha256(data).hexdigest())
    with pytest.raises(RuntimeError, match="SHA-256"):
        windows_brain.verify_file(path, len(data), "0" * 64)


def test_a_bad_existing_download_is_not_silently_replaced(tmp_path, monkeypatch):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"bad")
    monkeypatch.setattr(windows_brain.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not redownload"))
    with pytest.raises(RuntimeError, match="SHA-256"):
        windows_brain.fetch("https://example.invalid/model", path, 3, "0" * 64)


def test_windows_slate_writing_uses_the_loopback_model_and_bounds_its_answer(client, monkeypatch):
    seen = []
    monkeypatch.setattr(agents, "saved_settings", lambda: {
        "local_endpoint": "http://127.0.0.1:8080/v1", "local_model": windows_brain.MODEL_ID,
    })
    monkeypatch.setattr(agents.slate, "find_local", lambda address: (address, [windows_brain.MODEL_ID]))

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"shots":[{"name":"Wide"}]}'}}]}).encode()

    def answer(request, timeout):
        seen.append((request.full_url, json.loads(request.data), timeout))
        return Answer()
    monkeypatch.setattr(agents.urllib.request, "urlopen", answer)
    response = client.post("/api/slate/brain", json={
        "backend": "local", "prompt": "Plan one shot.", "expectJson": True,
    })
    assert response.status_code == 200 and response.json()["ok"]
    assert response.json()["json"] == {"shots": [{"name": "Wide"}]}
    url, body, timeout = seen[0]
    assert url == "http://127.0.0.1:8080/v1/chat/completions"
    assert body["max_tokens"] == 1024 and body["model"] == windows_brain.MODEL_ID
    assert body["messages"][-1]["content"] == "Plan one shot."
    assert timeout > 0


@pytest.mark.parametrize("address", ["", "studio:bolt", "http://192.168.1.10:8080",
                                      "https://127.0.0.1:8080", "http://user:password@localhost:8080"])
def test_windows_writing_refuses_non_loopback_or_implicit_bolt(client, monkeypatch, address):
    monkeypatch.setattr(agents, "saved_settings", lambda: {"local_endpoint": address, "local_model": ""})
    monkeypatch.setattr(agents.slate, "find_local", lambda *args: pytest.fail("must refuse before discovery"))
    response = client.post("/api/slate/brain", json={"backend": "local", "prompt": "Plan."})
    assert response.status_code == 200 and not response.json()["ok"]
    assert "explicit loopback" in response.json()["error"]


def test_windows_writing_does_not_enable_other_cli_or_local_mcp_agents(client, monkeypatch):
    monkeypatch.setattr(agents, "saved_settings", lambda: {})
    monkeypatch.setattr(agents, "cli", lambda *args: pytest.fail("must not launch unqualified CLI"))
    response = client.post("/api/slate/brain", json={"backend": "claude", "prompt": "Plan."})
    assert not response.json()["ok"] and "Windows writing assistance" in response.json()["error"]
    assert not agents.is_harness("local", {})
    assert agents.automatic_agent("local", "http://127.0.0.1:8080/v1", "") == ""
    with pytest.raises(server.HTTPException) as error:
        agents.submit({"backend": "local", "prompt": "Use Studio tools."})
    assert error.value.status_code == 501


@pytest.mark.parametrize("content,reason,error", [
    ("partial prompt", "length", "answer limit"),
    ("", "stop", "no text answer"),
])
def test_windows_writing_does_not_apply_truncated_or_empty_answers(client, monkeypatch, content, reason, error):
    monkeypatch.setattr(agents, "saved_settings", lambda: {
        "local_endpoint": "http://127.0.0.1:8080/v1", "local_model": windows_brain.MODEL_ID,
    })
    monkeypatch.setattr(agents.slate, "find_local", lambda address: (address, [windows_brain.MODEL_ID]))
    data = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": reason}]}).encode()
    monkeypatch.setattr(agents.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    response = client.post("/api/slate/brain", json={"backend": "local", "prompt": "Plan."})
    assert not response.json()["ok"] and error in response.json()["error"]
