/* Self-contained update controls. No reload until the user chooses it: unsaved creative work stays on screen. */
(() => {
  "use strict";
  const byId = id => document.getElementById("studio-updates-" + id);
  const dialog = document.getElementById("studio-updates");
  let waiting = false, acting = false, before = null;
  const request = async (action, body = {}) => {
    const response = await fetch("/api/updates" + (action ? "/" + action : ""), action ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    } : {});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.detail || "Could not reach Studio updates.");
    return data;
  };
  function draw(data) {
    if (before && before.session && data.session && before.session !== data.session) waiting = true;
    byId("status").textContent = data.message;
    byId("source").textContent = data.source ? "Updates from " + data.source + " · " + data.branch +
      (data.latest_version ? " · Latest " + data.latest_version : "") : "";
    byId("result").textContent = data.last_result ? data.last_result.message : "";
    byId("check").disabled = acting || data.checking || data.updating;
    byId("check").textContent = data.checking ? "Checking…" : "Check for updates";
    byId("install").disabled = acting || !data.available || !data.managed || data.updating || data.checking;
    byId("auto").checked = data.automatic;
    byId("auto").disabled = acting || data.updating || (!data.managed && !data.automatic);
    if (!data.managed) byId("result").textContent = "Start this installation with scripts/app.sh to install updates. Worker Sparks update through the Sparks tab.";
    if (data.updating) waiting = true;
    if (waiting && !data.updating) {
      byId("status").textContent = "Studio is back online. Reload when you have saved your work.";
      const reload = document.createElement("button");
      reload.type = "button"; reload.textContent = "Reload Studio";
      reload.onclick = () => location.reload();
      byId("status").append(" ", reload);
    }
    before = data;
  }
  async function refresh() {
    if (acting || (!dialog.open && !waiting)) return;
    try { draw(await request()); }
    catch (error) { byId("status").textContent = waiting ? "Studio is restarting. Waiting to reconnect…" : error.message; }
  }
  async function action(name, body) {
    if (acting) return;
    acting = true;
    for (const id of ["check", "install", "auto"]) byId(id).disabled = true;
    try {
      const data = await request(name, body);
      if (name === "install") waiting = true;
      acting = false;
      draw(data);
    } catch (error) {
      acting = false;
      if (before) draw(before);
      byId("status").textContent = error.message;
    }
  }
  byId("open").onclick = () => { dialog.showModal(); refresh(); };
  byId("close").onclick = () => dialog.close();
  byId("check").onclick = () => action("check");
  byId("install").onclick = () => action("install");
  byId("auto").onchange = event => action("settings", { automatic: event.target.checked });
  setInterval(refresh, 2000);
})();
