#!/usr/bin/env python3
"""Training data from benchmark runs (stage 2, not run here): every turn that was exact and honest, as one chat in the
OpenAI messages format with its tools, ready for a LoRA or SFT run on NVIDIA Nemotron 3.5 Bolt's own chat template.

    python3 scripts/agent-bench/export_sft.py ~/agent-bench-runs/E-full ~/agent-bench-runs/claude-* -o bolt-sft.jsonl

Each line: {"messages": [...], "tools": [...], "meta": {run, brief, method, seconds, bad}}. The local harness's
turns come from the messages.json it keeps beside each turn (the system prompt, the brief, the checklist, every call
and every tool answer as the model read them) with its own last answer appended (not the records the harness puts
under it). With --clean a turn with a refused call (a bad one, or a failed call repeated) is left out too, so only turns
that were right the first time teach. Turns of the claude and codex
harnesses have no messages.json: their calls are in stream.jsonl in the CLI's own shape, to be replayed into this
format by the stage-2 tooling (see scripts/agent-bench/README.md).
"""
import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", help="run folders (each with results.jsonl and turns/)")
    parser.add_argument("-o", "--out", default="bolt-sft.jsonl")
    parser.add_argument("--clean", action="store_true", help="only turns without a refused call")
    args = parser.parse_args()
    kept = skipped = 0
    with open(args.out, "w") as out:
        for run in map(Path, args.runs):
            results = run / "results.jsonl"
            if not results.is_file():
                continue
            for row in (json.loads(line) for line in results.read_text().splitlines() if line.strip()):
                turn = run / "turns" / row["id"]
                if not (row.get("exact") and row.get("honest")) or not (turn / "messages.json").is_file():
                    skipped += 1
                    continue
                saved = json.loads((turn / "messages.json").read_text())
                if args.clean and row.get("bad"):
                    skipped += 1
                    continue
                meta = {"run": run.name, "brief": row["id"], "method": saved.get("method"), "seconds": row.get("seconds"), "bad": row.get("bad")}
                messages = list(saved.get("messages") or []) + [{"role": "assistant", "content": saved.get("said") or saved.get("reply") or ""}]
                out.write(json.dumps({"messages": messages, "tools": saved.get("tools") or [], "meta": meta}) + "\n")
                kept += 1
    print(f"{kept} turns written to {args.out}; {skipped} left out")


if __name__ == "__main__":
    main()
