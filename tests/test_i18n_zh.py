"""The optional Traditional Chinese pack (languages/zh-TW.js): its dictionary keeps every placeholder, its translator handles
exact text, {0}-patterns and phrases inside longer text, and the page loads it before the studio's own scripts.
python3 -m pytest tests/test_i18n_zh.py. Skipped where node is not installed; no browser."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node is not installed")

STUB = """
const store = { "studio-language": "en" };
globalThis.window = globalThis;
globalThis.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
globalThis.StudioLanguage = { current: () => store["studio-language"] };
globalThis.document = { readyState: "complete", getElementById: () => null, documentElement: {} };
"""


def run(js: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf8", delete=False) as f:
        f.write(js)
    try:
        done = subprocess.run([NODE, f.name], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(f.name)
    assert done.returncode == 0, done.stderr
    return done.stdout


def load_dictionary() -> dict:
    out = run(STUB + (ROOT / "languages/zh-TW.js").read_text("utf8") + "\nprocess.stdout.write(JSON.stringify(window.STUDIO_ZH));")
    return json.loads(out)








@needs_node
def test_dictionary_keeps_placeholders():
    data = load_dictionary()
    assert len(data["dict"]) > 1000
    for en, zh in data["dict"].items():
        assert zh.strip(), en
        assert not re.search(r"\{\d+\}", en), en
    for en, zh in data["patterns"]:
        # every value the translation shows is one the English has (a plural "s" placeholder may be left out)
        # ({0:raw} and {0:d} in the English are {0} in the translation: i18n.js)
        assert set(re.findall(r"\{\d+\}", zh)) <= {"{%s}" % n for n in re.findall(r"\{(\d+)(?::(?:raw|d))?\}", en)}, en


@needs_node
def test_translator():
    fake = {"dict": {"Queue": "佇列", "Start a new clip": "開始新片段", "Not installed on": "未安裝於", "Home": "首頁"},
            "patterns": [["Rendering {0} of {1}", "正在算圖第 {0} 段，共 {1} 段"], ["{0} failed", "{0} 失敗"]]}
    js = STUB + "window.STUDIO_ZH = " + json.dumps(fake, ensure_ascii=False) + ";\n" + \
        (ROOT / "tools/i18n-zh/overlay.js").read_text("utf8") + """
