"""Qualification badges use saved state and route to the relevant card without starting a check."""
import json
import subprocess
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parents[1]

FRONTEND_HARNESS = r"""
const vm = require("node:vm"), fs = require("node:fs");
globalThis.el = (tag, attrs = {}, ...kids) => {
  const n = {tag, attrs, kids:kids.flat(Infinity).filter(k => k != null && k !== false), addEventListener(){},
    setAttribute(k,v){this.attrs[k]=v;}, append(...k){this.kids.push(...k.flat(Infinity));},
    replaceChildren(...k){this.kids=k.flat(Infinity);}, querySelector(){return null;},
    classList:{add(){}}};
  return n;
};
globalThis.put = (node,...kids) => node.append(...kids);
globalThis.text = n => n == null ? "" : typeof n !== "object" ? String(n)
  : Array.isArray(n) ? n.map(text).join(" ") : text(n.kids || []);
globalThis.all = (node, test) => !node || typeof node !== "object" ? []
  : Array.isArray(node) ? node.flatMap(n => all(n,test))
  : [...(test(node) ? [node] : []), ...all(node.kids,test)];
globalThis.find = (node,test) => all(node,test)[0];
globalThis.button = (node,label) => find(node,n => n.tag==="button" && text(n).includes(label));
globalThis.icon = () => null;
globalThis.mark = (id,label) => el("span", {"data-mark":id}, label || id);
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
globalThis.render = () => {};
globalThis.toasts = [];
globalThis.toast = msg => toasts.push(msg);
globalThis.go = tab => {globalThis.destination = tab;};
globalThis.ROOMS = [{id:"image",name:"Image"},{id:"sound",name:"Sound"},{id:"edit",name:"Edit"}];
globalThis.C = {mode:"image",goes:"image",step:0,imageModel:"",imageSize:"1344x768",count:4,scene:"A tree",seed:1};
globalThis.jobs = [];
globalThis.poll = async () => {};
globalThis.sync = () => {};
globalThis.isRef = () => false;
globalThis.clearButton = () => null;
globalThis.field = (label,node) => el("div",{},label,node);
globalThis.bind = (obj,key,node) => {node.attrs.value=obj[key]; return node;};
globalThis.prefs = () => ({presets:{image_model:"qwen",mode:"picture"},appearance:{}});
globalThis.nodes = {"#feature-checks":el("div")};
globalThis.document = {querySelector:s => nodes[s] || null};
globalThis.calls = [];
globalThis.api = async (path, body) => {
  calls.push([path,body]);
  if (path.endsWith("/mode")) {
    const mode = {...S.feature_checks.mode,enabled:body.enabled};
    S = {...S,feature_checks:{...S.feature_checks,mode}};
    return {mode};
  }
  if (path==="/api/state") return S;
  if (path==="/api/feature-checks") return WFC.data;
  return {id:"job1"};
};
globalThis.S = {platform:"windows",image_models:["sd-turbo"],windows_stills:{available:true,model:"sd-turbo",width:512,height:512,max_count:6},
  feature_checks:{supported:true,features:{scout:"SD-Turbo storyboard drafts",qwen:"Qwen 2.1 Turbo","qwen-full":"Qwen 2.1 Full","flux3k-full":"FLUX3-K Full","moss-image":"MOSS-Image",foley:"Foley"}},
  capabilities:{scout:{available:true,qualified:true},qwen:{available:false,qualified:false,why:"Verification needed"},
    foley:{available:true,installed:true,qualified:false}}};
globalThis.capWhy = kind => !kind || S.platform!=="windows" ? ""
  : S.capabilities[kind]?.available ? "" : S.capabilities[kind]?.why || "Unavailable";
const settings = fs.readFileSync(require("node:path").join(process.argv[1],"app","static","settings.js"),"utf8");
vm.runInThisContext("{"+settings.slice(settings.indexOf("const setSwitch ="),settings.indexOf("const setPanel ="))+
  ";globalThis.setSwitch=setSwitch;globalThis.setRow=setRow;}");
for (const name of ["machine.js","engines.js","home.js","apps.js","sound.js"])
  vm.runInThisContext(fs.readFileSync(require("node:path").join(process.argv[1],"app","static",name),"utf8"));
const source = fs.readFileSync(require("node:path").join(process.argv[1],"app","static","index.html"),"utf8");
vm.runInThisContext(source.slice(source.indexOf("const IMAGE_LABELS ="),source.indexOf("// A number of passes")));
vm.runInThisContext(source.slice(source.indexOf("const STEPS ="),source.indexOf("/* ---------- Production")));
"""


