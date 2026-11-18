"use strict";
// Prompt Builder (app/prompt_builder.py): a MiniMax H3 prompt from your own cast, a scenario, a place, a film look
// and an idea, written by the Studio's brain or laid out from the choices alone, then sent into Create: the scene,
// action, camera and story beats to step 1, the lines, voices, soundscape and music to step 3, and the length (over
// 15 s, the split into chained clips joined into one video).
const PB = { opts: null, cast: null, pick: [], refs: true, clip: { job: "", at: 1, part: "full", found: null }, scenario: "", location: "", time: "", mood: "", look: "", shots: "auto",
  seconds: 10, language: "English", idea: "", prompt: "", busy: false, by: "", edit: null, said: "",
  characterQuery: "", characterChoice: "", sheetImageModel: "studio",
  refmodsAvailable: [], refmods: [], refmodsLimit: 0, refmodQuery: "", refmodChoice: "",
  refmodStrength: 1, refmodsError: "", refmodsLoaded: false,
  refmodsFolder: "", refmodsFolderEntries: null, refmodsNote: "" };

async function pbLoad() { [PB.opts, PB.cast] = await Promise.all([api("/api/builder/options"), api("/api/builder/cast")]);
  if (PB.cast.some(c => c.sheet && ["still", "turn"].includes(c.sheet.state))) pbWatch();
  render(); await pbRefreshRefMods(true); }

async function pbRefreshRefMods(silent = false) {
  try {
    const result = await api("/api/builder/refmods");
    if (!result || !Array.isArray(result.items)) throw new Error("The RefMod list was not returned");
    PB.refmodsAvailable = result.items.filter(one => one && typeof one.name === "string" && one.name.trim());
    PB.refmodsLimit = Math.max(0, Number(result.limit) || 0);
    PB.refmodsFolder = typeof result.folder === "string" ? result.folder : "";
    PB.refmodsFolderEntries = result.folder_entries == null ? null : Number(result.folder_entries);
    PB.refmodsNote = typeof result.note === "string" ? result.note : "";
    // Don't silently apply RefMods that have been removed from the Library.
    PB.refmods = PB.refmods.filter(one => PB.refmodsAvailable.some(saved => saved.name === one.name));
    PB.refmodsError = "";
    if (!silent) toast(PB.refmodsAvailable.length + " saved RefMod(s) found");
  } catch (e) {
    PB.refmodsError = e.message || String(e);
    if (!silent) toast("Cannot read saved RefMods: " + PB.refmodsError);
  }
  PB.refmodsLoaded = true;
  if (typeof tab === "undefined" || tab === "Prompt Builder") render();
}
const pbSave = async () => { PB.cast = await api("/api/builder/cast", PB.cast, "PUT"); };
const pbKnown = one => !!(one && (one.catalogue || String(one.id).startsWith("hf-")));
async function pbSaveCharacter(one) {
  if (pbKnown(one)) {
    const updated = await api("/api/builder/character-details", {
      id: one.id, look: one.look || "", wardrobe: one.wardrobe || "", voice: one.voice || "",
      refmod: one.refmod || "", refmod_strength: Number(one.refmod_strength) || 1
    }, "PUT");
    Object.assign(one, updated);
  } else await pbSave();
}
const pbCharacterLabel = one => one.name + (one.actor ? " — " + one.actor : "") +
  (one.franchise ? " · " + one.franchise : "") + (one.status === "onthefence" ? " [experimental]" : "");
const pbLooks = () => ({ "": "No particular look", ...PB.opts.looks });
// a drop-down that also takes a typed value: the list's choices, or your own words
function pbPick(key, label, list, placeholder) {
  const id = "pb-" + key, input = bind(PB, key, el("input", { list: id, placeholder }));
  return field(label, el("div", {}, input, el("datalist", { id }, list.map(v => el("option", { value: v })))));
}

