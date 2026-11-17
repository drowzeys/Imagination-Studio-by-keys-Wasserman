"""Extract user-facing English strings from Imagination Studio backend (app/*.py).
Usage: python3 -I extract_py.py <repo> > py_strings.json
Strings: constants and f-strings (as {0}-patterns) that look like sentences, excluding docstrings,
and the windows_* modules (Windows-only backend, not used on Linux)."""
import ast, json, re, sys
from pathlib import Path

repo = Path(sys.argv[1])
out = {}

def human(t):
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 4 or len(t) > 1200:
        return None
    if not re.search(r"[A-Za-z]{2,} [A-Za-z]{2,}", t):
        return None
    if re.match(r"^(https?:|/|\.|-|--|\$|SELECT|import |from |def |#!)", t):
        return None
    if re.search(r"\b(stdout|argv|subprocess|__\w+__)\b", t):
        return None
    return t

for f in sorted((repo / "app").glob("*.py")):
    if f.name.startswith("windows_"):
        continue
    tree = ast.parse(f.read_text("utf8"))
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                docs.add(id(b.value))
    inside_f = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            for v in n.values:
                inside_f.add(id(v))
    for n in ast.walk(tree):
        t = None
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and id(n) not in inside_f:
            t = human(n.value)
        elif isinstance(n, ast.JoinedStr):
            s, i = "", 0
            for v in n.values:
                if isinstance(v, ast.Constant):
                    s += str(v.value)
                else:
                    s += "{%d}" % i
                    i += 1
            t = human(s)
        if t:
            out.setdefault(t, set()).add(f.name)

res = [{"t": t, "kind": "py", "files": sorted(fs)} for t, fs in sorted(out.items())]
json.dump(res, sys.stdout, ensure_ascii=False, indent=1)
print("total", len(res), "chars", sum(len(r["t"]) for r in res), file=sys.stderr)
