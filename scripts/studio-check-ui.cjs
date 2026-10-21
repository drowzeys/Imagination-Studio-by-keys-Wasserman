#!/usr/bin/env node
// Browser sweep of the web app: every tab, every visible button in it, with nothing written to the app.
//
//   PLAYWRIGHT=/path/to/node_modules/playwright-core node scripts/studio-check-ui.cjs http://127.0.0.1:7870 [outdir]
//
// For each tab of TABS (index.html) it opens #Tab, records console errors, page errors, failed requests and broken
// media, then clicks every visible button of the view one by one. Every request that is not a GET is answered 418 by
// the sweep itself (recorded as "would send"), so a click never queues a job, saves a setting or deletes a file; a
// confirm() is dismissed and a file chooser left empty. A page error raised by a click is a broken control.
// Writes <outdir>/ui.json and one screenshot a tab; exits 1 when a tab has a page error or a broken control.
//
// Needs playwright-core and a Chromium: PLAYWRIGHT (a playwright-core folder) and CHROME (its executable) override
// the lookup; it is a driver only, it renders nothing.
"use strict";
const fs = require("fs"), path = require("path");
const base = (process.argv[2] || "http://127.0.0.1:7870").replace(/\/$/, "");
const out = process.argv[3] || "studio-check-ui";
// the app's access key (app/access.py): STUDIO_KEY for another machine's app, else this computer's studio folder's
const KEY = process.env.STUDIO_KEY || (() => { try { const home = require("child_process").execFileSync("python3", [path.join(__dirname, "../app/studio_home.py")]).toString().trim();
  return fs.readFileSync(path.join(home, "access-key"), "utf8").trim(); } catch (e) { return ""; } })();
const KEYED = KEY ? { extraHTTPHeaders: { "X-Studio-Key": KEY } } : {};
const pw = (() => { for (const p of [process.env.PLAYWRIGHT, "playwright-core", "playwright"].filter(Boolean)) {
  try { return require(p); } catch (e) { /* next */ } } throw new Error("playwright-core not found: set PLAYWRIGHT"); })();
const chrome = process.env.CHROME || (() => { const d = path.join(process.env.HOME || "", ".cache/ms-playwright");
  try { for (const v of fs.readdirSync(d).filter(n => n.startsWith("chromium-")).sort().reverse())
    for (const c of ["chrome-linux-arm64/chrome", "chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"])
      if (fs.existsSync(path.join(d, v, c))) return path.join(d, v, c); } catch (e) { /* none */ } return undefined; })();
