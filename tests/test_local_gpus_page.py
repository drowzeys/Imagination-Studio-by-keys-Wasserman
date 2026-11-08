"""Sparks' local GPU cards and saved placement, exercised through the page's real handlers."""
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent

HARNESS = r"""
const vm = require("vm"), fs = require("fs");
globalThis.S = { platform: "spark", platform_label: "CUDA · NVIDIA synthetic GPU" };
globalThis.tab = "Sparks"; globalThis.render = () => {}; globalThis.toasts = [];
globalThis.history = { replaceState: (state, title, url) => { globalThis.locationHash = url; } };
globalThis.toast = message => toasts.push(message); globalThis.mmss = seconds => seconds + " s";
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.document = { querySelector: () => null, activeElement: { matches: () => false } };
globalThis.confirmations = []; globalThis.agree = true;
globalThis.confirm = message => { confirmations.push(message); return agree; };
globalThis.calls = []; globalThis.answers = {};
globalThis.api = async (path, body, method) => {
  if (path === "/api/sparks/plan") return { supported: true, count: "one worker", says: "Independent clips", without: [] };
  if (path === "/api/sparks/takes") return {};
  calls.push([path, body === undefined ? null : JSON.parse(JSON.stringify(body)), method || (body === undefined ? "GET" : "POST")]);
  const answer = answers[path]; if (answer instanceof Error) throw answer;
  if (typeof answer === "function") return answer(body);
  return answer || SPK.data;
};
globalThis.el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids: kids.flat().filter(k => k != null && k !== false), replaceChildren(...kids) { this.kids = kids.flat(); } });
globalThis.put = (node, ...kids) => node.kids.push(...kids.flat());
globalThis.field = (label, node) => el("div", {}, label, node); globalThis.bind = (object, key, node) => node;
globalThis.text = node => node == null || node === false ? "" : typeof node !== "object" ? String(node) : Array.isArray(node) ? node.map(text).join(" ") : text(node.kids);
globalThis.all = (node, test, out = []) => { if (node && typeof node === "object") { if (!Array.isArray(node) && test(node)) out.push(node); (Array.isArray(node) ? node : node.kids || []).forEach(k => all(k, test, out)); } return out; };
globalThis.button = (node, label) => all(node, n => n.tag === "button" && text(n) === label)[0];
globalThis.cards = node => all(node, n => n.attrs && n.attrs["data-gpu"]);
globalThis.select = (node, label) => all(node, n => n.tag === "select" && n.attrs["aria-label"] === label)[0];
globalThis.fixture = count => {
  const devices = Array.from({ length: count }, (_, index) => ({
    id: "local:GPU-00000000-0000-0000-0000-" + String(index + 1).padStart(12, "0"), index,
    name: "Synthetic GPU " + index, total_mb: 24576, used_mb: 1024, free_mb: 23552,
    util_pct: 0, temp_c: 39, power_w: 35, display_active: false, head: index === 0, worker: null, reason: ""
  }));
  return { supported: true, limit: 5, available: 1, listed: 1, default: 1, placement: null,
    head: "spark", folder: "~/Studio", this: { name: "Head", kind: "GPU", state: "ready", version: "synthetic", can: {} }, workers: [],
    local_gpus: { supported: true, limit: 4, head_id: devices[0] && devices[0].id, managed: 1, devices } };
};
globalThis.worker = (gpu, extra = {}) => ({ address: gpu.id, gpu_uuid: gpu.id.slice(6), kind: "local", name: "Render worker",
  enabled: true, owned: true, state: "ready", says: "idle", ...extra });
vm.runInThisContext(fs.readFileSync(process.argv[3], "utf8"));
vm.runInThisContext(`(async () => { const out = {}; ${fs.readFileSync(process.argv[2], "utf8")}; console.log(JSON.stringify(out)); })().catch(error => { console.error(error); process.exit(1); });`);
"""


