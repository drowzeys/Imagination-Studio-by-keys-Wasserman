// node tools/i18n-zh/dom_test.js <repo> [zh|en] : DOM behaviour of tools/i18n-zh/overlay.js in jsdom with a fake dictionary:
// text, attributes, title, dynamic nodes and patterns translated; inputs, textareas and code untouched; an <option> without
// a value still reads English; the tour's spot() finds the translated label; "en" translates nothing. Needs jsdom (NODE_PATH).
const { JSDOM } = require("jsdom"); const fs = require("fs"), path = require("path");
const repo = process.argv[2], lang = process.argv[3] || "zh";
const fake = { dict: { "Queue": "佇列", "Home": "首頁", "Draft": "草稿", "Search or run": "搜尋或執行", "Learn": "教學", "Access key": "存取金鑰", "Type here": "在此輸入" }, patterns: [["{0} jobs waiting", "{0} 個工作等待中"]] };
const i18n = fs.readFileSync(path.join(repo, "tools/i18n-zh/overlay.js"), "utf8");
const html = `<!doctype html><html lang="en"><head><title>Queue</title>
<script>window.StudioLanguage = { current: () => "${lang === "zh" ? "zh-TW" : "en"}" }; window.STUDIO_ZH = ${JSON.stringify(fake)};</script>
<script>${i18n}</script></head><body>
<header id="top"><button id="helpbtn" title="Learn" aria-label="Learn">Learn</button></header>
<h2 id="q">Queue</h2><select id="s"><option>Draft</option><option value="hq">Home</option></select>
<input id="i" value="Queue" placeholder="Type here"><textarea id="t">Queue</textarea><div contenteditable="true" id="c">Queue</div>
<code id="k">Queue</code><label id="l">Access key</label>
<script>function spot(text) { window.spotted = [...document.querySelectorAll("label")].find(n => n.textContent.trim().toLowerCase().startsWith(text.toLowerCase())); }</script>
</body></html>`;
const dom = new JSDOM(html, { runScripts: "dangerously", url: "http://localhost/" });
const w = dom.window, d = w.document, $ = id => d.getElementById(id);
const fails = []; const eq = (a, b, m) => { if (a !== b) fails.push(`${m}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };
setTimeout(async () => {
  const zh = lang === "zh";
  eq($("q").textContent, zh ? "佇列" : "Queue", "h2 text");
  eq($("helpbtn").title, zh ? "教學" : "Learn", "title attr");
  eq($("helpbtn").getAttribute("aria-label"), zh ? "教學" : "Learn", "aria-label");
  eq($("s").value, "Draft", "option without value still reads English");
  eq($("s").options[0].text, zh ? "草稿" : "Draft", "option text");
  eq($("i").value, "Queue", "input value untouched"); eq($("i").placeholder, zh ? "在此輸入" : "Type here", "placeholder");
  eq($("t").value, "Queue", "textarea untouched"); eq($("k").textContent, "Queue", "code untouched");
  eq(d.title, zh ? "佇列" : "Queue", "document title");
  eq(d.documentElement.lang, zh ? "zh-Hant-TW" : "en", "html lang");
  eq(!!$("langbtn"), false, "the official Settings selector owns the language choice");
  eq($("c").textContent, "Queue", "contenteditable untouched");
  // dynamic content
  const p = d.createElement("p"); p.textContent = "3 jobs waiting"; d.body.appendChild(p);
  $("q").firstChild.nodeValue = "Home";
  // a change made in a promise right after another one: the observer's batch must not drop it
  const late = d.createElement("span"); d.body.appendChild(d.createElement("i"));
  Promise.resolve().then(() => { late.textContent = "Draft"; d.body.appendChild(late); });
  await new Promise(r => setTimeout(r, 20));
  eq(late.textContent, zh ? "草稿" : "Draft", "node added in a promise during a batch");
  eq(p.textContent, zh ? "3 個工作等待中" : "3 jobs waiting", "added node pattern");
  eq($("q").textContent, zh ? "首頁" : "Home", "changed text node");
  w.spot("Access key"); eq(!!w.spotted, true, "tour spot finds translated label");
  console.log(fails.length ? "FAIL\n" + fails.join("\n") : "OK " + lang);
  process.exit(fails.length ? 1 : 0);
}, 50);
