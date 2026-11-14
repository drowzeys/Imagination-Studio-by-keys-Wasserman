"""Restored qualification UI contracts, with isolated Node fixtures and no inference."""
import json

import pytest

from test_shell_page import ROOT, run
from test_discover_page import run as discover_run


FIXTURE = """
S.platform = "windows";
S.feature_checks = { supported: true, url: "/api/feature-checks",
  features: { hlg: "HDR grading", qwen: "Qwen 2.1 Turbo", video: "Video" },
  mode: { enabled: true, required: true, can_disable: false, physical_total_mb: 65536 } };
S.capabilities = { hlg: { available: false, qualified: false, installed: true, why: "Open Machine > Feature checks" } };
const card = { id: "hlg", title: "HDR grading", group: "edit", installed: true, qualified: false,
  available: false, check_available: true, scope: "Ten-second 720p recipe", requirements: [],
  memory: {}, evidence: {}, open_tab: "Apps", open_label: "Open HDR" };
const data = { features: [card], mode: S.feature_checks.mode, memory: {}, policy: "balanced" };
answers["/api/feature-checks"] = data;
document.querySelector = selector => NODES[selector] || null;
const box = el("div", {}); box.querySelector = () => null;
NODES["#feature-checks"] = box;
"""


def test_diagnostics_start_collapsed_and_opening_refreshing_never_posts_a_check():
    out = run(FIXTURE + """
      const section = featureChecksSection(); out.open = section.attrs.open;
      await new Promise(resolve => setTimeout(resolve, 10));
      out.groupsClosed = !find(box, node => node.attrs?.["data-check-group"]).attrs.open;
      out.controls = text(box); await button(box, "Refresh status").attrs.onclick();
      out.posts = calls.filter(([, body]) => body !== undefined);
      clearTimeout(WFC.timer);
    """)
    assert out["open"] is False and out["groupsClosed"] is True
    assert "Check this feature" in out["controls"] and "Download saved report" in out["controls"]
    assert out["posts"] == []


def test_machine_feature_checks_sit_directly_below_the_four_gauges():
    out = run(FIXTURE + """
      document.createElementNS = () => ({ setAttribute() {}, append() {}, style: {} });
      answers["/api/storage"] = { buckets: [], free_gb: 10, total_gb: 20, used_pct: 50, computing: false };
      answers["/api/telemetry/history"] = { samples: [] };
      const page = viewMachine();
      out.gauges = page.kids.findIndex(node => node?.attrs?.class === "gauges");
      out.stats = page.kids.findIndex(node => node?.attrs?.class === "split m-gap");
      out.checks = page.kids.findIndex(node => node?.attrs?.id === "feature-check-section");
      out.closed = !page.kids[out.checks].attrs.open;
      await new Promise(resolve => setTimeout(resolve, 10)); clearTimeout(WFC.timer);
    """)
    assert 0 <= out["gauges"] < out["checks"] < out["stats"]
    assert out["checks"] == out["gauges"] + 1
    assert out["closed"] is True


@pytest.mark.parametrize(
    "mode,disabled,label",
    [
        ({"enabled": True, "required": True, "can_disable": False, "physical_total_mb": 65536}, True, "Required below 128 GiB"),
        ({"enabled": True, "required": True, "can_disable": False, "physical_total_mb": None,
          "reason": "Installed memory is unknown"}, True, "Installed memory is unknown"),
        ({"enabled": False, "required": False, "can_disable": True, "physical_total_mb": 131072}, False, "badges are hidden"),
        ({"enabled": True, "required": False, "can_disable": True, "physical_total_mb": 131072}, False, "badges are shown"),
    ],
)
def test_mode_control_uses_backend_policy_and_diagnostics_survive_badges_off(mode, disabled, label):
    out = run(FIXTURE + f"""
      S.feature_checks.mode = {json.dumps(mode)};
      const control = wfcModeControl({{ mode: S.feature_checks.mode }});
      out.disabled = find(control, node => node.attrs?.role === "switch").disabled;
      out.text = text(control); out.badge = featureBadge("hlg") !== null;
      out.guidance = text(featureGuidance("hlg"));
    """)
    assert out["disabled"] is disabled and label in out["text"]
    assert out["badge"] is mode["enabled"]
    assert "Feature checks" in out["guidance"]


