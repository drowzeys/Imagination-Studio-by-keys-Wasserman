---
name: imagination-studio
description: Render or process anything (pictures, clips, voice, songs, sound, edits, swaps, LoRA training) on this machine through Imagination Studio's queue, API or MCP tools, never with standalone scripts or a separate ComfyUI; check the Studio's memory headroom before starting a model server of your own.
---

# Imagination Studio: rules for agents

Imagination Studio (by keys+Wasserman) runs on this machine at `http://127.0.0.1:7870` (or `$STUDIO_URL`). It is the
one place where pictures, clips, voice takes, songs, sound effects, edits, swaps and LoRA runs are made here.

## 1. Every render and processing task goes through the Studio

- **Do** queue the work through the Studio: its MCP server (`mcp/studio-mcp.py`: `queue_clip`, `queue_still`,
  `queue_voice`, `queue_song`, `queue_sound`, `queue_edit`, ... then `wait_for_jobs`), or its HTTP API:
  `POST /api/jobs` with `{"kind": "video", "title": "...", "params": {...}}`, then `GET /api/jobs/<id>` until its
  `status` is `done` (its `outputs` are Library paths, served at `/files/<path>`).
- **Do not** run the render scripts (`scripts/*.sh`, `spark/*.py`, `h3_generate.py`, ...) yourself, start a second
  ComfyUI, or load the Studio's models in a Python session of your own. Only the Studio's queue checks a job's
  measured memory peak before it starts, unloads its own idle modules to make room, runs one heavy job at a time and
  stands a memory floor under it. A render started outside it has none of that: on a DGX Spark one such run
  OOM-rebooted the machine, and another hung hard for 50 minutes with an LLM server, the video worker and the app
  resident together.

## 2. Check the headroom before starting a model server of your own

The Studio and anything you start share one pool of memory (all of it on a DGX Spark or a Mac).

```bash
curl -s http://127.0.0.1:7870/api/memory      # MCP: memory_status
```

Read `headroom_mb` (available memory less the floor the Studio keeps free), `modules` (what the Studio has loaded,
and which are idle), `others` (the largest other programs, GPU programs first) and `events` (what the Studio unloaded,
waited for or refused). Then:

- Do not start an LLM server, a training run or any big model while a Studio job renders (`GET /api/jobs`: a job
  with `"status": "running"`), nor when it would leave less than the next job needs. A job that does not fit waits up
  to two minutes for memory, then is refused with your process named in the message.
- If you need room for your own model, ask the Studio to unload its idle modules first, and stop your own server
  when you are done with it.

## 3. Ask the Studio to give memory back

```bash
curl -s -X POST http://127.0.0.1:7870/api/memory/release -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:7870/api/memory/release -H 'Content-Type: application/json' -d '{"modules": ["bolt"]}'
```

(MCP: `release_memory`.) Modules: `voice` (Fish Audio Drama 3), `music` (the song engines), `comfy` (the Studio's
ComfyUI and its cached models), `bolt` (the Bolt brain). The module a running job uses is never unloaded, and nothing
the Studio did not start is ever stopped by it: your processes are yours to stop.

## 4. The machine guard

The one-shot setup installs `scripts/memguard.py` as a per-user service (systemd user unit, launchd agent or a logon
task; `python3 scripts/memguard.py status` says whether it is installed). It reads the machine's memory up to ten
times a second and, before the kernel's OOM killer or a hang: has the Studio unload its idle modules, then (only if
the person opted in with `--stop-foreign`) stops the largest of their own model servers, then, as the last resort,
cancels the render. Its log is `memguard.log` in the folder `status` names. Remove it with
`python3 scripts/memguard.py uninstall`.

If the Studio needs a key (`401`), send `X-Studio-Key: <key>` from `~/ImaginationStudio/access-key`; from this
computer itself none is needed.