function pbCharacter(one, i) {
  const on = PB.pick.includes(one.id), known = pbKnown(one);
  const choose = () => { PB.pick = on ? PB.pick.filter(id => id !== one.id) :
    (PB.pick.length < 8 ? [...PB.pick, one.id] : PB.pick);
    if (!on && PB.pick.length >= 8) toast("Up to eight characters in a scene.");
    render(); };
  if (PB.edit === one.id) {
    const done = async () => { try { await pbSaveCharacter(one); render(); toast(one.name + " saved"); }
      catch (e) { toast(e.message); } };
    const busySheet = ["still", "turn"].includes((one.sheet || {}).state);
    return el("div", { class: "seg", style: "margin-top:10px" },
      el("b", {}, "Character details · " + one.name),
      known ? el("div", {class: "note"}, "Actor: " + (one.actor || "unknown") +
        " · " + (one.franchise || "unknown production") + (one.status === "onthefence" ? " · experimental" : "")) : null,
      known ? el("div", {class: "note"}, "The index identifies the character and actor but does not supply a physical description. Add one yourself; a blank description uses the role and actor as a prompt reference.") : null,
      el("div", { class: "row" },
        known ? null : field("Name", bind(one, "name", el("input"))),
        field("Their era's look (optional)", select(one, "era", pbLooks()))),
      field("Appearance / actor description", bind(one, "look", el("textarea", {style: "min-height:72px", maxlength: 600, placeholder: "Age, face, hair, build and distinguishing features; describe what should appear"}))),
      field("Wardrobe", bind(one, "wardrobe", el("textarea", {style: "min-height:55px", maxlength: 600, placeholder: "Clothes, accessories and costume details"}))),
      field("Voice (optional — automatic MiniMax H3 voice if blank)", bind(one, "voice", el("input", {maxlength: 600, placeholder: "Accent, pitch, timbre and delivery, or leave empty"}))),
      field("RefMod for this character (video only)", select(one, "refmod", {"":"No character RefMod", ...Object.fromEntries(PB.refmodsAvailable.map(m => [m.name, m.name]))})),
      el("div", {class:"note"}, "This saved association adds the RefMod to scenes where this character is selected. RefMods apply to the whole Ref2VA clip, not one subject, and cannot be used by the still-image model that makes a reference sheet."),
      pbPictureTools(one),
      pbSheet(one),
      el("div", { class: "row", style: "margin-top:8px" },
        el("button", { class: "fit primary", onclick: done }, "Save description"),
        el("button", { class: "fit", onclick: () => { if (!on && PB.pick.length >= 8) return toast("Maximum eight characters in one prompt"); PB.pick = on ? PB.pick.filter(p => p !== one.id) : [...PB.pick, one.id]; render(); } }, on ? "Remove from scene" : "Add to scene"),
        el("button", {class: "fit", disabled: busySheet, onclick: e => { e.stopPropagation(); pbMakeSheet(one); }},
          busySheet ? "Making reference sheet…" : "Make a reference sheet"),
        known ? null : el("button", {class:"fit", onclick: async () => { PB.cast.splice(i,1); PB.pick = PB.pick.filter(p => p !== one.id); PB.characterChoice = ""; PB.edit = null; try { await pbSave(); render(); } catch (e) { toast(e.message); } }}, "Delete custom character"),
        el("button", {class:"fit", onclick: () => {PB.edit=null; render();}}, "Close")));
  }
  return el("div", { class: "tile" + (on ? " on" : ""), style: "padding:10px;cursor:pointer", title: "Click to put them in or remove them from the scene",
      onclick: choose },
    el("b", {}, (on ? "✓ " : "") + one.name),
    known ? el("div", {class:"note"}, [one.actor, one.franchise].filter(Boolean).join(" · ")) : null,
    el("div", { class: "note" }, one.look || (known ? "Appearance: add a description" : "Appearance not described")),
    el("div", { class: "note" }, "Voice: " + (one.voice || "Automatic (MiniMax H3)") + (one.era && PB.opts.looks[one.era] ? " · " + one.era : "")),
    pbSheet(one) || (one.picture ? el("img", { src: file(one.picture), alt: one.name, style: "width:100%;max-height:160px;object-fit:contain;border-radius:6px;margin-top:6px" }) : null),
    el("div", { class: "row", style: "margin-top:6px" }, el("button", { class: "small fit", onclick: e => { e.stopPropagation(); PB.edit = one.id; PB.characterChoice = one.id; render(); } }, "Edit / describe"),
      el("button", { class: "small fit", disabled: ["still", "turn"].includes((one.sheet || {}).state),
        title: "Use this character's saved appearance and wardrobe for a full-body still, then make a turnaround with H3 Ref2VA",
        onclick: e => { e.stopPropagation(); pbMakeSheet(one); } }, one.sheet && one.sheet.state === "done" ? "Make the sheet again" : "Make a reference sheet"), el("span")));
}