def test_badges_and_model_links_open_the_specific_group_without_running_a_check():
    out = run(FIXTURE + """
      const badge = featureBadge("hlg"); badge.attrs.onclick();
      WFC.data = data; wfcDraw();
      out.tab = tab; out.selected = WFC.selected; out.open = WFC.open;
      out.groupOpen = find(box, node => node.attrs?.["data-check-group"] === "edit").attrs.open;
      out.posts = calls.filter(([, body]) => body !== undefined);
    """)
    assert out == {"tab": "Machine", "selected": "hlg", "open": True, "groupOpen": True, "posts": []}


def test_checks_and_cancellation_use_explicit_normal_queue_actions():
    out = run(FIXTURE + """
      WFC.data = data; wfcDraw(); out.before = calls.length;
      await button(box, "Check this feature").attrs.onclick();
      out.check = calls.find(([path]) => path.endsWith("/check"));
      card.job = { id: "check-1", status: "running" }; wfcDraw();
      await button(box, "Cancel check").attrs.onclick();
      out.cancel = calls.find(([path]) => path.endsWith("/cancel"));
      clearTimeout(WFC.timer);
    """)
    assert out["before"] == 0
    assert out["check"] == ["/api/feature-checks/check", {"feature": "hlg"}]
    assert out["cancel"] == ["/api/jobs/check-1/cancel", {}]


def test_optional_mode_change_refreshes_state_without_queuing_a_check():
    out = run(FIXTURE + """
      const mode = { enabled: false, required: false, can_disable: true, physical_total_mb: 131072 };
      answers["/api/feature-checks/mode"] = { mode };
      answers["/api/state"] = { ...S, feature_checks: { ...S.feature_checks, mode } };
      answers["/api/feature-checks"] = { ...data, mode };
      await wfcSetMode(false);
      out.enabled = S.feature_checks.mode.enabled;
      out.posts = calls.filter(([, body]) => body !== undefined);
      out.badge = featureBadge("hlg");
    """)
    assert out["enabled"] is False and out["badge"] is None
    assert out["posts"] == [["/api/feature-checks/mode", {"enabled": False}]]


def test_refused_check_surfaces_the_backend_error_without_discarding_saved_results():
    out = run(FIXTURE + """
      card.qualified = true; card.evidence = { checked_at: "2026-11-01T00:00:00Z", matches: true };
      WFC.data = data;
      answers["/api/feature-checks/check"] = new Error("Not enough current memory");
      await wfcCheck(card);
      out.error = toasts.at(-1); out.qualified = WFC.data.features[0].qualified;
      out.saved = WFC.data.features[0].evidence.checked_at;
    """)
    assert out == {"error": "Not enough current memory", "qualified": True, "saved": "2026-11-01T00:00:00Z"}


def test_progressive_recipes_show_partial_results_and_send_only_the_selected_scope():
    out = run(FIXTURE + """
      card.qualification_levels = [
        { id: "basic", title: "Basic", width: 512, height: 512, count: 1, qualified: true },
        { id: "large", title: "Large", width: 1024, height: 1024, count: 1, qualified: false, outcome: "failed",
          attempt: { error: "Guard refused the larger recipe" } }];
      card.qualification_groups = [{ id: "remaining", title: "Remaining", scopes: ["basic", "large"], remaining: ["large"] }];
      WFC.data = data; wfcDraw(); out.results = text(box);
      out.summary = text(wfcSummary([card]));
      WFC.scopes.hlg = "large"; await wfcCheck(card);
      WFC.scopes.hlg = "group:remaining"; await wfcCheck(card);
      out.requests = calls.filter(([path]) => path.endsWith("/check"));
    """)
    assert "1/2 passed" in out["results"] and "Guard refused the larger recipe" in out["results"]
    assert "0.5/1 features qualified" in out["summary"]
    assert out["requests"] == [
        ["/api/feature-checks/check", {"feature": "hlg", "scope": "large"}],
        ["/api/feature-checks/check", {"feature": "hlg", "group": "remaining"}],
    ]


