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
globalThis.setTimeout = () => 0;
globalThis.S = {feature_checks:{supported:true, url:"/api/feature-checks", features:{finish:"Finish",foley:"MMAudio foley"}},
  capabilities: {finish: {available: true}, foley: {available: false}}};
globalThis.go = tab => { globalThis.destination = tab; };
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const good = featureBadge("finish"), blocked = featureBadge("foley");
const initial = featureChecksPanel();
blocked.attrs.onclick();
const out = {good: good.kids.join(""), blocked: blocked.kids.join(""), className: blocked.attrs.class,
  disabled: !!blocked.attrs.disabled, destination, focus: WFC.focus,
  sectionTag:initial.tag, initiallyOpen:initial.attrs.open, expandedByBadge:featureChecksPanel().attrs.open};
S.platform = "mac"; S.ui_only = false; out.otherPlatform = featureBadge("finish").kids.join("");
S.feature_checks.supported = false; out.unsupported = featureBadge("finish");
out.unsupportedSection = featureChecksSection();
delete S.feature_checks; out.oldServerSection = featureChecksSection();
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
    assert result["sectionTag"] == "details" and not result["initiallyOpen"] and result["expandedByBadge"]
    assert result["otherPlatform"] == "Qualified" and result["unsupported"] is None
    assert result["unsupportedSection"] is None
    assert result["oldServerSection"]["tag"] == "details"


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
out.unified = text(wfcAdmission({qualified:true, check_available:true, requirements:[{pool:"unified",required_mb:16000,reserve_mb:2000}]},
  {pools:{unified:{available_mb:32000}}}));
out.unifiedBusy = text(wfcAdmission({qualified:true, check_available:true, requirements:[{pool:"unified",required_mb:16000,reserve_mb:2000}]},
  {pools:{unified:{available_mb:17000}}}));
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
    assert "headroom is available now" in result["unified"] and "currently busy" in result["unifiedBusy"]


def test_feature_groups_follow_the_actual_room_order_and_preserve_feature_ids():
    need_node()
    script = r"""
const vm = require("node:vm"), fs = require("node:fs");
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
console.log(JSON.stringify(featureCheckGroups([{id:"finish",group:"edit"},{id:"foley",group:"sound"},{id:"hlg",group:"edit"}],
  [{id:"video",name:"Video"},{id:"image",name:"Image"},{id:"sound",name:"Sound"},{id:"edit",name:"Edit"}])));
"""
    done = subprocess.run(["node", "-e", script, str(ROOT / "app" / "static" / "machine.js")],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    groups = json.loads(done.stdout)
    assert [g["title"] for g in groups] == ["Sound", "Edit"]
    assert [[c["id"] for c in g["cards"]] for g in groups] == [["foley"], ["finish", "hlg"]]


def test_storyboard_badges_follow_cards_on_image_and_home_not_just_apps():
    need_node()
    script = r"""
const vm = require("node:vm"), fs = require("node:fs");
globalThis.el = (tag, attrs, ...kids) => ({tag, attrs, kids});
globalThis.icon = () => null;
globalThis.mark = () => null;
globalThis.go = tab => {globalThis.destination = tab;};
globalThis.ROOMS = [{id:"image",name:"Image"}];
globalThis.S = {platform:"windows", feature_checks:{supported:true,features:{scout:"SD-Turbo storyboard drafts"}},
  capabilities:{scout:{available:true}}};
globalThis.capWhy = kind => S.capabilities[kind]?.available === false ? "Not qualified" : "";
for (const file of process.argv.slice(1)) vm.runInThisContext(fs.readFileSync(file,"utf8"));
const wrappers = [];
const walk = node => {if(Array.isArray(node)) node.forEach(walk); else if(node && typeof node==="object") {
  if(node.attrs?.["data-feature-card"]==="scout") wrappers.push(node);
  (node.kids || []).forEach(walk);
}};
walk(engineLanding("image"));
const positive = wrappers.map(node => node.kids[1].attrs["aria-label"]);
const home = pcard(STARTS().find(card => card.id==="still"));
const video = pcard(STARTS().find(card => card.id==="scout"));
S.capabilities.scout.available=false;
wrappers.length=0; walk(engineLanding("image"));
const blocked = wrappers[0];
blocked.kids[1].attrs.onclick();
const output = {positive, home:home.kids[1].attrs["aria-label"], videoWrapped:video.attrs.class==="feature-app",
  disabled:blocked.kids[0].attrs.disabled, badgeDisabled:!!blocked.kids[1].attrs.disabled,
  destination, focus:WFC.focus};
S.feature_checks.supported=false;
output.unsupportedWrapped=pcard(STARTS().find(card => card.id==="still")).attrs.class==="feature-app";
console.log(JSON.stringify(output));
"""
    files = [str(ROOT / "app" / "static" / name) for name in ("machine.js", "engines.js", "home.js")]
    done = subprocess.run(["node", "-e", script, *files], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["positive"] == ["Qualified: SD-Turbo storyboard drafts"] * 2
    assert result["home"] == "Qualified: SD-Turbo storyboard drafts"
    assert not result["videoWrapped"] and not result["unsupportedWrapped"]
    assert result["disabled"] and not result["badgeDisabled"]
    assert result["destination"] == "Machine" and result["focus"] == "scout"
