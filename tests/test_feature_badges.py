"""Qualification badges use saved state and route to the relevant card without starting a check."""
import json
import subprocess
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parents[1]


def test_positive_and_negative_badges_route_to_the_feature_card():
    need_node()
    script = r"""
const vm = require("node:vm"), fs = require("node:fs");
globalThis.el = (tag, attrs, ...kids) => ({tag, attrs, kids});
globalThis.S = {ui_only: true, capabilities: {finish: {available: true}, foley: {available: false}}};
globalThis.go = tab => { globalThis.destination = tab; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const good = featureBadge("finish"), blocked = featureBadge("foley");
blocked.attrs.onclick();
const out = {good: good.kids.join(""), blocked: blocked.kids.join(""), className: blocked.attrs.class,
  disabled: !!blocked.attrs.disabled, destination, focus: WFC.focus};
S.ui_only = false; out.offWindows = featureBadge("finish");
console.log(JSON.stringify(out));
"""
    done = subprocess.run(["node", "-e", script, str(ROOT / "app" / "static" / "machine.js")],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["good"] == "Qualified"
    assert result["blocked"] == "Not qualified →" and not result["disabled"]
    assert "warn" in result["className"]
    assert result["destination"] == "Machine" and result["focus"] == "foley"
    assert result["offWindows"] is None


def test_qualified_card_separates_historical_refusal_from_current_memory():
    need_node()
    script = r"""
const vm = require("node:vm"), fs = require("node:fs");
globalThis.el = (tag, attrs, ...kids) => ({tag, attrs, kids});
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const card = {id:"finish", qualified:true, check_available:true, memory:{required_headroom_mb:4096},
  attempt:{error:JSON.stringify({ok:false, feature:"finish", error:"Not enough memory on the previous attempt."}), finished:"2026-11-04T20:00:00Z"},
  job:{status:"done", finished:1793826000}};
const history = wfcHistory(card), text = n => !n ? "" : typeof n === "string" ? n : (n.kids || []).map(text).join(" ");
const available = wfcAdmission(card, {host_free_mb:8000, commit_free_mb:20000, low_memory:false});
const busy = wfcAdmission(card, {host_free_mb:2000, commit_free_mb:20000, low_memory:false});
const out = {history, text:text(history), available:text(available), busy:text(busy), qualified:card.qualified};
card.job = {status:"failed", error:"A newer check failed.", finished:2000000000};
out.latest = wfcAttempt(card).message;
globalThis.S = null; out.loading = text(viewMachine());
console.log(JSON.stringify(out));
"""
    done = subprocess.run(["node", "-e", script, str(ROOT / "app" / "static" / "machine.js")],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["history"]["tag"] == "details" and not result["history"]["attrs"].get("open")
    assert "Previous check attempt" in result["text"] and "feature remains qualified" in result["text"]
    assert '"ok"' not in result["text"] and "Not enough memory on the previous attempt." in result["text"]
    assert "headroom is available now" in result["available"]
    assert "currently busy" in result["busy"] and "qualification is unchanged" in result["busy"]
    assert result["qualified"] and result["latest"] == "A newer check failed."
    assert "Loading machine status" in result["loading"]