def test_failed_rerun_keeps_saved_evidence_and_current_contention_separate():
    out = run(FIXTURE + """
      Object.assign(card, { qualified: true, available: true, runtime_updated: true,
        evidence: { checked_at: "2026-11-01T00:00:00Z", matches: true, results: [{ peak_job_commit_mb: 1024 }] },
        attempt: { state: "cancelled", finished: "2026-11-02T00:00:00Z", error: "The check was cancelled" },
        requirements: [{ pool: "host", required_mb: 4096 }] });
      data.memory = { pools: { host: { label: "Host", available_mb: 1024 } } };
      WFC.data = data; wfcDraw(); out.text = text(box);
      answers["/api/feature-checks"] = new Error("Status is unavailable");
      await wfcLoad(); out.failure = text(box);
    """)
    assert "feature remains qualified" in out["text"]
    assert "Memory is currently busy" in out["text"] and "saved qualification is unchanged" in out["text"]
    assert "Earlier evidence is retained" in out["text"]
    assert "Status is unavailable" in out["failure"] and "Saved live check:" in out["failure"]


def test_completed_checks_refresh_capabilities_and_cached_feature_states():
    out = run(FIXTURE + """
      card.job = { id: "check-1", status: "running" };
      WFC.data = { ...data, features: [{ ...card }] };
      globalThis.ACT = { state: {} }; globalThis.SWAP = { state: {} }; FIN.state = {}; SFX.state = {};
      card.job = { id: "check-1", status: "done" };
      answers["/api/state"] = { ...S, capabilities: { hlg: { available: true, qualified: true } } };
      await wfcObserveJobs([{ id: "check-1", kind: "qualification", status: "done" }]);
      out.available = S.capabilities.hlg.available;
      out.invalidated = [ACT.state, SWAP.state, FIN.state, SFX.state];
    """)
    assert out == {"available": True, "invalidated": [None, None, None, None]}


def test_models_inventory_explains_installation_fit_and_links_independent_checks():
    out = discover_run("""
      const opened = []; globalThis.openFeatureCheck = kind => opened.push(kind);
      const node = el("div", {}); DSC.data = {};
      const row = dscRow(node, { id: "qwen", label: "Qwen", state: "missing", feature: "qwen",
        expected: "C:\\\\models\\\\qwen", requirements_note: "Needs its text encoder", why: "Install the selected backend",
        install: { how: "command", shown: "powershell setup.ps1" },
        download_size: { bytes: 33000000000 }, runtime_fit: { state: "unknown", why: "RAM need has not been measured" } });
      out.text = text(row); button(row, "Feature checks").attrs.onclick();
      out.opened = opened; out.posts = calls;
      out.copy = !!button(row, "Copy setup command").attrs.disabled;
    """)
    assert "33.0 GB" in out["text"] and "Runtime fit unknown" in out["text"]
    assert "Needs its text encoder" in out["text"] and "Install the selected backend" in out["text"]
    assert out["opened"] == ["qwen"] and out["posts"] == [] and out["copy"] is False


def test_models_setup_requires_acknowledgement_of_insufficient_capacity():
    out = discover_run("""
      globalThis.copyText = command => calls.push(command);
      const node = el("div", {}); DSC.data = { needs: [] };
      const need = { id: "large", install: { how: "command", shown: "powershell setup.ps1" },
        runtime_fit: { state: "insufficient_capacity" } };
      let control = dscInstall(null, need); out.disabled = button(control, "Copy setup command").attrs.disabled;
      button(control, "Copy setup command").attrs.onclick(); out.before = [...calls];
      find(control, node => node.tag === "input").attrs.onchange({ target: { checked: true } });
      control = dscInstall(null, need); button(control, "Copy setup command").attrs.onclick();
      out.after = calls;
    """)
    assert out["disabled"] is True and out["before"] == []
    assert out["after"] == ["powershell setup.ps1 -AllowInsufficientModel"]