// a character's reference sheet (prompt_builder.make_sheet): where its making stands, then the sheet itself
function pbSheet(one) {
  const sh = one.sheet; if (!sh || !sh.state) return null;
  if (sh.state === "still") return el("div", { class: "note" }, "Reference sheet: making the full-body still…");
  if (sh.state === "turn") return el("div", { class: "note" }, "Reference sheet: turning the still round…");
  if (sh.state === "failed") return el("div", { class: "note", style: "color:var(--warn)" }, "Reference sheet failed: " + (sh.error || "no reason given"));
  return el("div", { style: "margin-top:6px" }, sh.sheet ? el("a", { href: file(sh.sheet), target: "_blank", onclick: e => e.stopPropagation() },
    el("img", { src: file(sh.sheet), alt: one.name + " reference sheet", style: "width:100%;border-radius:6px;background:#fff" })) : null,
    sh.still ? el("a", { class: "note", href: file(sh.still), target: "_blank", onclick: e => e.stopPropagation() }, "the full-body still") : null);
}
async function pbMakeSheet(one) {
  try { await pbSaveCharacter(one);
    const who = PB.cast.find(c => c.id === one.id) || one;
    await api("/api/builder/cast/" + encodeURIComponent(who.id) + "/sheet", {image_model: PB.sheetImageModel});
    PB.edit = null;
    toast("Queued: " + one.name + " reference still, then H3 Ref2VA turnaround. The result appears here.");
    PB.cast = await api("/api/builder/cast"); render(); pbWatch(); } catch (e) { toast(e.message); } }
// while a sheet is being made, the cast is read again now and then so its card moves on by itself
let PB_WATCH = null;
function pbWatch() { if (PB_WATCH) return; PB_WATCH = setInterval(async () => {
  try { PB.cast = await api("/api/builder/cast"); } catch { return; }
  if (!PB.cast.some(c => c.sheet && ["still", "turn"].includes(c.sheet.state))) { clearInterval(PB_WATCH); PB_WATCH = null; }
  if (tab === "Prompt Builder" && !PB.edit) render(); }, 6000); }

// clips made here, newest first: where a character's voice, look and picture can come from
const pbClips = () => (typeof jobs !== "undefined" ? jobs : []).filter(j => ["video", "long"].includes(j.kind) && j.status === "done" && (j.outputs || []).length);
const pbClipPick = () => select(PB.clip, "job", { "": "Pick a clip from the Library", ...Object.fromEntries(pbClips().slice(0, 60).map(j => [j.id, j.title || j.id])) }, () => { PB.clip.found = null; render(); });
// a character's reference picture: the still of their sheet (alone, full body, on grey), else their own picture
const pbRefOf = c => (c.sheet && c.sheet.state === "done" && c.sheet.still) || c.picture || "";

function pbPictureTools(one) {
  const take = async () => { if (!PB.clip.job) return toast("Pick a clip first");
    try { await pbSaveCharacter(one); const r = await api("/api/builder/cast/" + encodeURIComponent(one.id) + "/frame", { job: PB.clip.job, at: Number(PB.clip.at) || 0, part: PB.clip.part });
      one.picture = r.picture; PB.cast = await api("/api/builder/cast"); render(); toast("Their picture is a frame of the clip. Make a reference sheet to put them alone on grey and turn them round."); } catch (e) { toast(e.message); } };
  return el("div", { class: "seg", style: "margin:8px 0" }, el("b", {}, "Their picture"),
    one.picture ? el("img", { src: file(one.picture), alt: one.name, style: "display:block;max-width:220px;max-height:200px;border-radius:6px;margin:6px 0" }) : el("div", { class: "note" }, "None yet: take one from a clip you made, where they are clearly seen."),
    el("div", { class: "row" }, field("Clip", pbClipPick()), field("At (seconds)", bind(PB.clip, "at", el("input", { type: "number", min: 0, step: 0.5, style: "width:90px" }))),
      field("Part of the frame", select(PB.clip, "part", { full: "Whole frame", left: "Left third", middle: "Middle third", right: "Right third", "left-half": "Left half", "right-half": "Right half" }))),
    el("div", { class: "row" }, el("button", { class: "fit", onclick: take }, "Use this frame as their picture"),
      one.picture ? el("button", { class: "fit", onclick: async () => { try {
        if (pbKnown(one)) {
          // Index media is saved independently; clearing requires the detail endpoint.
          await api("/api/builder/character-details", {id:one.id, picture:""}, "PUT");
        } else { delete one.picture; await pbSave(); }
        PB.cast = await api("/api/builder/cast"); render();
      } catch(e) { toast(e.message); } } }, "Remove the picture") : null, el("span")));
}

