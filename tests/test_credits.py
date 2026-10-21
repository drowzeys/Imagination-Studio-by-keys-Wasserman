"""Everyone whose work the studio uses is credited in the repo (CREDITS.md) and on the page (Settings → About).

The page's *Every credit* panel draws CREDITS.md and THIRD_PARTY_NOTICES.md from /api/credits, so the two cannot
drift; its short Credits list names the makers of everything the studio runs (CORE below). A LoRA preset's author is
named in CREDITS.md and on its card in Looks."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("STUDIO_HOME", str(Path(tempfile.mkdtemp()) / "TensorFoldStudio"))
sys.path.insert(0, str(ROOT / "app"))
import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

CREDITS = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
PAGE = (ROOT / "app/static/settings.js").read_text(encoding="utf-8")

# the makers of what the studio runs: named in CREDITS.md and in the page's short list
CORE = ["Keys (drowzeys)", "Sam Wasserman", "Wasserman Productions", "Qwen", "Black Forest Labs", "OpenMOSS", "MiniMax",
        "Fish Audio", "Stability AI", "MMAudio", "kijai", "Ash Hart", "TensorFold", "NVIDIA", "MiaAI-Lab", "antirez",
        "RobZombAI", "mrbizarro", "Filip Strand", "felix-ab", "Comfy Org", "MLX", "FastVideo", "LightX2V", "Viggle",
        "speach1sdef178", "rookiestar28", "ddalcu", "larryvrh", "TaoLiveAIGC", "MATLOWAI", "dntpi", "LBH-123-AI",
        "bbaudio-2025", "Alissonerdx", "Akatz Labs", "SAM 3.1", "MediaPipe", "Depth Anything", "OpenCV", "ONNX Runtime",
        "llama.cpp", "Whisper", "faster-whisper", "yt-dlp", "FFmpeg", "PyTorch", "diffusers", "FastAPI", "Geist",
        "Instrument Serif", "blender-mcp", "LobeHub", "itxabdullaa", "Mobius Labs", "Xenova", "Descript", "SGLang-Omni",
        "xgrammar", "Zig", "BusyBox", "Simple Icons", "DeepBeepMeep", "VisualInference", "Synchformer", "BigVGAN", "T5"]


def test_core_makers_are_credited_in_the_repo_and_on_the_page():
    assert [name for name in CORE if name.lower() not in CREDITS.lower()] == []
    assert [name for name in CORE if name.lower() not in PAGE.lower()] == []


def test_every_lora_preset_author_is_credited():
    presets = json.loads((ROOT / "app/lora-presets.json").read_text(encoding="utf-8"))
    presets = presets if isinstance(presets, list) else presets.get("loras") or presets.get("presets") or []
    authors = {p["author"].split(" (")[0] for p in presets if p.get("author")}
    assert authors and sorted(a for a in authors if a not in CREDITS) == []


def test_the_page_shows_the_repos_own_credit_files():
    said = TestClient(server.app).get("/api/credits").json()
    assert said["credits"] == CREDITS and said["notices"] == (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "setFullCredits()" in PAGE and '"/api/credits"' in PAGE
