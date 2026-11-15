"use strict";
// Parser-blocking bootstrap: a local language runs before any built-in UI module.
// English (including browsers without storage) does not request a language file.
const StudioLanguage = (() => {
  const key = "studio-language", valid = id => typeof id === "string" && id.length <= 48 && /^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$/.test(id);
  let selected = "en", choices = [{ id: "en", label: "English" }];
  try { const saved = localStorage.getItem(key); if (valid(saved)) selected = saved.toLowerCase() === "en" ? "en" : saved; } catch {}
  function choose(id) {
    if (!choices.some(c => c.id === id)) return;
    try { localStorage.setItem(key, id); } catch { return; }
    location.reload();
  }
  function fail() {
    if (selected === "en") return;
    selected = "en";
    try { localStorage.removeItem(key); } catch {}
    // A failing script may have changed globals already: start a clean English page.
    location.reload();
  }
  async function load() {
    try { choices = await api("/api/languages"); } catch { choices = [{ id: "en", label: "English" }]; }
  }
  return { current: () => selected, choices: () => choices, choose, fail, load };
})();
if (StudioLanguage.current() !== "en") {
  const path = "/api/languages/" + encodeURIComponent(StudioLanguage.current()) + ".js";
  window.addEventListener("error", event => { if (event.filename && new URL(event.filename, location.href).pathname === path) StudioLanguage.fail(); });
  document.write('<script src="' + path + '" onerror="StudioLanguage.fail()"><\/script>');
}