const ONLY = (process.env.TABS || "").split(",").filter(Boolean);
const CLICKS = Number(process.env.CLICKS || 80);  // buttons clicked a tab at most
// buttons never clicked even with writes blocked: they leave the page or open the OS
const SKIP = /^(sign in|sign out|log ?in|download|open in finder|reveal|print|quit|stop the app|restart)/i;
// what a view shows when something went wrong in its drawing
const BAD_TEXT = /\b(undefined|NaN|\[object Object\]|Traceback|Internal Server Error)\b/;

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await pw.chromium.launch({ executablePath: chrome });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, ...KEYED });
  const page = await ctx.newPage();
  let now = { tab: "(load)", click: null }, events = [];
  const note = (type, text) => events.push({ tab: now.tab, click: now.click, type, text: String(text).slice(0, 400) });
  page.on("pageerror", e => note("pageerror", e.message));
  page.on("console", m => { if (m.type() === "error") { const t = m.text();
    if (!/status of 418|Failed to load resource: the server responded with a status of 418/.test(t)) note("console", t); } });
  page.on("requestfailed", r => { const f = r.failure() && r.failure().errorText;
    if (!/ERR_ABORTED/.test(f || "") || r.resourceType() !== "media") note("requestfailed", r.method() + " " + r.url().replace(base, "") + " " + f); });
  page.on("response", r => { if (r.status() >= 400 && r.status() !== 418 && r.url().startsWith(base))
    note("http" + r.status(), r.request().method() + " " + r.url().replace(base, "")); });
  page.on("dialog", d => { note("dialog", d.type() + ": " + d.message().slice(0, 160)); d.dismiss().catch(() => {}); });
  page.on("filechooser", () => note("filechooser", "opened"));
  page.on("popup", p => { note("popup", p.url()); p.close().catch(() => {}); });
  const wouldSend = [];
  await page.route("**/*", route => { const r = route.request();
    if (r.method() !== "GET" && r.method() !== "HEAD") { wouldSend.push({ tab: now.tab, click: now.click, method: r.method(), url: r.url().replace(base, "") });
      return route.fulfill({ status: 418, contentType: "application/json", body: JSON.stringify({ error: "studio-check-ui: writes are blocked" }) }); }
    if (!r.url().startsWith(base) && !/^data:|^blob:/.test(r.url())) note("external", r.method() + " " + r.url());
    return route.continue(); });

  const t0 = Date.now();
  await page.goto(base + "/", { waitUntil: "load" });
  await page.waitForTimeout(2500);
  const tabs = await page.evaluate(() => typeof TABS !== "undefined" ? TABS : []);
  const report = { base, started: new Date().toISOString(), tabs: [] };
  for (const tab of tabs) {
    if (ONLY.length && !ONLY.includes(tab)) continue;
    now = { tab, click: null };
    const before = events.length, t = Date.now();
    await page.evaluate(t => { location.hash = "#" + encodeURIComponent(t); }, tab);
    await page.waitForTimeout(1800);
    const look = await page.evaluate(([badText]) => {
      const view = document.getElementById("view") || document.body, bad = new RegExp(badText);
      const vis = n => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
      const text = view.innerText || "";
      return {
        shown: typeof tab !== "undefined" ? tab : null,
        chars: text.length,
        bad: (text.match(new RegExp(bad.source, "g")) || []).slice(0, 5),
        brokenImages: [...view.querySelectorAll("img")].filter(i => vis(i) && i.complete && i.naturalWidth === 0 && i.src).map(i => i.src.slice(-80)),
        brokenMedia: [...view.querySelectorAll("video,audio")].filter(v => v.error).map(v => (v.currentSrc || v.src).slice(-80)),
        unavailable: [...new Set([...view.querySelectorAll(".note, .warn, .why, small, p")].map(n => n.innerText || "")
          .filter(s => /DGX Spark only|not (installed|downloaded|available|ready)|needs? (a )?(DGX|Spark|CUDA)/i.test(s)).map(s => s.trim().slice(0, 200)))].slice(0, 8),
        buttons: [...view.querySelectorAll("button, [role=tab], a[href^='#']")].filter(vis).length,
        disabled: [...view.querySelectorAll("button[disabled]")].filter(vis).map(b => (b.innerText || b.title || "").trim().slice(0, 40)).slice(0, 15),
      }; }, [BAD_TEXT.source]);
    await page.screenshot({ path: path.join(out, String(report.tabs.length).padStart(2, "0") + "-" + tab.replace(/\W+/g, "_") + ".png") });
    // every visible button of the view, by its text and position among buttons with that text
    const labels = await page.evaluate(() => { const view = document.getElementById("view") || document.body, seen = {};
      return [...view.querySelectorAll("button:not([disabled]), [role=tab]")].filter(n => n.offsetWidth || n.offsetHeight)
        .map(n => { const label = (n.innerText || n.getAttribute("aria-label") || n.title || "").trim().replace(/\s+/g, " ").slice(0, 60);
          seen[label] = (seen[label] || 0) + 1; return [label, seen[label] - 1]; }); });
    const clicked = [];
    for (const [label, nth] of labels.slice(0, CLICKS)) {
      if (!label || SKIP.test(label)) continue;
      now.click = label;
      const hit = await page.evaluate(([label, nth, tab]) => {
        const view = document.getElementById("view") || document.body;
        const all = [...view.querySelectorAll("button:not([disabled]), [role=tab]")].filter(n => n.offsetWidth || n.offsetHeight)
          .filter(n => (n.innerText || n.getAttribute("aria-label") || n.title || "").trim().replace(/\s+/g, " ").slice(0, 60) === label);
        const b = all[nth]; if (!b) return false; b.click(); return true; }, [label, nth, tab]).catch(e => { note("clickfail", e.message); return null; });
      if (hit) clicked.push(label);
      await page.waitForTimeout(250);
      // a click that went to another tab or opened a dialog: back to this tab, closed
      await page.keyboard.press("Escape").catch(() => {});
      const shown = await page.evaluate(() => decodeURIComponent(location.hash.slice(1))).catch(() => tab);
      if (shown !== tab) { await page.evaluate(t => { location.hash = "#" + encodeURIComponent(t); }, tab); await page.waitForTimeout(600); }
    }
    now.click = null;
    const mine = events.slice(before);
    report.tabs.push({ tab, ms: Date.now() - t, ...look, clicked: clicked.length,
      pageerrors: mine.filter(e => e.type === "pageerror"), console: mine.filter(e => e.type === "console"),
      http: mine.filter(e => /^http/.test(e.type)), failed: mine.filter(e => e.type === "requestfailed"),
      other: mine.filter(e => !/^(pageerror|console|http\d+|requestfailed)$/.test(e.type)) });
    const r = report.tabs[report.tabs.length - 1];
    console.log(`${tab.padEnd(14)} ${r.pageerrors.length ? "FAIL" : "ok  "} ${String(r.ms).padStart(6)} ms  clicked ${String(r.clicked).padStart(3)}  pageerr ${r.pageerrors.length}  console ${r.console.length}  http ${r.http.length}  bad ${r.bad.length}${r.unavailable.length ? "  unavailable: " + r.unavailable[0].slice(0, 70) : ""}`);
  }
  // the phone layout of Home
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, ...KEYED });
  await phone.goto(base + "/", { waitUntil: "load" }); await phone.waitForTimeout(2000);
  report.phone_overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await phone.screenshot({ path: path.join(out, "99-phone.png") });
  report.would_send = wouldSend;
  report.seconds = (Date.now() - t0) / 1000;
  fs.writeFileSync(path.join(out, "ui.json"), JSON.stringify(report, null, 1));
  const failing = report.tabs.filter(t => t.pageerrors.length || t.bad.length || t.brokenImages.length || t.brokenMedia.length);
  console.log(`\n${report.tabs.length} tabs in ${report.seconds.toFixed(0)} s, ${failing.length} with problems, ${wouldSend.length} writes blocked; phone overflow ${report.phone_overflow}px; ${path.join(out, "ui.json")}`);
  await browser.close();
  process.exit(failing.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
