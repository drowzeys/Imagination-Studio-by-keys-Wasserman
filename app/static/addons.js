"use strict";
// Loaded after the built-in modules, registered before the first navigation/render.
const StudioAddons = (() => {
  const views = new Map(), agents = new Map();
  function register({ rooms = [], tabs = [] } = {}) {
    const ids = new Set(ROOMS.map(r => r.id)), names = new Set(TABS);
    for (const r of rooms) {
      if (!/^[a-z][a-z0-9_-]{0,47}$/.test(r.id) || ids.has(r.id) || !GROUPS.some(g => g.id === r.group) || !r.name || !Array.isArray(r.tabs) || !r.tabs.length)
        throw new Error("Invalid or duplicate add-on room");
      ids.add(r.id);
    }
    for (const t of tabs) {
      if (typeof t.id !== "string" || !t.id.trim() || names.has(t.id) || typeof t.view !== "function" || !ids.has(t.room))
        throw new Error("Invalid or duplicate add-on tab");
      names.add(t.id);
    }
    if (rooms.some(r => r.tabs.some(id => !tabs.some(t => t.id === id && t.room === r.id)))) throw new Error("An add-on room needs registered tabs");
    for (const r of rooms) { ROOMS.push({ key: "", says: "", ...r, tabs: [...r.tabs] }); ICON[r.id] = ICON.agent; }
    for (const t of tabs) {
      TABS.push(t.id); TAB_NAMES[t.id] = t.label || t.id; views.set(t.id, t.view);
      const room = ROOMS.find(r => r.id === t.room); if (!room.tabs.includes(t.id)) room.tabs.push(t.id);
    }
  }
  function addAgent(a) {
    if (agents.has(a.id) || AG_AGENTS.some(([id]) => id === a.id)) throw new Error("Duplicate add-on agent");
    agents.set(a.id, a); AG_AGENTS.push([a.id, a.label]); CN_ORDER.push(a.id); if (a.harness) AG_HARNESS.push(a.id);
  }
  async function load() {
    try {
      for (const addon of await api("/api/addons")) {
        for (const a of addon.agents) addAgent(a);
        if (addon.script) await new Promise(resolve => {
          const script = document.createElement("script"); script.src = addon.script;
          script.onload = script.onerror = resolve; document.head.append(script);
        });
      }
    } catch (error) { console.warn("Local add-ons could not be loaded", error); }
  }
  return { register, load, view: name => views.get(name), agents: () => [...agents.values()] };
})();
