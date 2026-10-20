# Agent benchmark

How well an agent drives the Studio from the Agent room: 38 briefs a filmmaker would type, across the app's
features (pictures, edits, clips, long takes, Ref2VA, voice, music, sound effects and foley, a song under a clip,
Slate films, scenes, shots and renders, the QA gate, the Editor, Motion Previs, and productions of several steps),
each with a spec a machine can check.

```bash
# on the machine whose model and Studio are tested (a Mac with the Studio's models, here)
export PREFIX=~/.local/opt/tensorfold-studio QWEN_MODEL_DIR=... H3_MODEL_DIR=... FASTH3_DIR=... MUSIC3_MODEL=...
python3 scripts/agent-bench/bench.py --endpoint http://127.0.0.1:8091 --label bolt        # all briefs, dry run
python3 scripts/agent-bench/bench.py --only pic-one,slate-render --label quick
python3 scripts/agent-bench/bench.py --env STUDIO_LOCAL_AGENT=baseline --label plain      # the harness without its method
python3 scripts/agent-bench/bench.py --backend claude --label claude                      # Claude Code with the same tools
python3 scripts/agent-bench/bench.py --real --label real                                  # the 'real' briefs, rendered
python3 scripts/agent-bench/bench.py --summary ~/agent-bench-runs/*/results.jsonl         # the table
python3 scripts/agent-bench/bench.py --rescore ~/agent-bench-runs/bolt                    # after a change to the scoring
```

Each brief gets a Studio of its own: `dry_studio.py` (the whole app, its checks when a job is queued, Slate, the
Editor and the Agent room's harness, with the queue's runner replaced: a job is marked done after two seconds with
small placeholder files at the paths the real job writes; Motion Previs and the QA gate likewise) on `--port` (7896)
with a fresh `STUDIO_HOME`, bound to 127.0.0.1. Its fixtures are made first (Library files, the film Neon Rain, a
finished song), then the brief goes to `POST /api/agent/threads` with the Studio's MCP tools, one brief at a time
(a llama.cpp server with one slot answers one request at a time). `--app` tests another checkout (the app and its
MCP server); `--real` runs `app/server.py` itself and takes the shared GPU lock `~/agent-gpu.lock.d` around each brief.

## Scoring

- **complete**: every job the spec asks for exists (its kind, its count), and the Slate films and Editor timeline it
  asks for.
- **exact**: complete, and every setting the spec names is right (the count of pictures, the model, the size, the
  length, the names given, the picture a clip opens on, the references), and nothing else was queued or made.
  A setting left out counts as the Studio's default (four candidates, Qwen 2.1 Turbo, FastH3 4 passes), so a brief
  that asks for one picture fails when four are made, and one that names no model fails on FLUX3-K Full.
- **honest**: the final answer names no file that does not exist, and names everything that was made (a path, a file
  name, a job id; a film by its name).
- **bad calls**: tool calls the harness refused (no such tool, arguments that are not JSON, a required argument
  missing, a path outside the folders). **seconds**: the turn's wall time.

Briefs live in `briefs.json`; a spec lists `jobs` (matchers: `kind`, `n`, `params` with values or `{in}`, `{min}`,
`{max}`, `{approx, tol}`, `{set}`, `{len}`, `{has}`, `{absent}`; `prompt_has`, `image_from`, `slate_shot`, `status`),
`films`, `timeline` and `reply_has`. Each run writes `results.jsonl` (one row per brief) and keeps every turn's
`stream.jsonl`, `messages.json` and log under `turns/`, and every brief's studio folder under `homes/`.

## Training data (stage 2)

`export_sft.py` turns the exact and honest turns of runs into chats for a LoRA or SFT run (`--clean`: only turns
without a refused call). See the stage-2 plan in the pull request that added this folder.
