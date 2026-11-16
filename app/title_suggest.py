"""An optional title for a take's folder, suggested by a local model (issue #293). Off unless the person switches it
on (Settings → Presets, ``presets.title_suggest``), and nothing is asked of any model while it is off.

A take's folder is named by its job's title, or by the first words of its prompt (take_names.py); no model is
involved. With the setting on, Create offers a short title before a clip is queued, and the person keeps, edits or
ignores it. It is:

- text in, text out: one chat completion on a local OpenAI-compatible model server (Settings → Connections, or a
  local agent, or the Studio's own engine). A CLI brain is never asked, since those run with tools and file access;
- untrusted: the answer is cut down to a few plain words here, and still goes through take_names.slug like any title;
- bounded: an answer that takes longer than ``SECONDS`` is dropped. Any failure leaves the deterministic name, and
  nothing here is called when a job is queued or rendered, so it can never delay or fail one.
"""
from __future__ import annotations

import re
import threading

import take_names

SECONDS = 15
PROMPT_LIMIT = 800  # of the prompt, as much as a title needs
SYSTEM = ("You name video clips. Answer with a title of two to five plain words for the clip described. "
          "Only the title: no quotes, no punctuation, no explanation.")


def clean(text) -> str:
    """A model's answer as a title: its first line with words in it, without thinking, markup, quotes or a
    "Title:" label, at most six words and 48 characters. "" when nothing usable is left."""
    text = re.sub(r"<think>.*?</think>|<think>.*", " ", str(text or ""), flags=re.S | re.I)
    for line in text.splitlines():
        line = re.sub(r"^[\s#>*_\-]*(?:\d+[.)]\s*)?[\s*_]*(?:(?:title|name)\s*[:\-])?", "", line, flags=re.I)
        words = re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", line)
        if not words:
            continue
        out = ""
        for word in words[:take_names.WORDS]:
            longer = f"{out} {word}" if out else word
            if len(longer) > take_names.LIMIT:
                break
            out = longer
        return out or words[0][:take_names.LIMIT]
    return ""


def local_brain() -> tuple[str, str]:
    """(endpoint, model) of the local model server a title is asked of: the default brain's when that is a local
    agent, else the one set in Settings → Connections (empty = the usual local ports)."""
    import agents  # noqa: PLC0415  (agents.py is large; only needed when a title is asked for)

    now = agents.default_brain()
    if now.get("backend") == "local":
        return now.get("endpoint") or "", now.get("model") or ""
    saved = agents.saved_settings()
    return saved.get("local_endpoint") or "", saved.get("local_model") or ""


def _ask(words: str, seconds: int) -> str:
    import agents  # noqa: PLC0415

    endpoint, model = local_brain()
    return agents.ask("local", SYSTEM, words, "fast", endpoint, model, timeout=seconds)


def suggest(words: str, ask=None, seconds: int = SECONDS) -> dict:
    """{"ok", "title", "name", "error"} for the clip ``words`` describe: ``title`` is the suggestion and ``name`` the
    part of the folder's name it would give. Never raises and never takes much longer than ``seconds``."""
    words = " ".join(str(words or "").split())[:PROMPT_LIMIT]
    if not words:
        return {"ok": False, "title": "", "name": "", "error": "Describe the clip first."}
    said: dict = {}

    def run() -> None:
        try:
            said["text"] = (ask or _ask)(words, seconds)
        except Exception as error:  # the reason goes to the page as it is
            said["error"] = str(error)[:300] or type(error).__name__

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds + 2)
    if worker.is_alive():
        return {"ok": False, "title": "", "name": "", "error": f"The local model did not answer in {seconds} seconds."}
    title = clean(said.get("text"))
    if not title:
        return {"ok": False, "title": "", "name": "", "error": said.get("error") or "The local model gave no usable title."}
    return {"ok": True, "title": title, "name": take_names.slug(title, "clip"), "error": ""}