def run(code):
    need_node()
    with tempfile.TemporaryDirectory() as scratch:
        harness = Path(scratch) / "harness.js"
        script = Path(scratch) / "test.js"
        harness.write_text(HARNESS)
        script.write_text(code)
        done = subprocess.run(["node", str(harness), str(script), str(ROOT / "app/static/sparks.js")],
                              capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("count", [2, 3, 4])
def test_multiple_cards_show_independent_memory_and_head_controls(count):
    out = run(f"""SPK.data = fixture({count}); const page = viewSparks();
      out.count = cards(page).length; out.page = text(page); out.head = text(cards(page)[0]);
      out.headStop = !!button(cards(page)[0], "Stop"); out.headStart = !!button(cards(page)[0], "Start");
      out.starts = cards(page).slice(1).map(card => !!button(card, "Start") && !button(card, "Start").attrs.disabled);
      out.models = text(page).includes("Models:");""")
    assert out["count"] == count
    assert "VRAM is not pooled" in out["page"] and "Host RAM is shared" in out["page"]
    assert "24,576 MiB" in out["head"] and "23,552 MiB" in out["head"]
    assert "0%" in out["head"] and "39 °C" in out["head"] and "35 W" in out["head"]
    assert not out["headStop"] and not out["headStart"] and not out["models"]
    assert all(out["starts"])
    assert "GPU-00000000" not in out["page"]


def test_over_limit_inventory_is_visible_and_a_different_subset_can_be_selected():
    out = run("""SPK.data = fixture(6); const d = SPK.data;
      for (const gpu of d.local_gpus.devices.slice(1, 4)) { gpu.worker = worker(gpu); d.workers.push(gpu.worker); }
      d.local_gpus.managed = 4;
      let page = viewSparks(); out.count = cards(page).length; out.last = text(cards(page)[5]);
      out.blocked = button(cards(page)[5], "Start").attrs.disabled;
      d.local_gpus.managed = 3; d.workers.pop(); d.local_gpus.devices[3].worker = null;
      page = viewSparks(); out.selectable = !button(cards(page)[5], "Start").attrs.disabled;""")
    assert out["count"] == 6 and "Synthetic GPU 5" in out["last"]
    assert "4-GPU limit includes the head" in out["last"] and out["blocked"]
    assert out["selectable"]


def test_absent_telemetry_is_unavailable_not_zero_and_job_state_is_reported():
    out = run("""SPK.data = fixture(2); const gpu = SPK.data.local_gpus.devices[1];
      for (const key of ["used_mb", "free_mb", "total_mb", "util_pct", "temp_c", "power_w", "display_active"]) gpu[key] = null;
      gpu.worker = worker(gpu, { job: "job-synthetic" }); SPK.data.workers = [gpu.worker];
      out.card = text(cards(viewSparks())[1]);""")
    assert out["card"].count("unavailable") == 7
    assert "0%" not in out["card"] and "null" not in out["card"] and "NaN" not in out["card"]
    assert "Models:" not in out["card"] and "Job: job-synthetic" in out["card"]


def test_local_actions_use_stable_gpu_identity_and_only_owned_workers_can_stop():
    out = run("""SPK.data = fixture(3); const devices = SPK.data.local_gpus.devices;
      devices[1].worker = worker(devices[1]); devices[2].worker = worker(devices[2], { owned: false, state: "down", why: "Owned by another process" });
      devices[2].reason = "Owned by another process"; SPK.data.workers = devices.slice(1).map(g => g.worker);
      const rows = cards(viewSparks()); out.unownedStop = !!button(rows[2], "Stop"); out.conflictDisabled = button(rows[2], "Start").attrs.disabled;
      await button(rows[0], "Check").attrs.onclick(); await button(rows[1], "Stop").attrs.onclick();
      devices[1].worker = null; SPK.data.workers = [devices[2].worker];
      await button(cards(viewSparks())[1], "Start").attrs.onclick(); out.calls = calls;
      out.ids = devices.map(g => g.id);""")
    assert not out["unownedStop"] and out["conflictDisabled"]
    assert out["calls"] == [
        ["/api/sparks/local", {"gpu": out["ids"][0], "action": "check"}, "POST"],
        ["/api/sparks/local", {"gpu": out["ids"][1], "action": "stop"}, "POST"],
        ["/api/sparks/local", {"gpu": out["ids"][1], "action": "start"}, "POST"],
    ]


def test_display_start_requires_opt_in_and_busy_worker_cannot_stop():
    out = run("""SPK.data = fixture(2); const gpu = SPK.data.local_gpus.devices[1]; gpu.display_active = true;
      agree = false; await button(cards(viewSparks())[1], "Start").attrs.onclick(); out.refusedCalls = calls.length;
      agree = true; await button(cards(viewSparks())[1], "Start").attrs.onclick(); out.accepted = calls.pop(); out.confirm = confirmations[0];
      gpu.worker = worker(gpu, { working: "job-synthetic" }); SPK.data.workers = [gpu.worker];
      out.busy = button(cards(viewSparks())[1], "Stop").attrs.disabled;""")
    assert out["refusedCalls"] == 0 and "desktop unresponsive" in out["confirm"]
    assert out["accepted"][1]["allow_display"] is True and out["busy"]


def test_video_placement_persists_address_and_require_prefer_not_display_label():
    out = run("""SPK.data = fixture(2); const d = SPK.data, gpu = d.local_gpus.devices[1]; gpu.worker = worker(gpu, { name: "Same label" });
      d.workers = [gpu.worker, { kind: "ssh", address: "worker@example.invalid", name: "Same label", state: "ready", enabled: true }];
      answers["/api/sparks"] = body => { d.placement = body.placement; return d; };
      let page = viewSparks(); out.options = select(page, "Default video worker").kids.map(o => [o.attrs.value, text(o)]);
      out.autoDisabled = select(page, "Video placement policy").attrs.disabled;
      await select(page, "Default video worker").attrs.onchange({ target: { value: gpu.id } }); out.required = { ...d.placement };
      page = viewSparks(); out.selected = select(page, "Default video worker").kids.filter(o => o.attrs.selected).map(o => o.attrs.value);
      await select(page, "Video placement policy").attrs.onchange({ target: { value: "prefer" } }); out.preferred = { ...d.placement };
      await select(viewSparks(), "Default video worker").attrs.onchange({ target: { value: "head" } }); out.head = { ...d.placement };
      await select(viewSparks(), "Default video worker").attrs.onchange({ target: { value: "" } }); out.automatic = d.placement;
      out.calls = calls; out.policyText = text(page); out.id = gpu.id;""")
    assert [option[0] for option in out["options"]] == ["", "head", out["id"], "worker@example.invalid"]
    assert out["options"][2][1] == "GPU 1 · Synthetic GPU 1"
    assert out["id"] not in out["policyText"]
    assert out["autoDisabled"] and out["selected"] == [out["id"]]
    assert out["required"] == {"worker": out["id"], "policy": "require"}
    assert out["preferred"] == {"worker": out["id"], "policy": "prefer"}
    assert out["head"] == {"worker": "head", "policy": "prefer"} and out["automatic"] is None
    assert all(call[0] == "/api/sparks" and list(call[1]) == ["placement"] for call in out["calls"])


def test_remote_actions_and_mac_behavior_remain_and_local_workers_have_no_ssh_actions():
    out = run("""SPK.data = fixture(2); const d = SPK.data, gpu = d.local_gpus.devices[1]; gpu.worker = worker(gpu);
      const remote = { address: "worker@example.invalid", name: "Remote Spark", kind: "ssh", state: "ready", enabled: true, says: "idle" };
      d.workers = [gpu.worker, remote]; let page = viewSparks();
      out.localUpdate = !!button(cards(page)[1], "Update and start");
      out.updates = all(page, n => n.tag === "button" && text(n) === "Update and start").length;
      await button(page, "Update and start").attrs.onclick(); out.update = calls.pop();
      delete d.local_gpus; d.workers = [remote]; d.head = "mac"; d.this.kind = "Mac"; S.platform = "mac";
      page = viewSparks(); out.mac = text(page); out.cards = cards(page).length;
      out.via = all(page, n => n.tag === "input").some(n => n.attrs.placeholder === "user@the-spark-that-leads");""")
    assert not out["localUpdate"] and out["updates"] == 1
    assert out["update"] == ["/api/sparks/workers/worker%40example.invalid/update", {}, "POST"]
    assert out["cards"] == 0 and out["via"] and "this Mac" in out["mac"] and "Local GPUs" not in out["mac"]


def test_inventory_changes_trigger_redraw_and_plan_cache_invalidation():
    out = run("""SPK.data = fixture(2); const before = spkSig(SPK.data); SPK.data.local_gpus.devices[1].temp_c = 70;
      out.changed = spkSig(SPK.data) !== before;
      const inventory = SPK.data; SPK.plans = { old: {} }; answers["/api/sparks"] = { ...inventory, placement: { worker: "head", policy: "require" } };
      await spkLoad(false); out.plans = Object.keys(SPK.plans); out.placement = SPK.data.placement;""")
    assert out["changed"] and out["plans"] == []
    assert out["placement"] == {"worker": "head", "policy": "require"}


def test_stopped_workers_can_be_removed_and_use_toggle_preserves_gpu_identity():
    out = run("""SPK.data = fixture(2); const gpu = SPK.data.local_gpus.devices[1];
      gpu.worker = worker(gpu, { owned: false, state: "stopped", name: "Friendly label" }); SPK.data.workers = [gpu.worker];
      const row = cards(viewSparks())[1], use = all(row, n => n.tag === "input" && n.attrs.type === "checkbox")[0];
      await use.attrs.onchange({ target: { checked: false } });
      await button(row, "Remove").attrs.onclick(); out.calls = calls; out.id = gpu.id;""")
    path = "/api/sparks/workers/" + out["id"].replace(":", "%3A")
    assert out["calls"] == [[path, {"enabled": False}, "POST"], [path, None, "DELETE"]]


def test_failed_placement_save_keeps_overview_as_the_source_of_truth():
    out = run("""SPK.data = fixture(2); const gpu = SPK.data.local_gpus.devices[1];
      gpu.worker = worker(gpu); SPK.data.workers = [gpu.worker]; answers["/api/sparks"] = new Error("Placement was not saved");
      await select(viewSparks(), "Default video worker").attrs.onchange({ target: { value: "head" } });
      out.placement = SPK.data.placement; out.toast = toasts.pop();
      out.selected = select(viewSparks(), "Default video worker").kids.filter(o => o.attrs.selected).map(o => o.attrs.value);""")
    assert out["placement"] is None and out["selected"] == [""]
    assert out["toast"] == "Placement was not saved"


def test_missing_configured_gpu_stays_manageable_without_driver_telemetry():
    out = run("""SPK.data = fixture(2); const d = SPK.data, gpu = d.local_gpus.devices[1];
      gpu.worker = worker(gpu, { owned: false, state: "down" }); d.workers = [gpu.worker];
      gpu.index = null; gpu.reason = "This GPU UUID is not present; reconnect the card before starting it.";
      for (const key of ["used_mb", "free_mb", "total_mb", "util_pct", "temp_c", "power_w", "display_active"]) gpu[key] = null;
      d.local_gpus.devices = [gpu]; d.local_gpus.supported = false;
      const row = cards(viewSparks())[0]; out.card = text(row); out.startDisabled = button(row, "Start").attrs.disabled;
      out.stop = !!button(row, "Stop"); out.remove = !!button(row, "Remove");
      out.useDisabled = all(row, n => n.tag === "input")[0].attrs.disabled;""")
    assert "Missing GPU" in out["card"] and "GPU null" not in out["card"]
    assert "reconnect the card" in out["card"] and out["startDisabled"]
    assert not out["stop"] and out["remove"] and not out["useDisabled"]


def test_running_disabled_worker_still_occupies_capacity_but_can_be_reenabled():
    out = run("""SPK.data = fixture(5); const d = SPK.data;
      for (const gpu of d.local_gpus.devices.slice(1, 4)) { gpu.worker = worker(gpu); d.workers.push(gpu.worker); }
      d.local_gpus.devices[1].worker.enabled = false; d.local_gpus.managed = 4;
      const rows = cards(viewSparks()), use = all(rows[1], n => n.tag === "input")[0];
      out.canReenable = !use.attrs.disabled; out.fifthBlocked = button(rows[4], "Start").attrs.disabled;
      out.stoppable = !!button(rows[1], "Stop"); out.removable = !!button(rows[1], "Remove");
      await use.attrs.onchange({ target: { checked: true } }); out.changed = calls.pop();""")
    assert out["canReenable"] and out["fifthBlocked"] and out["stoppable"] and not out["removable"]
    assert out["changed"][1] == {"enabled": True}


def test_worker_chooser_opens_fleet_and_keeps_combined_and_remote_limits():
    out = run("""SPK.data = fixture(4); const d = SPK.data;
      for (const gpu of d.local_gpus.devices.slice(1)) { gpu.worker = worker(gpu); d.workers.push(gpu.worker); }
      d.limit = 8; d.available = 4; d.listed = 4; d.local_gpus.managed = 4; SPK.n = 4;
      const queue = el("div"); queue.style = {}; document.querySelector = selector => selector === "#sparks" ? queue : null;
      const pick = sparksPick(); spkQueue();
      out.choices = all(pick, n => n.tag === "button" && /^[1-8]$/.test(text(n))).length;
      tab = "Home"; button(pick, "Workers…").attrs.onclick(); out.destination = [tab, locationHash];
      await button(pick, "8").attrs.onclick(); out.requested = calls.pop(); out.selected = SPK.n;
      out.count = spkCount(d, 4);
      delete d.local_gpus; d.workers = [{ kind: "ssh", address: "worker@example.invalid", name: "Remote", enabled: true, state: "ready" }];
      d.available = 2; d.limit = 5; SPK.n = 2; spkQueue();
      const remote = sparksPick(); out.remoteChoices = all(remote, n => n.tag === "button" && /^[1-8]$/.test(text(n))).length;
      tab = "Home"; button(remote, "Sparks…").attrs.onclick(); out.remoteDestination = [tab, locationHash];
      out.remoteCount = spkCount(d, 2);
      S.platform = "mac"; d.this.kind = "Mac"; out.macCount = spkCount(d, 2);""")
    assert out["choices"] == 8 and out["remoteChoices"] == 5
    assert out["destination"] == ["Sparks", "#Sparks"] and out["remoteDestination"] == ["Sparks", "#Sparks"]
    assert out["selected"] == 8 and out["requested"] == ["/api/sparks", {"sparks": 8}, "POST"]
    assert out["count"] == "this GPU and 3 workers"
    assert out["remoteCount"] == "this GPU and 1 Spark" and out["macCount"] == "2 of 2 available Sparks"


def test_fleet_shrink_reconciles_open_chooser_queue_and_next_plan():
    out = run("""const d = fixture(4), remote = { kind: "ssh", address: "worker@example.invalid", name: "Remote", enabled: true, state: "ready" };
      for (const gpu of d.local_gpus.devices.slice(1)) { gpu.worker = worker(gpu); d.workers.push(gpu.worker); }
      d.workers.push(remote); d.limit = 8; d.default = 8; d.available = 5; d.listed = 5;
      const saved = []; localStorage.getItem = () => "8"; localStorage.setItem = (key, value) => saved.push(value);
      const queue = el("div"); queue.style = {}; document.querySelector = selector => selector === "#sparks" ? queue : null;
      answers["/api/sparks"] = d; await spkLoad(false); out.initial = SPK.n;
      const smaller = { ...d, limit: 5, default: 5, workers: [remote], available: 2, listed: 2 };
      answers["/api/sparks"] = smaller; await spkLoad(false);
      const pick = sparksPick(); out.afterPoll = SPK.n; out.on = button(pick, "5").attrs.class.includes(" on");
      out.eight = !!button(pick, "8"); out.queue = text(queue); out.saved = saved;
      const original = api; globalThis.api = async (path, body, method) => { if (path === "/api/sparks/plan") out.nextPlan = body.sparks; return original(path, body, method); };
      await spkPlan(el("div"), { kind: "video", clips: 2 });
      SPK.data = d; SPK.n = 8; answers["/api/sparks/workers/" + encodeURIComponent(d.workers[0].address)] = smaller;
      await spkDo("/api/sparks/workers/" + encodeURIComponent(d.workers[0].address), undefined, "DELETE");
      out.afterRemove = SPK.n; out.removeOn = button(sparksPick(), "5").attrs.class.includes(" on");""")
    assert out["initial"] == 8 and out["afterPoll"] == 5 and out["afterRemove"] == 5
    assert out["on"] and out["removeOn"] and not out["eight"]
    assert "2 of 5 available" in out["queue"] and out["saved"][-1] == "5"
    assert out["nextPlan"] == 5


def test_labels_and_live_machine_status_coexist_with_local_cards_and_placement():
    out = run("""SPK.data = fixture(2); const d = SPK.data, gpu = d.local_gpus.devices[1];
      d.this.label = "Main renderer"; d.this.status = { at: 1, gpu: { util_pct: 17, temp_c: 41 } };
      gpu.worker = worker(gpu, { label: "Side renderer", status: { at: 1, gpu: { util_pct: 23, temp_c: 45 } } });
      const remote = { address: "worker@example.invalid", name: "Remote", label: "Remote renderer", kind: "ssh", state: "ready", enabled: true };
      d.workers = [gpu.worker, remote]; d.names = { Head: d.this.label, Remote: remote.label };
      const page = viewSparks(), panel = all(page, n => n.attrs && "data-spk-machines" in n.attrs)[0];
      out.panel = text(panel); out.cards = cards(page).length; out.page = text(page);
      out.options = select(page, "Default video worker").kids.map(o => [o.attrs.value, text(o)]);
      out.label = spkLabel("Remote"); SPK.takes = { "clip.mp4": { spark: "Remote" } }; out.take = spkTake("clip.mp4");
      const machinePanel = spkMachinesPanel(); out.machinePanel = text(machinePanel); out.id = gpu.id;""")
    assert out["cards"] == 2
    assert all(label in out["panel"] for label in ["Main renderer", "Side renderer", "Remote renderer"])
    assert "17%" in out["panel"] and "23%" in out["panel"] and "45 °C" in out["panel"]
    assert out["id"] not in out["page"]
    assert out["options"][1] == ["head", "Head · Main renderer"]
    assert out["options"][-1] == ["worker@example.invalid", "Spark · Remote renderer · worker@example.invalid"]
    assert out["label"] == "Remote renderer" and out["take"] == "on Remote renderer"
    assert out["panel"] in out["machinePanel"]


def test_status_only_poll_refreshes_each_machine_panel_without_resetting_plans():
    out = run("""SPK.data = fixture(2); const d = SPK.data, gpu = d.local_gpus.devices[1];
      gpu.worker = worker(gpu, { label: "Side renderer", status: { at: 1, gpu: { util_pct: 10 } } }); d.workers = [gpu.worker];
      d.this.label = "Main renderer"; d.this.status = { at: 1, gpu: { util_pct: 20 } };
      const machine = spkMachinesPanel(), sparks = viewSparks();
      const panels = [machine, sparks].map(page => all(page, n => n.attrs && "data-spk-machines" in n.attrs)[0]);
      document.querySelectorAll = selector => selector === "[data-spk-machines]" ? panels : [];
      const renders = []; globalThis.render = () => renders.push(tab);
      SPK.plans = { retained: {} }; const signature = spkSig(d);
      const updated = JSON.parse(JSON.stringify(d)); updated.this.status.gpu.util_pct = 61; updated.workers[0].status.gpu.util_pct = 73;
      answers["/api/sparks"] = updated; await spkLoad(true);
      out.sameSignature = signature === spkSig(SPK.data); out.panels = panels.map(text);
      out.renders = renders.length; out.plans = Object.keys(SPK.plans);
      const renamed = JSON.parse(JSON.stringify(updated)); renamed.this.label = "Renamed renderer";
      renamed.names = { Head: renamed.this.label }; answers["/api/sparks"] = renamed;
      await spkLoad(true); out.renamed = panels.map(text); out.afterRename = renders.length;""")
    assert out["sameSignature"] and out["renders"] == 0 and out["plans"] == ["retained"]
    assert all("61%" in panel and "73%" in panel for panel in out["panels"])
    assert all("Renamed renderer" in panel for panel in out["renamed"])
    assert out["afterRename"] == 1


def test_machine_panel_survives_quiet_gpu_and_no_inventory_fallback():
    out = run("""SPK.data = fixture(1); SPK.data.this.label = "Solo renderer";
      out.quiet = text(viewSparks()); delete SPK.data.local_gpus;
      const page = viewSparks(); out.fallback = text(page); out.cards = cards(page).length;
      SPK.data = { supported: false, note: "No compatible GPU" }; out.unsupported = text(spkMachinesPanel());
      SPK.data = null; out.loading = text(spkMachinesPanel());""")
    assert "Each machine" in out["quiet"] and "Solo renderer" in out["quiet"]
    assert "Each machine" in out["fallback"] and "Solo renderer" in out["fallback"]
    assert out["cards"] == 0 and "Local GPUs" not in out["fallback"]
    assert "No compatible GPU" in out["unsupported"] and "Loading…" in out["loading"]


@pytest.mark.parametrize("platform,kind,label", [
    ("spark", "GPU", "CUDA · NVIDIA synthetic GPU"),
    ("spark", "Spark", "DGX SPARK"),
    ("mac", "Mac", "Apple synthetic"),
])
def test_no_worker_machines_have_no_placement_controls(platform, kind, label):
    out = run(f"""SPK.data = fixture(1); const d = SPK.data;
      S.platform = {json.dumps(platform)}; S.platform_label = {json.dumps(label)}; d.this.kind = {json.dumps(kind)};
      if (d.this.kind !== "GPU") delete d.local_gpus;
      const page = viewSparks(); out.placement = !!select(page, "Default video worker");
      out.policy = !!select(page, "Video placement policy"); out.cards = cards(page).length;""")
    assert not out["placement"] and not out["policy"] and out["cards"] == 0


def test_single_card_keeps_simple_render_surface_and_inventory_adds_controls():
    out = run("""SPK.data = fixture(1);
      const queue = el("div"); queue.style = {}; document.querySelector = selector => selector === "#sparks" ? queue : null;
      SPK.n = 4; spkQueue(); let page = viewSparks();
      out.solo = text(page); out.cards = cards(page).length; out.pick = sparksPick();
      out.queueHidden = queue.style.display === "none" && queue.kids.length === 0; out.count = SPK.n;
      out.add = !!button(page, "Add");
      SPK.data = fixture(2); page = viewSparks();
      out.inventory = cards(page).length; out.start = !!button(cards(page)[1], "Start");
      out.placement = !!select(page, "Default video worker");""")
    assert "This GPU renders every clip" in out["solo"] and "Local GPUs" not in out["solo"]
    assert out["cards"] == 0 and out["pick"] is None and out["queueHidden"] and out["count"] == 1
    assert out["add"] and out["inventory"] == 2 and out["start"] and not out["placement"]


def test_plan_summary_does_not_repeat_the_same_message_or_add_double_periods():
    out = run("""SPK.data = fixture(1);
      out.single = text(spkSays({ supported: true, count: "This GPU alone.", says: "This GPU alone.", without: [] })).trim();
      out.other = text(spkSays({ supported: true, count: "This GPU alone.", says: "One clip renders here.", without: [] })).trim();""")
    assert out["single"] == "This GPU alone."
    assert out["other"].count("This GPU alone") == 1 and ".." not in out["other"]
    assert "One clip renders here." in out["other"]


def test_reordered_inventory_updates_gpu_labels_without_changing_saved_worker():
    out = run("""SPK.data = fixture(2); const d = SPK.data, gpu = d.local_gpus.devices[1];
      gpu.worker = worker(gpu); d.workers = [gpu.worker]; d.placement = { worker: gpu.id, policy: "require" };
      const updated = JSON.parse(JSON.stringify(d)); updated.local_gpus.devices[1].index = 3;
      const draws = []; globalThis.render = () => draws.push(true); answers["/api/sparks"] = updated;
      await spkLoad(true); const page = viewSparks(), target = select(page, "Default video worker");
      out.selected = target.kids.filter(o => o.attrs.selected).map(o => [o.attrs.value, text(o)]);
      out.card = text(cards(page)[1]); out.id = gpu.id; out.draws = draws.length;""")
    assert out["draws"] == 1
    assert out["selected"] == [[out["id"], "GPU 3 · Synthetic GPU 1"]]
    assert "GPU 3" in out["card"] and "GPU 1 ·" not in out["card"]
