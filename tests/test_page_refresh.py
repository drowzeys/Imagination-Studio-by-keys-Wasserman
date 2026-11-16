"""The page brings itself up to date (app/static/index.html: poll, viewCards, busyHere): the first job after a start
redraws the page, a job's card inside a page follows the job, and a tick box that kept the focus holds nothing back."""
import json
import re
import subprocess
from pathlib import Path

from test_shell_page import need_node

ROOT = Path(__file__).resolve().parent.parent

HARNESS = r"""
const vm = require("vm");
globalThis.renders = 0; globalThis.render = () => { renders++; };
globalThis.jobs = []; globalThis.project = null; globalThis.tab = "Library"; globalThis.answer = [];
globalThis.api = async () => answer;
globalThis.jobTools = () => {}; globalThis.queueDraw = () => {}; globalThis.bayUpdate = () => {}; globalThis.$ = () => null;
globalThis.focus = { matches: () => false }; globalThis.cards = []; globalThis.listeners = {};
globalThis.document = { get activeElement() { return focus; }, querySelectorAll: () => cards,
  addEventListener: (on, f) => { listeners[on] = f; } };
// an input as the browser matches it against the page's selector: only what the selector's :not() list leaves out
const input = type => ({ matches: s => !s.includes(":not([type=" + type + "])") });
const card = (id, sig) => ({ dataset: { job: id, sig }, closest: () => null, querySelector: () => null, replaced: null,
  replaceWith(n) { this.replaced = n; } });
globalThis.jobCard = job => ({ dataset: {}, text: job.status + " " + ((job.progress || {}).stage || ""), querySelector: () => null });
vm.runInThisContext(CODE);
(async () => { const out = {};
  await poll(); out.first = renders;                       // the page's own first poll: nothing queued, no redraw
  answer = [{ id: "a", status: "queued" }]; await poll(); out.queued = renders;   // the first job after a start
  focus = input("checkbox"); answer = [{ id: "a", status: "running" }]; await poll(); out.ticked = renders;
  focus = input("text"); answer = [{ id: "a", status: "done" }]; await poll(); out.typing = renders;
  focus = { matches: () => false }; await poll(); out.after = renders;
  const stale = card("a", "old"); cards = [stale]; answer = [{ id: "a", status: "failed", progress: { stage: "cutting" } }]; await poll();
  out.card = stale.replaced && stale.replaced.text;
  const same = card("a", JSON.stringify(answer[0])); cards = [same]; await poll(); out.kept = same.replaced === null;
  listeners.pointerdown(); await poll(); const held = card("a", "old"); cards = [held]; await poll(); out.held = held.replaced === null;
  listeners.pointermove({ buttons: 0 }); await poll(); out.released = held.replaced !== null;
  console.log(JSON.stringify(out)); })();
"""


def page_code() -> str:
    text = (ROOT / "app/static/index.html").read_text()
    wanted = [r"^let lastSig = [^\n]*$", r"^let HELD = [^\n]*$", r"^for \(const \[on, held\] of [^\n]*$",
              r'^document\.addEventListener\("pointermove"[^\n]*$', r"^const busyHere = [^\n]*$", r"^function viewCards\(\).*?was\.replaceWith\(card\); } }$", r"^async function poll\(\).*?render\(\); } }$"]
    found = [re.search(one, text, re.M | re.S) for one in wanted]
    assert all(found), [w for w, f in zip(wanted, found) if not f]
    return "\n".join(f.group(0) for f in found)


def test_the_page_follows_its_jobs_without_leaving_it():
    need_node()
    done = subprocess.run(["node", "-e", "const CODE = " + json.dumps(page_code()) + ";" + HARNESS], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["first"] == 0
    assert out["queued"] == 1      # an empty queue was taken for "no poll yet": the first job did not redraw the page
    assert out["ticked"] == 2      # a tick box, a slider or a file button keeps the focus after its click
    assert out["typing"] == 2 and out["after"] == 3   # typing holds the redraw, which comes once the field is left
    assert out["card"] == "failed cutting" and out["kept"]
    assert out["held"] and out["released"]   # a pointer that is down holds it; one moving with no button down is up