// the people who speak in a clip made here, offered as characters (voice and look from its prompt)
function pbFromClip() {
  const look = async () => { if (!PB.clip.job) return toast("Pick a clip first");
    try { PB.clip.found = await api("/api/builder/from-clip/" + encodeURIComponent(PB.clip.job)); render();
      if (!PB.clip.found.characters.length) toast("Nobody speaks by name in that clip's prompt"); } catch (e) { toast(e.message); } };
  const add = async (c, i) => { const have = PB.cast.find(x => !pbKnown(x) && x.name.toLowerCase() === c.name.toLowerCase());
    if (have) { for (const k of ["look", "wardrobe", "voice"]) if (!have[k] && c[k]) have[k] = c[k]; }
    else PB.cast.push({ id: "", era: "", ...c });
    await pbSave(); const now = PB.cast.find(x => !pbKnown(x) && x.name.toLowerCase() === c.name.toLowerCase());
    PB.edit = now ? now.id : null; PB.clip.part = ["left", "middle", "right"][i % 3]; render();
    toast(c.name + (have ? " updated" : " added") + ": take their picture from the clip below, then Done"); };
  const found = PB.clip.found;
  return el("details", { class: "seg", style: "margin-top:10px" }, el("summary", { style: "cursor:pointer" }, el("b", {}, "Import a character from a clip you made"), " · their voice and look from its prompt, a frame as their picture"),
    el("div", { class: "row", style: "margin-top:8px" }, field("Clip", pbClipPick()), el("button", { class: "fit", onclick: look }, "Find who speaks"), el("span")),
    found ? el("div", {}, found.characters.map((c, i) => el("div", { class: "row", style: "align-items:center;margin-top:6px" },
      el("div", {}, el("b", {}, c.name), el("div", { class: "note" }, [c.look, c.wardrobe].filter(Boolean).join("; ") || "no look in the prompt"), el("div", { class: "note" }, "Voice: " + (c.voice || "not given"))),
      el("button", { class: "fit small", onclick: () => add(c, i) }, PB.cast.some(x => x.name.toLowerCase() === c.name.toLowerCase()) ? "Fill in the cast's " + c.name : "Add to the cast")))) : null);
}

