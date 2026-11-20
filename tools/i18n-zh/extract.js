// Extract user-visible English strings from Imagination Studio front end.
// Usage: node extract.js <repo> > strings.json
"use strict";
const fs = require("fs"), path = require("path");
const acorn = require("acorn"), walk = require("acorn-walk"), parse5 = require("parse5");

const repo = process.argv[2];
const dir = path.join(repo, "app/static");
const out = new Map(); // text -> {kind, files:Set}

const ATTRS = new Set(["title", "aria-label", "placeholder", "alt", "label", "data-tip"]);
function add(text, kind, file) {
  if (text == null) return;
  text = text.replace(/\s+/g, " ").trim();
  if (!looksHuman(text)) return;
  const k = out.get(text) || { kind, files: new Set() };
  k.files.add(file); out.set(text, k);
}
function looksHuman(t) {
  if (t.length < 2 || t.length > 1200) return false;
  if (!/[A-Za-z]{2,}/.test(t)) return false;
  if (/^[a-z0-9_.\-\/:#\[\]=*>~+,()]+$/.test(t) && !/ /.test(t)) {
    // single lower token: keep only if a plain english word list later decides; mark as maybe
    return /^[a-z]{3,}$/.test(t) ? "maybe" : false;
  }
  if (/^(https?:|\/api\/|\.\/|data:|#[0-9a-f]{3,8}$)/i.test(t)) return false;
  if (/^[\w-]+(\s+[\w-]+)*$/.test(t) && /[-_]/.test(t) && !/[A-Z]/.test(t)) return false; // class lists
  if (/[{};]\s*$/.test(t) && /:/.test(t) && !/ [a-z]+ [a-z]+ /.test(t)) return false; // css
  if (/^(var\(|rgba?\(|calc\(|translate|matrix|M\d|[0-9.]+(px|%|em|rem|s|ms)\b)/.test(t)) return false;
  if (/^[A-Z_][A-Z0-9_]+$/.test(t)) return false; // CONSTANT
  if (/^[a-z]+[A-Z]\w*$/.test(t)) return false; // camelCase
  return true;
}
function fromHtml(html, file) {
  let frag;
  try { frag = parse5.parseFragment(html); } catch { return; }
  (function rec(n) {
    if (n.nodeName === "#text") add(n.value, "html", file);
    if (n.attrs) for (const a of n.attrs) if (ATTRS.has(a.name)) add(a.value, "attr", file);
    if (n.nodeName === "script" || n.nodeName === "style") return;
    for (const c of n.childNodes || []) rec(c);
    if (n.content) rec(n.content);
  })(frag);
}
function fromJs(code, file) {
  let ast;
  try { ast = acorn.parse(code, { ecmaVersion: "latest", sourceType: "script", allowHashBang: true, allowReturnOutsideFunction: true }); }
  catch (e) { console.error("parse fail", file, e.message); return; }
  walk.fullAncestor(ast, (node, _s, anc) => {
    const parent = anc[anc.length - 2];
    if (node.type === "Literal" && typeof node.value === "string") {
      if (parent && parent.type === "Property" && parent.key === node) return;
      if (parent && parent.type === "ImportDeclaration") return;
      const v = node.value;
      if (/<[a-z][^>]*>/i.test(v)) { fromHtml(v, file); return; }
      add(v, "js", file);
    } else if (node.type === "TemplateLiteral") {
      // pattern with {n} placeholders
      let s = "";
      node.quasis.forEach((q, i) => { s += q.value.cooked ?? q.value.raw; if (i < node.expressions.length) s += "{" + i + "}"; });
      if (/<[a-z][^>]*>/i.test(s)) { fromHtml(s.replace(/\{\d+\}/g, "⁣"), file); return; }
      if (node.expressions.length === 0) add(s, "js", file);
      else if (/[A-Za-z]{2,}\s+[A-Za-z]{2,}/.test(s.replace(/\{\d+\}/g, ""))) add(s, "tpl", file);
    }
  });
}
for (const f of fs.readdirSync(dir)) {
  const p = path.join(dir, f);
  if (f.endsWith(".js") && f !== "sw.js") fromJs(fs.readFileSync(p, "utf8"), f);
  if (f === "index.html") {
    const html = fs.readFileSync(p, "utf8");
    fromHtml(html.replace(/<script[\s\S]*?<\/script>/g, ""), f);
    for (const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) fromJs(m[1], f);
  }
}
const res = [];
for (const [t, v] of out) res.push({ t, kind: v.kind, files: [...v.files].sort() });
res.sort((a, b) => a.t.localeCompare(b.t));
process.stdout.write(JSON.stringify(res, null, 1));
console.error("total", res.length, "chars", res.reduce((s, r) => s + r.t.length, 0));