@pytest.mark.parametrize("state,has_uninstall", [("expected", True), ("expected", False), ("missing", True), ("found", True)])
def test_models_uninstall_only_copies_the_available_command_for_installed_weights(state, has_uninstall):
    uninstall = {"scope": "Removes this model's exclusive weights; projects and receipts stay intact.",
                 "shown": "powershell -File scripts\\remove-windows-model.ps1 -Model qwen"}
    out = discover_run(f"""
      const copied = []; globalThis.copyText = (command, label) => copied.push([command, label]);
      DSC.data = {{}};
      const row = dscRow(null, {{ id: "qwen", label: "Qwen", state: {json.dumps(state)},
        uninstall: {json.dumps(uninstall if has_uninstall else None)} }});
      const copy = button(row, "Copy uninstall command"); out.offered = !!copy;
      out.before = [...copied]; out.requests = calls;
      if (copy) {{
        out.closed = !find(row, node => node.tag === "details").attrs.open;
        out.text = text(row); copy.attrs.onclick(); out.copied = copied;
      }}
    """)
    expected = state == "expected" and has_uninstall
    assert out["offered"] is expected
    assert out["before"] == [] and out["requests"] == []
    if expected:
        assert out["closed"] is True and uninstall["scope"] in out["text"]
        assert out["copied"] == [[uninstall["shown"], "Uninstall command"]]


def model_helpers():
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    return html[html.index("const IMAGE_LABELS"):html.index("const choice =")]


def test_model_selection_exposes_unavailable_options_without_silent_fallback():
    out = run(FIXTURE + model_helpers() + """
      S.image_models = [{ id: "sd-turbo" }];
      S.image_model_options = [
        { id: "sd-turbo", available: true },
        { id: "qwen", available: false, installed: true, qualified: false, backend: "native",
          why: "Run the explicit Qwen check", scope: "Only checked canvases", qualification_required: true }];
      C.imageModel = "qwen";
      out.ids = imageModels().map(model => model.id); out.selected = imageModel();
      out.why = imageModelWhy(); const picker = imageModelPick(); out.text = text(picker);
      out.detailsClosed = !find(picker, node => node.attrs?.["data-model-details"]).attrs.open;
      out.posts = calls;
    """)
    assert out["ids"] == ["sd-turbo", "qwen"] and out["selected"] == "qwen"
    assert out["why"] == "Run the explicit Qwen check"
    assert "Backend: native" in out["text"] and "Only checked canvases" in out["text"]
    assert out["detailsClosed"] is True
    assert out["posts"] == []


def test_available_model_keeps_details_collapsed_and_does_not_repeat_status_prose():
    out = run(FIXTURE + model_helpers() + """
      S.image_models = [{ id: "qwen" }];
      S.image_model_options = [{ id: "qwen", available: true, backend: "native", scope: "Exact checked recipe",
        why: "Routine informational status" }];
      C.imageModel = "qwen";
      const picker = imageModelPick(), details = find(picker, node => node.attrs?.["data-model-details"]);
      out.closed = !details.attrs.open;
      out.text = text(picker); out.details = text(details);
      out.compact = !featureGuidance("hlg").attrs.class.includes("panel");
    """)
    assert out["closed"] is True and out["compact"] is True
    assert "Routine informational status" not in out["text"]
    assert "Backend: native" in out["details"] and "Exact checked recipe" in out["details"]