def run_frontend(code):
    need_node()
    script = FRONTEND_HARNESS + "\n(async()=>{" + code + "\n})().catch(e=>{console.error(e);process.exit(1);});"
    done = subprocess.run(["node", "-e", script, str(ROOT)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_qualification_mode_is_optional_or_locked_without_hiding_checks():
    out = run_frontend(r"""
const out = {};
const input = () => find(wfcModeControl(), n => n.attrs?.role==="switch");
S.feature_checks.mode = {enabled:false,required:false,can_disable:true,physical_total_mb:131072,reason:"Optional on this machine."};
out.off = {badge:featureBadge("scout"),section:featureChecksSection().tag,checked:input().attrs["aria-checked"]==="true",
  disabled:!!input().disabled,text:text(wfcModeControl())};
S.feature_checks.mode.enabled = true;
out.on = {badge:text(featureBadge("scout")),checked:input().attrs["aria-checked"]==="true"};
for (const [label,memory] of [["small",65536],["unknown",null]]) {
  S.feature_checks.mode = {enabled:true,required:true,can_disable:false,physical_total_mb:memory,
    reason:memory ? "At least 128 GiB installed physical memory is required to disable." : "Installed physical memory is unknown."};
  out[label] = {checked:input().attrs["aria-checked"]==="true",disabled:input().disabled,text:text(wfcModeControl())};
}
delete S.feature_checks.mode;
out.legacy = {badge:text(featureBadge("scout")),control:wfcModeControl(),section:featureChecksSection().tag};
console.log(JSON.stringify(out));
""")
    assert out["off"]["badge"] is None and out["off"]["section"] == "details"
    assert not out["off"]["checked"] and not out["off"]["disabled"]
    assert "either mode" in out["off"]["text"] and "Memory guards, prerequisites and recipe limits always apply" in out["off"]["text"]
    assert out["on"] == {"badge": "Qualified ", "checked": True}
    assert out["small"]["checked"] and out["small"]["disabled"] and "128 GiB" in out["small"]["text"]
    assert out["unknown"]["disabled"] and "unknown" in out["unknown"]["text"]
    assert out["legacy"]["badge"].strip() == "Qualified" and out["legacy"]["control"] is None
    assert out["legacy"]["section"] == "details"


def test_mode_switch_only_changes_policy_and_individual_checks_still_run_when_off():
    out = run_frontend(r"""
S.feature_checks.mode = {enabled:true,required:false,can_disable:true};
const card = {id:"foley",title:"Foley",group:"sound",installed:true,qualified:false,available:true,
  check_available:true,state:"verification_needed",open_tab:"Foley",open_label:"Open",
  evidence:{checked_at:"2026-11-01T10:00:00Z",matches:true,results:[]}};
const native = {id:"qwen",title:"Qwen",group:"image",installed:true,qualified:false,available:false,
  check_available:false,state:"verification_needed",backend:"native",quality:"full-model",
  why:"Windows measurement is not established",requirements:[{pool:"host",required_mb:null,basis:"unknown"}]};
WFC.data = {features:[card,native],mode:S.feature_checks.mode,memory:{pools:{}},policy:"strict"};
const evidence = JSON.stringify(card.evidence);
await wfcSetMode(false);
const page = nodes["#feature-checks"], check = button(page,"Rerun check");
const out = {callsAfterToggle:calls.slice(),text:text(page),checkEnabled:!check.attrs.disabled,
  openEnabled:!button(page,"Open").attrs.disabled,unsupportedButtons:all(page,n=>n.tag==="button" && /not available|not implemented/.test(text(n))).length,
  badge:featureBadge("foley"),section:featureChecksSection().tag,evidenceUnchanged:JSON.stringify(card.evidence)===evidence};
await check.attrs.onclick();
out.checkCalls = calls.filter(([path])=>path.endsWith("/check"));
console.log(JSON.stringify(out));
""")
    assert [call[0] for call in out["callsAfterToggle"]] == [
        "/api/feature-checks/mode", "/api/state", "/api/feature-checks"
    ]
    assert out["callsAfterToggle"][0][1] == {"enabled": False}
    assert out["checkEnabled"] and out["openEnabled"] and out["evidenceUnchanged"]
    assert out["unsupportedButtons"] == 0 and out["badge"] is None and out["section"] == "details"
    assert "Strict update matching is enabled" in out["text"] and "Update matching below is separate" in out["text"]
    assert "Windows measurement: unknown" in out["text"] and "unknown host (unknown)" in out["text"]
    assert "No saved live pass" in out["text"]
    assert out["checkCalls"] == [["/api/feature-checks/check", {"feature": "foley"}]]


def test_selected_full_models_keep_their_controls_and_never_borrow_draft_badges():
    out = run_frontend(r"""
S.image_models = ["sd-turbo","qwen","qwen-full","flux3k-full","moss-image"];
S.image_model_options = S.image_models.map(id=>({id,available:true,installed:true,qualified:id==="sd-turbo",
  backend:id==="sd-turbo" ? "bounded" : "native",quality:id==="sd-turbo" ? "draft" : "full-model"}));
for (const id of S.image_models) if (id!=="sd-turbo") S.capabilities[id]={available:true,qualified:false};
const out = {models:imageModels().map(m=>m.id),full:{}};
out.defaultModel=imageModel();
for (const id of ["qwen","qwen-full","flux3k-full","moss-image"]) {
  C.imageModel = id; C.count = 10;
  const form=viewCreate(), params=stillParams({image_model:id,preset:"2k",width:1344,height:768,count:10});
  const cards=all(engineLanding("image"),n=>n.attrs?.["data-feature-card"]);
  const home=STARTS().find(c=>c.id==="still"), app=apCard(APPS().find(c=>c.id==="still"));
  out.full[id]={params,feature:imageFeature(),badge:text(featureBadge(imageFeature())),
    max:find(form,n=>n.tag==="input" && n.attrs.max===12)?.attrs.max,
    sizes:text(form),engineFeatures:cards.map(c=>c.attrs["data-feature-card"]),homeFeature:home.feature,
    appFeature:app.attrs["data-feature-card"],model:imageModel()};
}
C.imageModel="sd-turbo"; C.count=10;
const draft=viewCreate();
out.draft={params:stillParams({image_model:"sd-turbo",preset:"2k",width:1344,height:768,count:10}),
  max:find(draft,n=>n.tag==="input" && n.attrs.max===6)?.attrs.max,storedSize:C.imageSize,
  badge:text(featureBadge(imageFeature())),marks:all(engineLanding("image"),n=>n.attrs?.["data-mark"]).map(n=>n.attrs["data-mark"])};
C.imageModel="qwen-full"; S.image_model_options.find(m=>m.id==="qwen-full").available=false;
S.image_model_options.find(m=>m.id==="qwen-full").why="Verification needed; unknown Windows measurement";
out.blocked={model:imageModel(),disabled:button(viewCreate(),"Make image").attrs.disabled,why:imageModelWhy(),
  params:stillParams({image_model:"qwen-full",preset:"2k"})};
console.log(JSON.stringify(out));
""")
    for model, result in out["full"].items():
        assert result["params"] == {"image_model": model, "preset": "2k", "width": 1344, "height": 768, "count": 10}
        assert result["feature"] == model and result["engineFeatures"] == [model, model]
        assert result["homeFeature"] == result["appFeature"] == model
        assert result["badge"].split() == ["Not", "qualified", "→"] and result["max"] == 12
        assert "1664 × 928" in result["sizes"] and result["model"] == model
    assert out["draft"]["params"] == {"image_model": "sd-turbo", "width": 512, "height": 512, "count": 6}
    assert out["draft"]["max"] == 6 and out["draft"]["storedSize"] == "1344x768"
    assert out["draft"]["badge"].strip() == "Qualified" and out["draft"]["marks"] == []
    assert out["blocked"]["model"] == "qwen-full" and out["blocked"]["disabled"]
    assert "unknown Windows measurement" in out["blocked"]["why"]
    assert out["blocked"]["params"] == {"image_model": "qwen-full", "preset": "2k"}
    assert out["defaultModel"] == "qwen"


def test_native_informational_card_opens_its_model_without_running_a_check():
    out = run_frontend(r"""
S.feature_checks.mode={enabled:false,required:false,can_disable:true};
WFC.data={memory:{pools:{}},features:[{id:"qwen-full",title:"Qwen 2.1 Full",group:"image",
  installed:true,available:true,qualified:false,check_available:false,backend:"native",quality:"full-model",
  open_tab:"Image",open_label:"Open Qwen Full",open_model:"qwen-full"}]};
wfcDraw();
button(nodes["#feature-checks"],"Open Qwen Full").attrs.onclick();
console.log(JSON.stringify({model:C.imageModel,goes:C.goes,destination,calls,
  checks:all(nodes["#feature-checks"],n=>n.tag==="button" && text(n).includes("Check this feature")).length}));
""")
    assert out == {"model": "qwen-full", "goes": "image", "destination": "Image", "calls": [], "checks": 0}


def test_image_catalog_fallbacks_preserve_explicit_models_and_default_to_available_drafts():
    out = run_frontend(r"""
const out={legacy:imageModel(),legacyLabel:imageModelName()};
S.image_models=[{id:"qwen",label:"Qwen 2.1 Turbo"},{id:"flux3k-fast",label:"FLUX3-K Fast"}];
S.capabilities.qwen={available:true,qualified:true};
out.oldObjects={model:imageModel(),ids:imageModels().map(m=>m.id)};
S.image_models=["sd-turbo"];
S.windows_runtime={image_models:[{id:"sd-turbo",available:true,backend:"bounded",quality:"draft"},
  {id:"qwen",available:false,installed:true,why:"Verification needed: no Windows measurement",backend:"native",quality:"full-model"}]};
out.small={model:imageModel(),ids:imageModels().map(m=>m.id)};
C.imageModel="qwen";
out.explicit={model:imageModel(),why:imageModelWhy(),disabled:button(viewCreate(),"Make image").attrs.disabled};
S.windows_runtime.image_models=S.windows_runtime.image_models.filter(m=>m.id!=="qwen");
out.missing={model:imageModel(),why:imageModelWhy()};
S.platform="mac"; delete S.windows_runtime;
S.image_models=[{id:"qwen",label:"Qwen 2.1 Turbo"}]; C.imageModel="";
out.mac={model:imageModel(),params:stillParams({preset:"2k",image_model:"qwen",count:12})};
console.log(JSON.stringify(out));
""")
    assert out["legacy"] == "sd-turbo" and out["legacyLabel"] == "SD-Turbo"
    assert out["oldObjects"] == {"model": "qwen", "ids": ["qwen", "flux3k-fast"]}
    assert out["small"] == {"model": "sd-turbo", "ids": ["sd-turbo", "qwen"]}
    assert out["explicit"]["model"] == out["missing"]["model"] == "qwen"
    assert out["explicit"]["disabled"] and "Verification needed" in out["explicit"]["why"]
    assert "Choose a model explicitly" in out["missing"]["why"]
    assert out["mac"] == {"model": "qwen", "params": {"preset": "2k", "image_model": "qwen", "count": 12}}


def test_initial_settings_leave_model_default_dynamic_and_preserve_explicit_picker_choices():
    out = run_frontend(r"""
vm.runInThisContext(fs.readFileSync(require("node:path").join(process.argv[1],"app","static","settings.js"),"utf8"));
S.image_model_options=[{id:"sd-turbo",available:true,backend:"bounded"},
  {id:"qwen",available:false,backend:"native",why:"Verification needed"}];
const out={initial:[]};
for (const settings of [null,{presets:{image_model:"qwen"}}]) {
  SET.data=settings; C.imageModel="qwen";
  prefsToCreate();
  out.initial.push({explicit:C.imageModel,model:imageModel(),feature:imageFeature(),badge:text(featureBadge(imageFeature()))});
}
find(imageModelPick(),n=>n.attrs?.role==="radio" && text(n).includes("Qwen 2.1 Turbo")).attrs.onclick();
out.picked={model:imageModel(),why:imageModelWhy(),params:stillParams({image_model:imageModel(),width:1024,height:1024})};
prefsToCreate(); out.reset={explicit:C.imageModel,model:imageModel()};
S.image_model_options.find(m=>m.id==="qwen").available=true;
out.newlyAvailable=imageModel();
console.log(JSON.stringify(out));
""")
    assert out["initial"] == [{"explicit": "", "model": "sd-turbo", "feature": "scout", "badge": "Qualified "}] * 2
    assert out["picked"] == {"model": "qwen", "why": "Verification needed",
                             "params": {"image_model": "qwen", "width": 1024, "height": 1024}}
    assert out["reset"] == {"explicit": "", "model": "sd-turbo"}
    assert out["newlyAvailable"] == "qwen"


def test_still_params_works_alone_for_legacy_storyboards_without_changing_new_catalog_choices():
    out = run_frontend(r"""
const context={S:{windows_stills:{available:true,model:"sd-turbo",width:512,height:512}}};
vm.runInNewContext("const stillParams = "+source.split("const stillParams = ")[1].split("\nconst imageModelName")[0]
  + "\nglobalThis.build=stillParams;", context);
const out={standalone:context.build({prompt:"A door",count:1,seed:0,preset:"2k"})};
context.imageModel=()=>"qwen";
out.legacyDefault=context.build({prompt:"A tree",count:1});
out.batch=context.build({prompts:["A door","A tree"],seeds:[0,7],suite_shots:["a","b"]});
out.explicitLegacy=context.build({image_model:"qwen-full",preset:"2k",count:10});
context.S.image_model_options=[{id:"sd-turbo",available:true},{id:"qwen-full",available:false}];
context.imageModel=()=>"qwen-full";
out.newDefault=context.build({prompt:"A door",preset:"2k",count:10});
out.newExplicit=context.build({image_model:"qwen-full",width:1344,height:768,count:10});
delete context.imageModel; delete context.S.windows_stills; delete context.S.image_model_options;
out.otherLegacy=context.build({prompt:"A door",preset:"2k"});
console.log(JSON.stringify(out));
""")
    assert out["standalone"] == {"prompt": "A door", "count": 1, "seed": 0, "width": 512, "height": 512, "image_model": "sd-turbo"}
    assert out["legacyDefault"]["image_model"] == "sd-turbo"
    assert out["batch"] == {"prompts": ["A door", "A tree"], "seeds": [0, 7], "suite_shots": ["a", "b"],
                            "width": 512, "height": 512, "image_model": "sd-turbo"}
    assert out["explicitLegacy"] == {"image_model": "qwen-full", "preset": "2k", "count": 10}
    assert out["newDefault"] == {"prompt": "A door", "preset": "2k", "count": 10, "image_model": "qwen-full"}
    assert out["newExplicit"] == {"image_model": "qwen-full", "width": 1344, "height": 768, "count": 10}
    assert out["otherLegacy"] == {"prompt": "A door", "preset": "2k"}


def test_home_composer_names_and_routes_the_actual_backend_and_retakes_keep_it():
    out = run_frontend(r"""
const out={};
HC.mode="picture";
out.draft={model:hcPictureModel(),text:text(homeComposer()),params:(await hcRequest("picture","A tree",1))[1].params};
S.image_models=["sd-turbo","qwen","moss-image"];
S.image_model_options=S.image_models.map(id=>({id,available:true,installed:true,qualified:false,
  backend:id==="sd-turbo" ? "bounded" : "native"}));
S.capabilities.qwen={available:true,qualified:false}; S.capabilities["moss-image"]={available:true,qualified:false};
HC.pictureModel="qwen";
out.qwen={text:text(homeComposer()),params:(await hcRequest("picture","A tree",1))[1].params};
await hcGenerate({mode:"picture",text:"A tree",seed:1,image_model:"sd-turbo"});
out.retake=calls.filter(([path])=>path==="/api/jobs").at(-1)[1].params;
S.image_model_options.find(m=>m.id==="qwen").available=false;
S.image_model_options.find(m=>m.id==="qwen").why="Install needed";
out.blocked={model:hcPictureModel(),mode:hcMode(),disabled:button(homeComposer(),"Generate").attrs.disabled,text:text(homeComposer())};
console.log(JSON.stringify(out));
""")
    assert out["draft"]["model"] == "sd-turbo" and "SD-Turbo" in out["draft"]["text"]
    assert out["draft"]["params"]["width"] == out["draft"]["params"]["height"] == 512
    assert out["draft"]["params"]["image_model"] == out["retake"]["image_model"] == "sd-turbo"
    assert out["qwen"]["params"]["image_model"] == "qwen" and out["qwen"]["params"]["preset"] == "2k"
    assert "Qwen 2.1 Turbo" in out["qwen"]["text"]
    assert out["blocked"]["model"] == "qwen" and out["blocked"]["mode"] == "picture" and out["blocked"]["disabled"]
    assert "Install needed" in out["blocked"]["text"]


def test_sound_limits_follow_each_selected_backend_not_the_windows_platform():
    out = run_frontend(r"""
SFX.state={windows:true,seconds:{sfx:[1,47],foley:[1,5]},kinds:{
  sfx:{ready:true,bounded:false},foley:{ready:true,bounded:true}}};
SFX.takes=[]; SFX.clips=[];
SFX.mode="sfx"; SFX.seconds=12;
const full=viewFoley(), input=find(full,n=>n.tag==="input" && n.attrs.max===47);
const out={full:{disabled:input.attrs.disabled,value:input.attrs.value,text:text(full)}};
SFX.mode="foley"; out.boundedFoley=text(viewFoley());
SFX.state.kinds.sfx.bounded=true; SFX.state.seconds.sfx=[5,5];
SFX.state.kinds.foley.bounded=false; SFX.state.seconds.foley=[1,30];
SFX.mode="sfx";
const draft=viewFoley(), fixed=find(draft,n=>n.tag==="input" && n.attrs.max===5);
out.boundedSfx={disabled:fixed.attrs.disabled,value:fixed.attrs.value,text:text(draft)};
SFX.mode="foley"; out.fullFoley=text(viewFoley());
delete SFX.state.kinds.sfx.bounded;
SFX.mode="sfx"; out.legacyFixed=find(viewFoley(),n=>n.tag==="input" && n.attrs.max===5).attrs.disabled;
console.log(JSON.stringify(out));
""")
    assert not out["full"]["disabled"] and out["full"]["value"] == 12
    assert "Seconds (1 to 47)" in out["full"]["text"]
    assert "Windows recipe: clips of 1-5 seconds" in out["boundedFoley"]
    assert out["boundedSfx"]["disabled"] and out["boundedSfx"]["value"] == 5
    assert "100 passes, CFG 7" in out["boundedSfx"]["text"]
    assert "Windows recipe" not in out["fullFoley"] and out["legacyFixed"]


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
  description:blocked.kids[0].kids[2].kids[0].kids[1].kids.join(""),
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
    assert "storyboard draft" in result["description"] and "Not qualified" not in result["description"]
    assert result["destination"] == "Machine" and result["focus"] == "scout"
