"use strict";
// Display translation only. The upstream language hook owns selection, loading and fallback.
// Prompts, input values, code and request payloads remain in their original language.
(function () {
  const on = typeof StudioLanguage === "undefined" || StudioLanguage.current() === "zh-TW";
  const data = window.STUDIO_ZH || { dict: {}, patterns: [] };
  const dict = new Map(Object.entries(data.dict || {}));
  const norm = s => s.replace(/\s+/g, " ").trim();
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Patterns: "Rendering {0} of {1}" -> /^Rendering (.+?) of (.+?)$/ ; the captured values are translated in turn.
  // OSSLab: "{0:raw}" is a value shown as it is (a name, a model id, a lyric tag), never translated; "{0:d}" matches
  // a number only ("3", "1.2"), so "{0:d} files" leaves "Open the files" someone wrote alone.
  const patterns = [];
  for (const [en, zh] of data.patterns || []) {
    const parts = en.split(/\{(\d+)(?::(raw|d))?\}/), order = [], kinds = [];
    let src = "^";
    for (let i = 0; i < parts.length; i += 3) {
      src += esc(parts[i]).replace(/ +/g, "\\s+");
      if (i + 1 < parts.length) { order.push(+parts[i + 1]); kinds.push(parts[i + 2] || ""); src += parts[i + 2] === "d" ? "(\\d+(?:[.,]\\d+)*)" : "([\\s\\S]+?)"; }
    }
    if (!order.length) continue;
    const need = parts.filter((p, i) => i % 3 === 0).map(norm).sort((a, b) => b.length - a.length)[0] || "";
    try { patterns.push({ re: new RegExp(src + "$"), order, kinds, zh, need, fixed: en.replace(/\{\d+(?::(?:raw|d))?\}/g, "").length }); } catch (e) {}
  }
  patterns.sort((a, b) => b.fixed - a.fixed);

  // Fragments: phrases of two or more words, and capitalised names (rooms, tools), found inside longer text that the
  // code built by concatenation. Matching runs against `core = norm(text)`, where whitespace runs are already single
  // spaces, so the alternation uses literal spaces instead of `\s+` quantifiers (avoiding a 9–17 s V8 Irregexp stall).
  const frags = [...dict.keys()].filter(k => k.length >= 6 && /[A-Za-z]{2,}[^A-Za-z]+[A-Za-z]{2,}/.test(k) || /^[A-Z][a-z]{3,}$/.test(k))
    .sort((a, b) => b.length - a.length);
  let fragRe = null;  // a browser without regex lookbehind (Safari before 16.4) keeps exact and pattern matches only
  try { if (frags.length) fragRe = new RegExp("(?<![A-Za-z])(?:" + frags.map(s => esc(norm(s))).join("|") + ")(?![A-Za-z])", "g"); } catch (e) {}

  const cache = new Map();
  function tr(text, depth = 0) {
    if (!text || !/[A-Za-z]{2,}/.test(text)) return text;
    const hit = cache.get(text); if (hit !== undefined) return hit;
    const lead = text.match(/^\s*/)[0], tail = text.match(/\s*$/)[0], core = norm(text);
    let out = dict.get(core);
    if (out === undefined && depth < 2) for (const p of patterns) {
      if (p.need && !core.includes(p.need)) continue;
      const m = core.match(p.re); if (!m) continue;
      out = p.zh.replace(/\{(\d+)\}/g, (_, n) => { const i = p.order.indexOf(+n); return i < 0 ? "" : p.kinds[i] ? m[i + 1] : tr(m[i + 1], depth + 1); });
      break;
    }
    if (out === undefined && fragRe && depth < 2) {
      // only when most of the English is covered: a sentence with one known word in it stays English, not half-Chinese
      const words = s => (s.match(/[A-Za-z]{2,}/g) || []).length, total = words(core); let covered = 0;
      const swapped = core.replace(fragRe, m => { const z = dict.get(norm(m)); if (z === undefined) return m; covered += words(m); return z; })
        .replace(/([\u3400-\u9fff\u3000-\u303f\uff00-\uffef])\s+(?=[\u3400-\u9fff\u3000-\u303f\uff00-\uffef])/g, "$1");
      if (swapped !== core && covered * 2 >= total) out = swapped;
    }
    const res = out === undefined ? text : lead + out + tail;
    if (cache.size > 20000) cache.clear();
    cache.set(text, res);
    return res;
  }
  window.studioTr = tr;  // for the console and tests

  if (!on) { window.STUDIO_LANG = "en"; return; }
  window.STUDIO_LANG = "zh-TW";
  document.documentElement.lang = "zh-Hant-TW";

  const ATTRS = ["title", "aria-label", "placeholder", "alt", "aria-description"];
  const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "CODE", "KBD", "PRE", "SAMP", "NOSCRIPT", "svg"]);
  const done = new WeakMap();  // text node -> the translation this script wrote into it
  function skipped(el) {
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      if (SKIP.has(e.nodeName) || e.isContentEditable || e.getAttribute && ["", "true", "plaintext-only"].includes(e.getAttribute("contenteditable")) || e.hasAttribute && e.hasAttribute("data-no-tr")) return true;
    }
    return false;
  }
  function textNode(n) {
    const v = n.nodeValue;
    if (done.get(n) === v || !/[A-Za-z]{2,}/.test(v)) return;
    if (n.parentElement && skipped(n.parentElement)) return;
    const t = tr(v);
    done.set(n, t);
    // An <option> without a value is read back by its text: keep the English as its value.
    const p = n.parentElement;
    if (t !== v && p && p.nodeName === "OPTION" && !p.hasAttribute("value")) p.setAttribute("value", norm(v));
    if (t !== v) n.nodeValue = t;
  }
  function attrs(el) {
    if (!el.getAttribute || el.closest && el.closest("[data-no-tr]")) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || !/[A-Za-z]{2,}/.test(v)) continue;
      const t = tr(v); if (t !== v) el.setAttribute(a, t);
    }
  }
  function walk(root) {
    if (root.nodeType === 3) { textNode(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 11) return;
    if (root.nodeType === 1) { if (SKIP.has(root.nodeName) && root.nodeName !== "TEXTAREA") return; attrs(root); }
    const it = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: n => n.nodeType === 1 && (SKIP.has(n.nodeName) && n.nodeName !== "TEXTAREA") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    for (let n = it.nextNode(); n; n = it.nextNode()) { if (n.nodeType === 3) textNode(n); else attrs(n); }
  }
  let titleBusy = false;
  function title() { if (titleBusy) return; const t = tr(document.title); if (t !== document.title) { titleBusy = true; document.title = t; titleBusy = false; } }

  // Dialogs the studio opens with the browser's own boxes.
  for (const name of ["alert", "confirm", "prompt"]) {
    const orig = window[name];
    if (typeof orig !== "function") continue;
    window[name] = function (msg, ...rest) { return orig.call(window, typeof msg === "string" ? tr(msg) : msg, ...rest); };
  }

  const queue = new Set(); let pending = false;
  function collect(list) {
    for (const m of list) {
      if (m.type === "childList") m.addedNodes.forEach(n => queue.add(n));
      else queue.add(m.target);
    }
  }
  function flush() {
    pending = false;
    // disconnect() throws away records not yet handed over: take them first (the page often changes again in a
    // promise between the observer's call and this one)
    collect(obs.takeRecords());
    const items = [...queue]; queue.clear();
    obs.disconnect();
    try { for (const n of items) if (n.isConnected) walk(n); title(); }
    finally { obs.observe(document.documentElement, OPTS); }
  }
  const OPTS = { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS };
  const obs = new MutationObserver(list => {
    collect(list);
    // Translate before the next paint, so English does not flash.
    if (!pending) { pending = true; queueMicrotask(flush); }
  });
  // The tour's spot() finds a control by the start of its English label: look for the translated label instead.
  function wrapSpot() {
    const orig = window.spot;
    if (typeof orig !== "function" || orig.zh) return;
    const w = function (text, ...rest) { return orig.call(this, typeof text === "string" ? tr(text) : text, ...rest); };
    w.zh = true; window.spot = w;
  }
  function start() { walk(document.documentElement); title(); obs.observe(document.documentElement, OPTS); wrapSpot(); }
  if (document.readyState === "loading") {
    obs.observe(document.documentElement, OPTS);
    document.addEventListener("DOMContentLoaded", () => { obs.takeRecords(); obs.disconnect(); start(); }, { once: true });
  } else start();

})();
