"use strict";
// The trash of whole films (Production, Cast & Places, the Director), under the Library: what was moved there is
// listed and put back. Moving one there is on the film picker of the rooms (rooms.js).
const TB = { films: null, open: false, asked: false, ask: 0 };
async function tbLoad() {
  const ask = ++TB.ask;  // an answer to an older ask (one sent before a film was moved or put back) is dropped
  let films = [];
  try { const got = await api("/api/slate/trash"); films = Array.isArray(got) ? got : []; } catch {}
  if (ask !== TB.ask) return;
  TB.films = films;
  if (typeof render === "function") render();
}
const tbWhen = seconds => { try { return new Date(seconds * 1000).toLocaleString(); } catch { return ""; } };
function tbPanel() {
  if (!TB.asked) { TB.asked = true; tbLoad(); }
  const items = TB.films || [];
  if (!items.length) return null;
  return el("section", { class: "card", "data-trashbin": true, style: "margin-top:12px" },
    el("button", { type: "button", class: "small ghost", "data-trashbin-open": true, onclick: () => { TB.open = !TB.open; render(); } },
      TB.open ? "▾ " : "▸ ", "Films in the trash (" + items.length + ")"),
    TB.open ? el("div", { class: "stack", style: "margin-top:8px" }, items.map(one => el("div", { class: "row", style: "align-items:center", "data-trashed": one.folder },
      el("b", {}, one.name || "Untitled"), el("span", { class: "note m-flat" }, tbWhen(one.removed)), el("span", { style: "flex:1" }),
      el("button", { type: "button", class: "small", "data-put-back": one.folder, onclick: () => tbRestore(one) }, "Put back")))) : null);
}
async function tbRestore(one) {
  try {
    await api("/api/slate/trash/" + encodeURIComponent(one.folder) + "/restore", {});
    toast("Put back: " + (one.name || "Untitled"));
    tbForget();
  } catch (e) { toast(e.message); }
  await tbLoad();
}
// what the other pages keep of the films is read again after one is moved or put back
function tbForget() {
  if (typeof RM !== "undefined") { RM.films = null; RM.asked = false; RM.failed = ""; RM.ask = (RM.ask || 0) + 1; }
  if (typeof SL !== "undefined") SL.metas = null;  // Production's own list of the films
  TB.asked = false; TB.ask++;  // and what an ask still on its way brings back is not kept
}