function pbCastPanel() {
  const imported = el("input", { type: "file", accept: ".json,application/json", onchange: async () => { const f = imported.files[0]; imported.value = ""; if (!f) return;
    try { const list = JSON.parse(await f.text()); if (!Array.isArray(list)) throw new Error("that file is not a cast list");
      const have = new Set(PB.cast.filter(c => !pbKnown(c)).map(c => c.name.toLowerCase()));
      const added = list.filter(c => c && c.name && !have.has(String(c.name).toLowerCase()))
        .map(c => ({ ...c, id: "", catalogue: false, actor: "", franchise: "" }));
      PB.cast = PB.cast.concat(added); await pbSave(); render(); toast(added.length + " custom character(s) imported");
    } catch (e) { toast(e.message); } } });
  const exportCast = () => { const own = PB.cast.filter(c => !pbKnown(c));
    const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify(own, null, 2)], {type:"application/json"})), download: "cast.json" });
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); };
  const wardrobeFile = el("input", {type:"file", accept:".txt,text/plain", onchange:async () => {
    const f = wardrobeFile.files[0]; wardrobeFile.value = ""; if (!f) return;
    try {
      const text = (await f.text()).replace(/\r/g, "");
      const blocks = text.split(/\n\s*\n+/).map(b => b.trim()).filter(Boolean), entries = [];
      for (const block of blocks) {
        const lines = block.split("\n").map(s => s.trim()).filter(Boolean);
        if (lines.length < 2 || /^(here is|dedicated clothing paragraph)/i.test(lines[0])) continue;
        const name = lines.shift().replace(/:$/, "").trim();
        if (!name || name.length > 100 || /[.!?]$/.test(name)) continue;
        entries.push({name, wardrobe:lines.join(" ")});
      }
      if (!entries.length) throw new Error("No character name and clothing paragraphs found in that text file");
      let updated = 0, added = 0;
      for (const entry of entries) {
        const key = entry.name.toLocaleLowerCase();
        let match = PB.cast.find(c => c.name.trim().toLocaleLowerCase() === key);
        if (!match) {
          match = {id:"", name:entry.name, look:"", wardrobe:entry.wardrobe, voice:"", era:""};
          PB.cast.push(match); added++;
        } else { match.wardrobe = entry.wardrobe; updated++; }
        if (pbKnown(match)) await pbSaveCharacter(match);
      }
      await pbSave(); PB.characterChoice = ""; PB.edit = null; render();
      toast(`${updated} existing character(s) updated; ${added} custom character(s) added from ${f.name}`);
    } catch (e) { toast(e.message || String(e)); }
  }});
  const many = PB.pick.length > PB.opts.ensemble_warn;
  const scene = PB.pick.map(id => PB.cast.find(c => c.id === id)).filter(Boolean);
  const active = PB.cast.find(c => c.id === PB.characterChoice);
  const searchInput = el("input", {type:"search", value:PB.characterQuery,
    placeholder:"Search a character, actor or film/series…", autocomplete:"off"});
  const picker = el("select", {title:"Choose a character", onchange: e => {
    PB.characterChoice = e.target.value;
    PB.edit = PB.characterChoice || null;
    render();
  }});
  const count = el("span", {class:"note"});
  const paint = () => {
    const query = PB.characterQuery.trim().toLowerCase();
    const matches = PB.cast.filter(c => !query || [c.name,c.actor,c.franchise].some(v =>
      String(v || "").toLowerCase().includes(query)));
    // Populate the entire matching catalogue: capping the default list at 60 made the
    // alphabetically ordered actor list appear to stop around B. Search still narrows it.
    const visible = matches;
    picker.replaceChildren(el("option", {value:""}, matches.length ? "Choose character / actor…" : "No matches"),
      ...visible.map(c => el("option", {value:c.id}, pbCharacterLabel(c))));
    if (active && !visible.some(c => c.id === active.id)) picker.append(el("option", {value:active.id}, pbCharacterLabel(active)));
    picker.value = PB.characterChoice || "";
    count.textContent = matches.length + " matches" + (query ? " · filtered by your search" : " in the full catalogue");
  };
  searchInput.oninput = e => {PB.characterQuery = e.target.value; paint();};
  paint();
  return el("div", {},
    el("h3", {}, "Cast · " + PB.pick.length + " in this scene"),
    el("div", {class:"note"}, "Search the complete Hugging Face actor/character catalogue and your custom cast. Choose one to open its description box; add it to the scene or make a reference sheet."),
    el("div", {class:"row", style:"margin-top:10px"}, field("Search characters / actors", searchInput),
      field("Choose a character", picker), field("Matches", count)),
    el("div", {class:"row", style:"margin-top:8px"},
      field("Still image model for reference sheets", select(PB, "sheetImageModel", {
        studio:"Studio default (original behaviour)", qwen:"Qwen Image", "h3-image":"MiniMax H3-Image"
      })),
      el("span", {class:"note"}, "Reference sheet turnaround: H3 Ref2VA. H3-Image requires installed model weights and the appropriate licence.")),
    active && !scene.some(c => c.id === active.id) ? pbCharacter(active, PB.cast.indexOf(active)) : null,
    scene.length ? el("div", {}, el("h4", {style:"margin:14px 0 5px"}, "Selected characters"),
      el("div", {class:"grid", style:"margin-top:8px"}, scene.map(c => pbCharacter(c, PB.cast.indexOf(c))))) :
      el("div", {class:"note", style:"margin-top:12px"}, "No characters in this scene. Search above and click Add to scene."),
    many ? el("div", {class:"note", style:"color:var(--warn)"}, "MiniMax H3 may confuse characters when more than " + PB.opts.ensemble_warn + " appear in the same shot.") : null,
    el("div", {class:"row", style:"margin-top:10px"},
      el("button", {class:"fit", onclick: async () => { const one = {id:"", name:"New character", look:"", wardrobe:"",voice:"",era:""};
        PB.cast.unshift(one); try {await pbSave(); const added = PB.cast.find(c => !pbKnown(c) && c.name === "New character");
          PB.characterChoice = added?.id || ""; PB.edit = PB.characterChoice; render();}
        catch(e){toast(e.message);} }}, "+ New character"),
      el("button", {class:"fit", onclick:exportCast}, "Export custom cast"),
      field("Import a custom cast (.json)", imported), field("Import clothing descriptions (.txt)", wardrobeFile)),
    pbFromClip());
}


