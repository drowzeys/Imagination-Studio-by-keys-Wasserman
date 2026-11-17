"""python3 -I tools/i18n-zh/pending.py . > /tmp/pending.json : the studio's English strings that translations.json does
not have yet (new in an upstream update). Needs node with acorn, acorn-walk and parse5 for extract.js
(npm i acorn acorn-walk parse5 in a scratch folder, then NODE_PATH=<that folder>/node_modules)."""
import json, re, subprocess, sys
from pathlib import Path
repo = Path(sys.argv[1]).resolve(); here = Path(__file__).resolve().parent
have = json.load(open(here / "translations.json", encoding="utf8"))
js = json.loads(subprocess.run(["node", str(here / "extract.js"), str(repo)], capture_output=True, text=True, check=True).stdout)
py = json.loads(subprocess.run([sys.executable, "-I", str(here / "extract_py.py"), str(repo)], capture_output=True, text=True, check=True).stdout)
seen, out = set(), []
def add(t, kind, files):
    t = re.sub(r"\s+", " ", t).strip()
    if not t or t in have or t in seen: return
    if re.match(r"^[a-z-]+\s*:\s*[^ ]", t) and ";" in t: return
    if re.fullmatch(r"[a-z0-9-]+( [a-z0-9-]+)*", t) and "-" in t: return
    seen.add(t); out.append({"id": len(out), "t": t, "kind": kind, "files": files[:3]})
for r in js + py: add(r["t"], r["kind"], r["files"])
for f in sorted((repo / "app/static/slate").glob("*.json")):
    def w(x):
        if isinstance(x, dict): [w(v) for v in x.values()]
        elif isinstance(x, list): [w(v) for v in x]
        elif isinstance(x, str) and re.search(r"[A-Za-z]{2,}", x) and not re.match(r"^(https?:|[a-z0-9_.-]+$)", x): add(x, "slate", ["slate/" + f.name])
    w(json.load(open(f, encoding="utf8")))
# the LoRA and model catalogs: only the fields the page shows as words (names, descriptions, notes)
SHOWN = {"name", "what", "notes", "why", "choice", "label", "note", "group", "requirements_note", "installed_label"}
for f in ("lora-groups.json", "lora-host.json", "lora-presets.json", "model_catalog.json"):
    def c(x, key=None):
        if isinstance(x, dict): [c(v, k) for k, v in x.items()]
        elif isinstance(x, list): [c(v, key) for v in x]
        elif isinstance(x, str) and key in SHOWN and re.search(r"[A-Za-z]{2,}", x): add(x, "catalog", ["app/" + f])
    c(json.load(open(repo / "app" / f, encoding="utf8")))
json.dump(out, sys.stdout, ensure_ascii=False, indent=0)
print(len(out), "new strings", file=sys.stderr)
