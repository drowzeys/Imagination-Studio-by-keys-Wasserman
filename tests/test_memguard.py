"""The machine memory guard (scripts/memguard.py) and what the one-shot setups and the agent notes say about it.
python3 -m pytest tests/test_memguard.py. Nothing is installed and no service is started: install and uninstall are
planned for each platform and carried out in a dry run, or into a temporary folder with the service commands left out."""
import importlib.util
import json
import os
import plistlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("memguard", ROOT / "scripts/memguard.py")
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)

LIMITS = mg.thresholds(128_000, 12_800)  # warn 19.2 GB, floor 12.8, critical 6.4


def test_thresholds_follow_the_studios_floor(monkeypatch):
    monkeypatch.delenv("STUDIO_MEMGUARD_FLOOR_MB", raising=False)
    assert mg.thresholds(124_500) == {"warn_mb": 18_675, "floor_mb": 12_450, "critical_mb": 6_225}
    assert mg.thresholds(32_000)["floor_mb"] == 8000  # never under 8 GB
    monkeypatch.setenv("STUDIO_MEMGUARD_FLOOR_MB", "20000")
    assert mg.thresholds(124_500)["floor_mb"] == 20_000


@pytest.mark.parametrize("now, fall, level", [
    ({"available_mb": 60_000}, 0, "ok"),
    ({"available_mb": 18_000}, 0, "low"),
    ({"available_mb": 12_000}, 0, "floor"),
    ({"available_mb": 6_000}, 0, "critical"),
    ({"available_mb": 60_000, "pressure": "warn"}, 0, "low"),             # macOS says warn
    ({"available_mb": 60_000, "pressure": "critical"}, 0, "critical"),
    ({"available_mb": 60_000, "commit_left_mb": 9_000}, 0, "floor"),      # Windows: the commit nearly used up
    ({"available_mb": 20_000, "some_pct": 15.0}, 0, "low"),               # a stall near the floor: the early sign
    ({"available_mb": 60_000, "some_pct": 50.0, "stall_pct": 40.0}, 0, "ok"),  # a cold start's page-cache stall, half the machine free
    ({"available_mb": 22_000, "stall_pct": 25.0}, 0, "floor"),
    ({"available_mb": 30_000}, 9_000, "low"),                             # at 9 GB a second the floor is two seconds away
    ({"available_mb": 30_000}, 29_500, "critical"),                       # an engine's load: past critical within a second
])
def test_levels(now, fall, level):
    assert mg.level_of(now, LIMITS, fall) == level


def test_actions_escalate_with_cooldowns_and_the_opt_in():
    memo = {}
    assert mg.decide("ok", memo, 0, False, True) == []
    assert mg.decide("low", memo, 0, False, True) == ["release"]
    assert mg.decide("low", memo, 5, False, True) == []  # cooldown
    assert mg.decide("floor", memo, 11, False, True) == ["release"]  # the person did not opt in: their servers stay
    assert mg.decide("floor", memo, 12, True, True) == ["stop_foreign"]
    memo = {}
    assert mg.decide("critical", memo, 100, False, True) == ["release"]  # the grace: a dip that passes is not a cancel
    assert mg.decide("critical", memo, 100 + mg.CRITICAL_GRACE_S, False, True) == ["cancel_render"]
    assert mg.decide("critical", {}, 0, False, False) == ["release"]  # --no-cancel
    assert mg.decide("ok", {}, 0, False, True, crowded=True) == ["release_cache"]


def test_the_page_cache_crowding_free_memory_is_seen_and_polled_fast():
    crowded = {"available_mb": 50_000, "free_mb": 2_000, "cache_mb": 45_000}
    assert mg.cache_crowding(crowded, LIMITS)
    assert not mg.cache_crowding({"available_mb": 50_000, "free_mb": 30_000, "cache_mb": 45_000}, LIMITS)
    assert mg.poll_s(crowded, LIMITS, 0, 0.5) == mg.FAST_POLL_S
    assert mg.poll_s({"available_mb": 90_000}, LIMITS, 2_000, 0.5) == mg.FAST_POLL_S  # falling fast
    assert mg.poll_s({"available_mb": 90_000, "some_pct": 0.0}, LIMITS, 0, 0.5) == 0.5