const t = window.studioTr;
process.stdout.write(JSON.stringify([t("Queue"), t("  Start   a new clip "), t("Rendering 2 of 5"), t("Home failed"),
  t("Not installed on spark-2"), t("Queued"), t("佇列"), t("3 × 480p"),
  t("Not installed on Home"), t("Home of a 1970s horror movie on tape")]));"""
    got = json.loads(run(js))
    assert got == ["佇列", "  開始新片段 ", "正在算圖第 2 段，共 5 段", "首頁 失敗", "未安裝於 spark-2", "Queued", "佇列", "3 × 480p",
                   # a name inside built-up text is swapped; a sentence with one known word in it stays English
                   "未安裝於首頁", "Home of a 1970s horror movie on tape"]


JSDOM = None
for base in filter(None, os.environ.get("NODE_PATH", "").split(os.pathsep)):
    if (Path(base) / "jsdom").is_dir():
        JSDOM = base


@needs_node
@pytest.mark.skipif(not JSDOM, reason="jsdom is not on NODE_PATH (npm i jsdom in a scratch folder)")
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_page_behaviour(lang):
    """Inputs, textareas and code untouched, an <option> without a value still reads English, the tour finds the
    translated label, dynamic text translated; "en" translates nothing (tools/i18n-zh/dom_test.js)."""
    done = subprocess.run([NODE, str(ROOT / "tools/i18n-zh/dom_test.js"), str(ROOT), lang], capture_output=True,
                          text=True, timeout=60, env={**os.environ, "NODE_PATH": JSDOM})
    assert done.returncode == 0, done.stdout + done.stderr


def test_page_uses_the_official_optional_language_hook():
    html = (STATIC / "index.html").read_text("utf8")
    assert html.index('src="language.js"') < html.index('src="shell.js"')
    assert 'src="i18n/zh-TW.js"' not in html
    assert 'src="i18n/i18n.js"' not in html




@needs_node
def test_the_441_remnants_read_chinese_and_names_and_user_text_stay():
    """Text upstream 4.4.1's 4.3.1 screens build that read half English (2026-11-10 scan of every tab): the eyebrows,
    the built-up lines of Home, Create, Machine, Brains, the Editor, Music and the Library's trash. The suite's app
    names stay English, and a sentence someone wrote is not taken apart."""

    cases = {
        "VIDEO": "影片", "FILMMAKER SUITE": "電影人套件", "Master Canvas": "Master Canvas",
        "Bring in: Master Canvas": "匯入: Master Canvas", "Apps: every tool as a one-click app": "應用程式: 每個工具都做成一鍵應用程式",
        "make several with Qwen 2.1 Turbo or FLUX3-K Fast, pick one": "用 Qwen 2.1 Turbo 或 FLUX3-K Fast 做幾張，挑一張",
        "▸ Browse the hub (22 LoRAs in 8 groups)": "▸ 瀏覽 LoRA 中心（22 個 LoRA，分成 8 組）",
        "step 1 of 5 · Intent": "第 1／5 步 · 意圖", "Segment overview · 0 s total": "段落總覽 · 共 0 s",
        "Timeline 50.6 s · 5 clips · project Story. Out = 0 plays a clip to its end.": "時間軸 50.6 s · 5 個片段 · 專案「Story」。Out = 0 會把片段播到結尾。",
        "Trash: empty": "回收桶：空的", "Trash: 3 files · 1.2 GB": "回收桶：3 個檔案 · 1.2 GB",
        "285 GB used · 722 GB free of 1007 GB": "已用 285 GB · 可用 722 GB / 1007 GB", "load 25.79 on 112 cores": "負載 25.79（112 核心）",
        "Read 0 s ago": "0 秒前讀取", "Claude Code and Codex · ChatGPT sign-in are not on this machine.": "Claude Code和Codex · ChatGPT sign-in都不在這台機器上。",
        "Running default · Found at studio:bolt · 1 model": "執行中：default · 位於 studio:bolt · 1 個模型",
        "Add [Instrumental] on a line of its own": "把 [Instrumental] 單獨加成一行",
        # what someone wrote, and file names, are left as they are
        "A man and a dog walk with the camera.": "A man and a dog walk with the camera.", "A woman waiting": "A woman waiting",
        "Read the logs from long ago": "Read the logs from long ago", "default": "default",
        "Open the file": "Open the file", "clean sheets": "clean sheets", "Slideshow.mp4": "Slideshow.mp4", "2019_Explainer.mp4": "2019_Explainer.mp4",
    }
    js = STUB + "globalThis.MutationObserver = class { observe() {} disconnect() {} };\n" + \
        (ROOT / "languages/zh-TW.js").read_text("utf8") + "\n" + "" + \
        "\nprocess.stdout.write(JSON.stringify(" + json.dumps(list(cases), ensure_ascii=False) + ".map(s => window.studioTr(s))));"
    got = json.loads(run(js.replace('"studio-language": "en"', '"studio-language": "zh"')))
    assert dict(zip(cases, got)) == cases


@needs_node
def test_fragment_matcher_compiles_without_blocking_first_paint():
    """Both one-byte (ASCII) and two-byte (Unicode) misses through fragRe compile in well under 500 ms."""

    js = STUB + "globalThis.MutationObserver = class { observe() {} disconnect() {} };\n" + \
        (ROOT / "languages/zh-TW.js").read_text("utf8") + "\n" + "" + """
const t0 = Date.now();
const out = [window.studioTr("TensorFold"), window.studioTr("TensorFold · CUDA")];
const elapsed = Date.now() - t0;
process.stdout.write(JSON.stringify({ out, elapsed }));"""
    res = json.loads(run(js.replace('"studio-language": "en"', '"studio-language": "zh"')))
    assert res["out"] == ["TensorFold", "TensorFold · CUDA"]
    assert res["elapsed"] < 500, f"fragRe blocked main thread for {res['elapsed']} ms"