// The Studio creates RefMods from library media. For video, server.py expects
// C.refmods = [{name, strength}], then validates saved names and the per-clip limit.
function pbRefModsPanel() {
  const names = PB.refmodsAvailable;
  const chosen = PB.refmods;
  const input = el("input", {type:"search", value:PB.refmodQuery,
    placeholder:"Search saved RefMods…", autocomplete:"off"});
  const picker = el("select", {title:"Choose a saved RefMod", onchange:e => {
    PB.refmodChoice = e.target.value;
  }});
  const paint = () => {
    const query = PB.refmodQuery.trim().toLowerCase();
    const visible = names.filter(m => m.name.toLowerCase().includes(query));
    picker.replaceChildren(el("option", {value:""}, visible.length ? "Choose RefMod…" : "No matching RefMods"),
      ...visible.map(m => el("option", {value:m.name}, m.name)));
    picker.value = visible.some(m => m.name === PB.refmodChoice) ? PB.refmodChoice : "";
  };
  input.oninput = e => {PB.refmodQuery = e.target.value; paint();};
  paint();
  const strength = el("input", {type:"number", min:"0.01", max:"1", step:"0.05",
    value:String(PB.refmodStrength), style:"width:95px"});
  strength.oninput = e => {PB.refmodStrength = e.target.value;};
  const add = () => {
    const name = PB.refmodChoice;
    if (!name || !names.some(m => m.name === name)) return toast("Choose a saved RefMod first");
    if (chosen.some(m => m.name === name)) return toast("That RefMod is already selected");
    if (chosen.length >= PB.refmodsLimit) return toast("Maximum " + PB.refmodsLimit + " RefMods per clip");
    const value = Number(PB.refmodStrength);
    if (!Number.isFinite(value) || value <= 0 || value > 1) return toast("Strength must be between 0.01 and 1.00");
    PB.refmods = [...chosen, {name, strength:Math.round(value * 100) / 100}];
    PB.refmodChoice = ""; render();
  };
  return el("div", {class:"seg", style:"margin-top:12px"},
    el("h3", {}, "Use saved RefMods · " + chosen.length + (PB.refmodsLimit ? " / " + PB.refmodsLimit : "")),
    el("div", {class:"note"}, "Choose RefMods you already created in the Studio Library. They are applied to the MiniMax H3 Ref2VA video during rendering, not to Qwen/H3-Image stills or to the reference-sheet generator."),
    el("div", {class:"row", style:"margin-top:8px"},
      field("Find a RefMod", input), field("Saved RefMod", picker),
      field("Strength (0.01–1)", strength),
      el("button", {class:"fit", onclick:add, disabled:!PB.refmodsLoaded || !!PB.refmodsError || !names.length}, "Add RefMod"),
      el("button", {class:"fit", onclick:()=>pbRefreshRefMods(), title:"Refresh after making a new RefMod in the Library"}, "Refresh list")),
    PB.refmodsError ? el("div", {class:"note", style:"color:var(--warn)"}, "RefMods unavailable: " + PB.refmodsError) :
      (!PB.refmodsLoaded ? el("div", {class:"note"}, "Loading saved RefMods…") :
       !names.length ? el("div", {class:"note"}, "No registered RefMods found.") : null),
    PB.refmodsFolder ? el("div", {class:"note", style:"margin-top:6px"},
      "Studio RefMod folder: " + PB.refmodsFolder +
      (PB.refmodsFolderEntries == null ? "" : " · " + PB.refmodsFolderEntries + " item(s) in folder")) : null,
    PB.refmodsNote ? el("div", {class:"note", style:"color:var(--warn);margin-top:5px"}, PB.refmodsNote) : null,
    chosen.length ? el("div", {style:"margin-top:8px"}, chosen.map(one =>
      el("div", {class:"row", style:"align-items:center;margin-top:5px"},
        el("b", {}, one.name),
        field("Strength", el("input", {type:"number", min:"0.01", max:"1", step:"0.05",
          value:String(one.strength), style:"width:95px", onchange:e=>{
            const value = Number(e.target.value);
            if (!Number.isFinite(value) || value <= 0 || value > 1) {
              toast("Strength must be between 0.01 and 1.00"); e.target.value = String(one.strength); return;
            }
            one.strength = Math.round(value * 100) / 100;
          }})),
        el("button", {class:"small fit", onclick:()=>{PB.refmods=PB.refmods.filter(x=>x.name!==one.name);render();}}, "Remove"),
        el("span")))) : null,
    chosen.length ? el("div", {class:"note", style:"margin-top:6px"}, "These RefMods are attached when you send the prompt to Create. Check the selected RefMods and strengths before rendering; RefMods affect the scene as a whole, not a guaranteed one-to-one actor assignment.") : null);
}

async function pbWrite(brain) {
  if (PB.busy) return; PB.busy = true; PB.said = brain ? "The brain is writing the prompt…" : ""; render();
  try {
    const r = await api("/api/builder/write", { brain, cast: PB.pick, scenario: PB.scenario, location: PB.location, time: PB.time, mood: PB.mood,
      look: PB.look, shots: PB.shots, seconds: Number(PB.seconds), language: PB.language, idea: PB.idea });
    PB.prompt = r.prompt; PB.by = r.by;
    PB.said = r.error ? "The brain did not write it (" + r.error + "), so here is the layout from your choices to fill in. Set a brain under Machine → Brains & tools." : r.by === "brain" ? "Written by the brain in " + Math.round(r.elapsedMs / 1000) + " s. Edit it freely." : "The layout from your choices: write the lines and what happens.";
  } catch (e) { PB.said = e.message; }
  PB.busy = false; render();
}

