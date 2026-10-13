"use strict";
/* Render nodes: other machines that run this studio and take jobs from this queue (app/nodes.py). */
const NODES = { data: null, open: false, form: { url: "", token: "" } };
async function nodesLoad() { try { NODES.data = await api("/api/nodes"); } catch { NODES.data = null; } nodesDraw(); }
async function nodesDo(path, body, method) { try { await api(path, body, method); } catch (e) { toast(e.message, 6000); } nodesLoad(); }
function nodesDraw() { const box = document.querySelector("#nodes"); if (!box) return; const d = NODES.data;
  if (!d) { box.replaceChildren(); return; }
  const dot = s => el("span", { style: "display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:" + ({ free: "#4ade80", rendering: "#fbbf24", busy: "#fbbf24", draining: "#94a3b8", offline: "#f87171" }[s] || "#64748b") });
  const row = n => el("div", { class: "note", style: "display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px" }, el("span", { style: "flex:1;min-width:0", title: n.url + (n.why ? " · " + n.why : "") }, dot(n.state), n.name + " · " + n.state + (n.memory_gb != null ? " · " + n.memory_gb + " GB free" : "") + (n.why ? " · " + n.why : "")),
    el("button", { class: "small", title: n.drain ? "Give this node jobs again" : "No new jobs for this node; the one it renders finishes", onclick: () => nodesDo("/api/nodes/" + encodeURIComponent(n.name), { drain: !n.drain }) }, n.drain ? "resume" : "drain"),
    el("button", { class: "small", title: "Take this node off the list", onclick: () => confirm("Remove " + n.name + " from the render nodes?") && nodesDo("/api/nodes/" + encodeURIComponent(n.name), undefined, "DELETE") }, "remove"));
  const head = el("div", { class: "note", style: "margin-top:10px;cursor:pointer", onclick: () => { NODES.open = !NODES.open; nodesDraw(); } },
    (NODES.open ? "▾ " : "▸ ") + "Render nodes: " + (d.nodes.length ? d.nodes.length + " besides this machine, " + d.nodes.filter(n => n.state === "free" || n.state === "rendering").length + " working" : "this machine only"));
  if (!NODES.open) { box.replaceChildren(head); return; }
  const url = el("input", { placeholder: "http://10.0.0.5:7870", value: NODES.form.url, oninput: e => NODES.form.url = e.target.value });
  const token = el("input", { placeholder: "the node's token", value: NODES.form.token, oninput: e => NODES.form.token = e.target.value });
  box.replaceChildren(head,
    el("div", { class: "note" }, "A node is another machine running this studio (on a DGX Spark: spark/setup-worker.sh). Each machine renders one job at a time, so two machines finish two queued jobs in the time of one. A job a node cannot take, or loses, comes back to the queue."),
    el("label", { class: "note", style: "display:flex;gap:6px;align-items:center;margin-top:6px" }, el("input", { type: "checkbox", style: "width:auto;flex:none", checked: d.local.render, onchange: e => nodesDo("/api/nodes/local", { render: e.target.checked }) }), "This machine (" + d.local.name + ") renders too"),
    ...d.nodes.map(row),
    el("div", { class: "row", style: "margin-top:8px" }, url, token, el("button", { class: "fit", onclick: async () => { await nodesDo("/api/nodes", { url: NODES.form.url, token: NODES.form.token }); NODES.form = { url: "", token: "" }; } }, "Add node")));
}
setInterval(() => { if (document.querySelector("#nodes")) nodesLoad(); }, 5000);
window.addEventListener("load", () => setTimeout(nodesLoad, 600));