def test_parsers():
    meminfo = "MemTotal:       127500000 kB\nMemFree:  2048000 kB\nMemAvailable:   40960000 kB\nCached: 30720000 kB\nShmem: 1024 kB\n"
    assert mg.parse_meminfo(meminfo)["MemAvailable"] == 40_000
    psi = "some avg10=12.50 avg60=3.00 avg300=1.00 total=1\nfull avg10=4.25 avg60=1.00 avg300=0.00 total=1\n"
    assert mg.parse_psi(psi, "some") == 12.5 and mg.parse_psi(psi, "full") == 4.25
    vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 65536.\nFile-backed pages: 131072.\n"
          "Pages purgeable: 0.\nPages inactive: 999999.\n")
    assert mg.parse_vm_stat(vm, 262_144) == 3072


def test_only_the_persons_matching_servers_are_picked_never_the_studios():
    rows = [{"pid": 10, "mb": 50_000, "command": f"python {ROOT}/spark/spark_generate.py"},          # the Studio's own
            {"pid": 11, "mb": 52_000, "command": "/opt/fp4/worker --listen 127.0.0.1"},          # not a known server
            {"pid": 12, "mb": 22_000, "command": "python3 -m vllm.entrypoints.openai.api_server"},
            {"pid": 13, "mb": 9_000, "command": "ollama serve"},
            {"pid": 14, "mb": 60_000, "command": "/home/spark/ComfyUI/main.py --port 8188"}]
    picked = mg.pick_foreign(rows, mg.STOP_PATTERNS, mg.studio_markers(), os.getpid())
    assert picked["pid"] == 14  # a ComfyUI started by hand, the largest matching
    assert mg.pick_foreign(rows[:2], mg.STOP_PATTERNS, mg.studio_markers(), os.getpid()) is None


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_install_and_uninstall_plans(platform, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "ImaginationStudio"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    (tmp_path / ".claude").mkdir()
    python = "C:\\Studio\\ui-venv\\Scripts\\python.exe" if platform == "win32" else "/usr/bin/python3"
    steps = mg.plan("install", platform, tmp_path, python, "http://127.0.0.1:7870", False)
    writes = {Path(s[1]): s[2] for s in steps if s[0] == "write"}
    runs = [s[1] for s in steps if s[0] == "run"]
    assert tmp_path / "ImaginationStudio" / "STUDIO-AGENT.md" in writes
    assert tmp_path / ".claude" / "skills" / "imagination-studio" / "SKILL.md" in writes
    manifest = json.loads(next(text for path, text in writes.items() if path.name == "memguard-installed.json"))
    assert manifest["stop_foreign"] is False and len(manifest["files"]) == len(writes) - 1
    if platform == "linux":
        unit = writes[tmp_path / ".config/systemd/user/imagination-studio-memguard.service"]
        assert "ExecStart=/usr/bin/python3 " in unit and " run --url http://127.0.0.1:7870\n" in unit and "--stop-foreign" not in unit
        assert ["systemctl", "--user", "enable", "--now", mg.UNIT] in runs
    elif platform == "darwin":
        plist = plistlib.loads(writes[tmp_path / "Library/LaunchAgents/com.imagination-studio.memguard.plist"].encode())
        assert plist["Label"] == mg.MAC_LABEL and plist["KeepAlive"] is True and plist["ProgramArguments"][2] == "run"
        assert any(r[:2] == ["launchctl", "bootstrap"] for r in runs)
    else:
        cmd = next(text for path, text in writes.items() if path.name == "memguard.cmd")
        assert "pythonw.exe" in cmd and "memguard.py" in cmd and " run " in cmd
        create = next(r for r in runs if "/Create" in r)
        assert "ONLOGON" in create and mg.WIN_TASK in create and len(create[-1]) <= 261
    undo = mg.plan("uninstall", platform, tmp_path, python, "http://127.0.0.1:7870", False)
    removed = {Path(s[1]) for s in undo if s[0] == "remove"}
    assert set(writes) <= removed  # uninstall takes back everything install wrote
    assert "--stop-foreign" in json.dumps(mg.plan("install", platform, tmp_path, python, "u", True), default=str)


def test_a_dry_run_changes_nothing_and_a_real_one_writes_and_removes(tmp_path):
    target = tmp_path / "a" / "unit.service"
    said = []
    assert mg.apply([("write", target, "x"), ("run", ["no-such-command-memguard"], False)], True, said.append) == 0
    assert not target.exists() and any("no-such-command" in s for s in said)
    assert mg.apply([("write", target, "x"), ("run", [sys.executable, "-c", "pass"], False)], False, said.append) == 0
    assert target.read_text() == "x"
    assert mg.apply([("run", [sys.executable, "-c", "raise SystemExit(3)"], True), ("remove", target)], False, said.append) == 0
    assert not target.exists()
    assert mg.apply([("run", [sys.executable, "-c", "raise SystemExit(3)"], False)], False, said.append) == 1


def test_unit_quoting_keeps_a_path_with_spaces_whole():
    text = mg.unit_text(["/usr/bin/python3", "/srv/My Studio/scripts/memguard.py", "run"])
    assert 'ExecStart=/usr/bin/python3 "/srv/My Studio/scripts/memguard.py" run' in text


def test_one_pass_of_the_watchdog_asks_the_studio_to_release(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(mg, "state_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(mg, "reading", lambda: {"available_mb": 15_000, "total_mb": 128_000, "commit_left_mb": None,
                                                 "pressure": "normal", "stall_pct": 0.0, "some_pct": 0.0, "free_mb": None, "cache_mb": None})
    asked = []
    monkeypatch.setattr(mg.Studio, "release", lambda self, why: asked.append(why) or [{"label": "NVIDIA Nemotron 3.5 Bolt"}])
    assert mg.main(["run", "--once", "--floor-mb", "12800"]) == 0
    assert asked and "the machine guard" in asked[0]
    log = (tmp_path / "memguard.log").read_text()
    assert "release:" in log and "unloaded NVIDIA Nemotron 3.5 Bolt" in log


def test_the_setups_install_it_announced_and_reversible():
    for name in ("oneshot-setup.sh", "oneshot-setup-cuda.sh"):
        text = (ROOT / name).read_text()
        assert "--no-memguard) MEMGUARD=0" in text and "--memguard-stop-foreign) MEMGUARD_FOREIGN=1" in text
        assert 'scripts/memguard.py" install' in text and "STUDIO-AGENT.md" in text
        assert "never stops your own programs" in text
    assert "--no-memguard|--memguard-stop-foreign" in (ROOT / "oneshot-setup.sh").read_text()  # passed to the Linux install
    ps1 = (ROOT / "oneshot-setup-rtx-spark-n1x.ps1").read_text()
    assert "[switch]$NoMemoryGuard" in ps1 and "[switch]$MemoryGuardStopForeign" in ps1 and "memguard.py" in ps1
    assert (ROOT / "oneshot-setup-spark.sh").read_text().count("oneshot-setup-cuda.sh")  # the Spark entry runs the CUDA install


def test_agents_are_told_that_every_render_goes_through_the_studio():
    note = (ROOT / "STUDIO-AGENT.md").read_text()
    head = note.split("---")[1]
    assert "name: imagination-studio" in head and "description:" in head  # a Claude Code skill as it is
    for said in ("POST /api/jobs", "/api/memory", "/api/memory/release", "memory_status", "release_memory", "headroom_mb",
                 "Do not start an LLM server", "yours to stop"):
        assert said in note, said
    for name in ("AGENTS.md", "README.md", "mcp/README.md", "docs/memory-guard.md"):
        assert "STUDIO-AGENT.md" in (ROOT / name).read_text(), name


def test_the_mcp_tools_reach_the_memory_routes(monkeypatch):
    spec_ = importlib.util.spec_from_file_location("studio_mcp_memory", ROOT / "mcp/studio-mcp.py")
    mcp = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mcp)
    sent = []

    def call(method, path, body=None, raw=None, kind="application/json"):
        sent.append((method, path, body))
        return {"available_mb": 40_000, "headroom_mb": 27_200, "released": [], "modules": [], "others": []}
    monkeypatch.setattr(mcp, "call", call)
    reply = mcp.answer({"method": "tools/call", "params": {"name": "memory_status", "arguments": {}}})
    assert json.loads(reply["content"][0]["text"])["headroom_mb"] == 27_200
    mcp.answer({"method": "tools/call", "params": {"name": "release_memory", "arguments": {"modules": ["bolt"]}}})
    assert sent[-1] == ("POST", "/api/memory/release", {"why": "an agent asked (release_memory)", "modules": ["bolt"]})