def test_home_uses_the_selected_models_checked_canvas_and_links_its_diagnostics():
    out = run(FIXTURE + model_helpers() + """
      Object.assign(globalThis, { imageModels, defaultImageModel, imageModelWhy, imageCheckedRecipe, stillParams, imageFeature });
      S.image_models = [{ id: "qwen" }];
      S.image_model_options = [{ id: "qwen", available: true, qualified: true, qualification_required: true,
        verified_recipe: { width: 512, height: 512, count: 1 } },
        { id: "qwen-full", available: false, why: "Install Qwen Full" }];
      HC.mode = "picture";
      const request = await hcRequest("picture", "A lighthouse", 0);
      out.params = request[1].params;
      const page = homeComposer(); out.text = text(page);
      find(page, node => node.tag === "select" && node.attrs["aria-label"] === "Picture model")
        .attrs.onchange({ target: { value: "qwen-full" } });
      out.selected = hcPictureModel();
      out.disabled = button(homeComposer(), "Generate").attrs.disabled;
      try { await hcRequest("picture", "A lighthouse", 0); } catch (error) { out.refused = error.message; }
    """)
    assert out["params"] == {"prompt": "A lighthouse", "count": 1, "seed": 0, "image_model": "qwen", "width": 512, "height": 512}
    assert "512 × 512" in out["text"] and "Feature checks" in out["text"]
    assert out["selected"] == "qwen-full" and out["disabled"] is True and out["refused"] == "Install Qwen Full"


@pytest.mark.parametrize("required", [True, False])
def test_image_form_offers_and_queues_only_the_applicable_recipe(required):
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    form = html[html.index("function viewCreate()"):html.index("/* ---------- Production")]
    choice = html[html.index("const choice ="):html.index("// A number of passes")]
    steps = html[html.index("const STEPS ="):html.index("const C_TEXT =")]
    out = run(FIXTURE + model_helpers() + choice + steps + form + f"""
      const base = el; globalThis.el = (...args) => Object.assign(base(...args), {{ setAttribute() {{}} }});
      globalThis.clearButton = () => null; globalThis.clearAllCreate = () => {{}};
      globalThis.bind = (obj, key, node) => {{ node.value = obj[key]; return node; }};
      S.image_models = [{{ id: "qwen" }}];
      S.image_model_options = [{{ id: "qwen", available: true, qualified: true, qualification_required: {json.dumps(required)},
        verified_recipe: {{ width: 512, height: 512, count: 1 }} }}];
      Object.assign(C, {{ mode: "image", goes: "image", imageModel: "qwen", imageSize: "1344x768", count: 6, scene: "A lighthouse" }});
      answers["/api/jobs"] = {{ id: "still-1" }};
      const page = viewCreate(); out.text = text(page);
      out.maxCount = find(page, node => node.tag === "input" && node.attrs.max).attrs.max;
      await button(page, "Make image").attrs.onclick();
      out.request = calls.find(([path]) => path === "/api/jobs")[1];
    """)
    params = out["request"]["params"]
    assert (params["width"], params["height"], params["count"]) == ((512, 512, 1) if required else (1344, 768, 6))
    assert out["maxCount"] == (1 if required else 12)
    assert ("16:9 large" in out["text"]) is not required


def test_image_parameters_reuse_exact_checked_recipes_and_policy_off_removes_only_evidence_bounds():
    out = run(FIXTURE + model_helpers() + """
      S.image_models = [{ id: "qwen" }];
      const model = { id: "qwen", qualified: true, available: true, qualification_required: true,
        verified_recipe: { width: 512, height: 512, count: 1 },
        verified_scopes: [{ width: 512, height: 512, count: 1 }, { width: 1024, height: 1024, count: 2 }] };
      S.image_model_options = [model]; C.imageModel = "qwen";
      out.checked = stillParams({ width: 1024, height: 1024, count: 2 });
      try { stillParams({ width: 1024, height: 512, count: 1 }); } catch (error) { out.refused = error.message; }
      try { stillParams({ width: 512, height: 512, count: 2 }); } catch (error) { out.count = error.message; }
      model.qualification_required = false;
      out.off = stillParams({ width: 1344, height: 768, count: 6 });
      C.imageModel = "missing"; out.missing = imageModelWhy();
    """)
    assert out["checked"] == {"width": 1024, "height": 1024, "count": 2, "image_model": "qwen"}
    assert "not been verified" in out["refused"] and "not been verified" in out["count"]
    assert out["off"] == {"width": 1344, "height": 768, "count": 6, "image_model": "qwen"}
    assert "Choose a model explicitly" in out["missing"]