// into Create: a new video clip from text, its boxes replaced by the prompt's
async function pbSend() {
  let r; try { r = await api("/api/builder/fields", { prompt: PB.prompt }); } catch (e) { return toast(e.message); }
  const f = r.fields, found = r.found;
  // a line spoken by someone in the cast keeps their saved voice, so H3 hears the same person in every line and clip;
  // the line's own note ("flat", "grinning") stays after it
  const byName = Object.fromEntries(PB.cast.map(c => [c.name.trim().toLowerCase(), c]));
  for (const d of found.dialogue) { const who = byName[(d.speaker || "").trim().toLowerCase()]; if (!who || !who.voice) continue;
    // only the words the note adds: "flat and gravelly" beside a gravelly voice is "flat"
    const has = new Set(who.voice.toLowerCase().match(/[a-z'-]+/g) || []), filler = new Set(["a", "an", "the", "and", "with", "voice", "in", "speaking", "of"]);
    const adds = ((d.voice || "").replace(/^a voice speaking\s+/i, "").toLowerCase().match(/[a-z'-]+/g) || []).filter(w => !filler.has(w) && !has.has(w));
    d.voice = who.voice + (adds.length ? ", " + adds.join(" and ") : ""); }
  // with their pictures as references (MiniMax H3 Ref2VA): <Picture n> is <Subject n>, and a line goes to its speaker's
  // subject; a speaker without a picture is a further subject of their own. One clip, 15 s at most.
  const cast = PB.pick.map(id => PB.cast.find(c => c.id === id)).filter(Boolean), pictured = pbRefsOn() ? cast.filter(pbRefOf) : [];
  // Per-character associations are added when that character is in this scene. RefMods
  // remain clip-wide at render time; this records which saved RefMod belongs with a role.
  const byRefmod = new Map(PB.refmods.map(m => [m.name, {name:m.name, strength:Number(m.strength)}]));
  for (const c of cast) if (c.refmod) {
    const m = PB.refmodsAvailable.find(x => x.name === c.refmod);
    if (!m) return toast(`${c.name}'s saved RefMod is missing from the Library. Edit the character or refresh RefMods.`);
    if (!byRefmod.has(m.name)) byRefmod.set(m.name, {name:m.name, strength:Number(c.refmod_strength) || 1});
  }
  const selectedRefmods = [...byRefmod.values()];
  if (selectedRefmods.length && !(S && S.ref2va)) return toast("RefMods need MiniMax H3 Ref2VA installed and enabled in Create");
  if (selectedRefmods.length && !PB.refmodsLoaded) return toast("Load the RefMod library before sending");
  if (selectedRefmods.length && PB.refmodsError) return toast("Cannot verify RefMods: " + PB.refmodsError);
  if (PB.refmodsLimit && selectedRefmods.length > PB.refmodsLimit)
    return toast("This scene has " + selectedRefmods.length + " different RefMods assigned; the per-clip limit is " + PB.refmodsLimit + ". Remove an assignment or global RefMod.");
  if (selectedRefmods.some(m => !PB.refmodsAvailable.some(x => x.name === m.name)))
    return toast("A selected RefMod is missing from the Library. Refresh the RefMods list.");
  Object.assign(C, { goes: "video", start: (pictured.length || selectedRefmods.length) ? "refs" : (C.start === "refs" ? "text" : C.start), scene: f.scene || "", action: f.action || "", camera: f.camera || "",
    dialogue: found.dialogue, soundscape: found.soundscape || "", music: found.music || "N/A",
    refmods: selectedRefmods, prompt: "", parts: null, job: null });
  if (pictured.length) {
    Object.assign(C, { refs: pictured.map(pbRefOf), subjects: pictured.map(c => [c.name, c.look, c.wardrobe ? "wearing " + c.wardrobe : ""].filter(Boolean).join(", ")),
      refVideos: [], video_subjects: [], refAudios: [], audio_notes: [], video_sound: false, motion: null });
    const number = {}; pictured.forEach((c, i) => { number[c.name.toLowerCase()] = i + 1; });
    let next = pictured.length;
    for (const d of C.dialogue) { const key = (d.speaker || "").trim().toLowerCase(); if (!key) continue; if (!number[key]) number[key] = ++next; d.subject = number[key]; }
  } else if (selectedRefmods.length) {
    // With only RefMods, keep Create's reference mode but clear reference pictures from a previous scene.
    Object.assign(C, {refs:[], subjects:[], refVideos:[], video_subjects:[], refAudios:[], audio_notes:[], video_sound:false, motion:null});
  }
  const want = found.seconds || Number(PB.seconds);
  C.seconds = fitLength(want); C.frames = null; sync();
  C.beats = isLong() ? (f.beats || []).join("\n") : "";
  C.step = 2; go("Create");
  toast(found.dialogue.length + " line" + (found.dialogue.length === 1 ? "" : "s") + " and the scene sent to Create · " + (S.long[C.seconds] || C.seconds + " s") + (isLong() ? ", " + Math.ceil(C.seconds / 15) + " clips joined into one video" : "")
    + (pictured.length ? " · " + pictured.length + " character picture" + (pictured.length === 1 ? "" : "s") + " as references" + (want > 15 ? " (one clip of 15 s: references are for single clips)" : "") : "")
    + (selectedRefmods.length ? " · " + selectedRefmods.length + " saved RefMod" + (selectedRefmods.length === 1 ? "" : "s") + " attached" : "")
    + ": check the voices and RefMods, then step 4 and Render");
}

// the characters' pictures as references: offered when Ref2VA is here and someone in the scene has a picture
const pbRefsOn = () => PB.refs && !!(S && S.ref2va);
function pbRefsChoice() {
  const cast = PB.pick.map(id => PB.cast.find(c => c.id === id)).filter(Boolean), with_ = cast.filter(pbRefOf);
  if (PB.refmods.length && !with_.length) return el("div", {class:"note", style:"margin-top:6px"},
    PB.refmods.length + " saved RefMod(s) selected for the video. You can add character pictures separately if you want reference images too.");
  if (!(S && S.ref2va) || !with_.length) return cast.length ? el("div", { class: "note", style: "margin-top:6px" }, "To keep the characters looking the same from clip to clip, give them a picture (Edit → Their picture) or a reference sheet: then they can go in as references.") : null;
  return el("label", { style: "display:flex;gap:6px;align-items:center;margin-top:8px" }, bind(PB, "refs", el("input", { type: "checkbox", style: "width:auto" })),
    el("span", {}, "Keep their looks: send " + with_.map(c => c.name).join(", ") + "'s picture" + (with_.length === 1 ? "" : "s") + " as references (MiniMax H3 Ref2VA). That is one clip of 15 s at most" + (cast.length > with_.length ? "; " + cast.filter(c => !pbRefOf(c)).map(c => c.name).join(", ") + " without a picture stay described in words" : "") + "."));
}

function viewBuilder() {
  const box = el("section", { class: "card" }, el("h2", {}, "Prompt Builder"),
    el("div", { class: "note" }, "A MiniMax H3 prompt from your own characters and a few choices, written by the Studio's brain, then sent into Create (step 3, Words / Sound) to render as one video. Over 15 s it renders as chained clips joined into one file."));
  if (!PB.opts || !PB.cast) { pbLoad().catch(e => toast(e.message)); return put(box, el("div", { class: "note" }, "Loading…")), box; }
  const lengths = Object.fromEntries([...Object.keys(S.durations).map(Number).filter(n => n >= 5), ...Object.keys(S.long || {}).map(Number).filter(n => n <= 120)].map(n => [n, S.long[n] || n + " s"]));
  const area = bind(PB, "prompt", el("textarea", { style: "min-height:320px;font-family:ui-monospace,monospace;font-size:13.5px", placeholder: "The prompt appears here. You can also paste or write one yourself." }));
  put(box, pbCastPanel(), pbRefModsPanel(),
    el("h3", {}, "The scene"),
    el("div", { class: "row" }, pbPick("scenario", "Scenario", PB.opts.scenarios, "pick one or write your own"), pbPick("location", "Place", PB.opts.locations, "pick one or write your own")),
    el("div", { class: "row" }, pbPick("time", "Time of day", PB.opts.times, "night"), pbPick("mood", "Mood", PB.opts.moods, "tense"),
      field("Film look", select(PB, "look", pbLooks()))),
    el("div", { class: "row" }, field("Length", select(PB, "seconds", lengths)), field("Shots", select(PB, "shots", PB.opts.shots)),
      field("Language of the lines", bind(PB, "language", el("input", { placeholder: "English" })))),
    field("What happens (a sentence or two; the brain writes the shots and lines)", bind(PB, "idea", el("textarea", { style: "min-height:60px", placeholder: "They meet at the diner to split the money, and it goes wrong when one of them counts it twice." }))),
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "hot fit", disabled: PB.busy, onclick: () => pbWrite(true) }, PB.busy ? "Writing…" : "Write with the brain"),
      el("button", { class: "fit", disabled: PB.busy, onclick: () => pbWrite(false) }, "Lay it out without a brain"), el("span")),
    PB.said ? el("div", { class: "note", style: "margin-top:6px" }, PB.said) : null,
    el("h3", {}, "The prompt"), area,
    el("div", { class: "row", style: "margin-top:8px" },
      el("button", { class: "primary fit", disabled: !PB.prompt.trim(), onclick: pbSend }, "Send to 3. Words / Sound"), el("span")),
    pbRefsChoice());
  return box;
}
