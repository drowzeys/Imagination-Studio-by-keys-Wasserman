# Slate Studio MCP

`studio-mcp.py` lets an agent you already use (Claude Code, Codex, Hermes, or any MCP client) work with a running
Wasserman Slate AI Studio: look at the Library, queue stills and clips, follow the queue, and bring files in. It
does what you would do on the Studio page, through the Studio's own API.

It is one Python file with no dependencies (Python 3.9 or newer). The Studio must be running.

```
 MCP client  ──stdio──▶  studio-mcp.py  ──HTTP──▶  Studio  (http://127.0.0.1:7870)
```

Set `STUDIO_URL` when the Studio is somewhere else (default `http://127.0.0.1:7870`). Paths in and out are
relative to the Studio's folder (`~/ImaginationStudio` by default), as the Library shows them; a finished file is
at `$STUDIO_URL/files/<path>`.

Inside the Studio, **Brains & agents** already connects this server for you. The sections below are for using it
from an agent on its own.

---

## Connect

Use the **absolute path** to `mcp/studio-mcp.py` in this folder in every config below.

### Claude Code

```bash
claude mcp add studio -e STUDIO_URL=http://127.0.0.1:7870 -- python3 /absolute/path/to/mcp/studio-mcp.py
```

`/mcp` in a session lists **studio** as connected. Remove it with `claude mcp remove studio`.

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.studio]
command = "python3"
args = ["/absolute/path/to/mcp/studio-mcp.py"]
env = { STUDIO_URL = "http://127.0.0.1:7870" }
```

### Hermes

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  studio:
    command: "python3"
    args: ["/absolute/path/to/mcp/studio-mcp.py"]
    env:
      STUDIO_URL: "http://127.0.0.1:7870"
```

Start a new Hermes session to load the tools.

### Any stdio MCP client

```json
{
  "mcpServers": {
    "studio": {
      "command": "python3",
      "args": ["/absolute/path/to/mcp/studio-mcp.py"],
      "env": { "STUDIO_URL": "http://127.0.0.1:7870" }
    }
  }
}
```

### From another machine

The Studio listens on its own `127.0.0.1` unless it was started with `HOST=0.0.0.0`. To use a Studio on a DGX Spark
from an agent on your Mac, run the server there over ssh, so nothing is opened to the network:

```json
{ "command": "ssh", "args": ["-o", "BatchMode=yes", "sam@spark", "--", "python3", "/path/on/the/spark/mcp/studio-mcp.py"] }
```

---

## Tools

| Tool | Params | Does |
|---|---|---|
| `studio_state` | — | **Call first.** Presets (sizes), qualities, clip lengths, picture models and the machine. |
| `list_library` | `kind?` (`video` / `image`), `limit?` | Clips and stills, newest first, with their paths. |
| `search_library` | `query`, `limit?` | Searches the Library by what is in each clip and picture (the Studio describes them): "a dog chasing a kite". Best matches first, with a caption and a path. |
| `queue_still` | `prompt`, `count?`, `preset?`, `width?`, `height?`, `seed?`, `image_model?` | Queues stills: `count` pictures (1 to 12, default 1: an agent's "a picture" is one) and a contact sheet. |
| `queue_clip` | `prompt`, `first_frame?`, `image_prompt?`, `references?`, `preset?`, `quality?`, `seconds?`, `seed?`, `title?` | Queues a clip with sound: from a Library picture (`first_frame`), from a picture it makes first (`image_prompt`), from up to nine reference pictures (`references`, Ref2VA: named `<Picture 1>`... in the prompt), or from text alone. |
| `queue_voice` | `text`, `seed?`, `title?` | Queues a spoken line with Fish Audio Drama 3. The voice and delivery go in brackets before the words: `[a tired older man, quiet] We should go.` |
| `queue_song` | `caption`, `lyrics?`, `seconds?`, `seed?`, `title?` | Queues a song with MiniMax Music 3: a style caption and lyrics with `[Verse]` / `[Chorus]` tags; no lyrics makes an instrumental. |
| `queue_edit` | `image`, `prompt`, `image_model?`, `seed?`, `title?` | Queues an edit of a Library picture by the image model; the prompt says what it should become. |
| `restyle_clip` | `video`, `prompt`, `keep_sound?`, `seconds?`, `seed?` | Makes a Library clip again in a new look, keeping its action, camera and timing (the clip is the reference video; at most 15 s). Needs Ref2VA. |
| `extend_clip` | `video`, `seconds?`, `prompt?`, `join?`, `seed?` | Carries a Library clip on from its last frame (5 to 30 s) and joins the two into one file. |
| `fix_clip` | `video`, `instruction`, `image_model?`, `keep_sound?`, `seed?` | Changes one thing in a clip from a sentence: its first frame is redrawn, then the clip is made again from it with the clip's own motion. Needs Ref2VA. |
| `queue_long` | `prompt`, `total_seconds`, `segment_seconds?`, `beats?`, `first_frame?`, `preset?`, `quality?`, `seed?`, `title?` | Queues a long take (over 15 s, up to 30 min): a chain of clips, each opening on the last frame of the one before, joined; `beats` is one line per part. |
| `queue_sound` | `kind` (`sfx` / `foley`), `prompt?`, `seconds?`, `video?`, `keep_sound?`, `seed?`, `title?` | Queues a sound effect from words (`sfx`, 1 to 47 s) or a clip's foley (`foley`, its `video`). |
| `queue_soundtrack` | `song`, `video`, `music_db?` | Lays a finished song (its job id) under a clip, ducked under the dialogue: a new .mp4. |
| `music_beats` | `path` | A song's tempo and beats, measured on this machine: bpm, beats, downbeats, bars, duration and confidence. |
| `rough_cut` | `song`, `film?`, `clips?`, `cut?`, `order?`, `foley?`, `title?` | Cuts a Slate film's best takes (or Library clips, for a trailer) to a song so every cut lands on the beat; FCPXML, EDL and a zip for DaVinci Resolve are written when it is done. |
| `rough_cut_status` | `project` | Where a rough cut is (foley, export, song, files, done, failed), its jobs, and when done its video and files. |
| `make_graphics` | `template`, `data`, `size?`, `title?` | Queues a motion graphic rendered to .mp4 on this machine (HyperFrames): `title`, `lower-third`, `kinetic`, `captions`, `slideshow`, `music-video` or `explainer`. |
| `check_clip` | `path`, `prompt?` | Runs the QA gate on a clip (sound, identity, sharpness); its job's `qa` is the report. |
| `motion_previs` | `clip`, `start?`, `end?`, `mode?`, `title?` | Motion Previs: analyses a 2 to 15 s range of a clip into a production pack; its job's `pack` is the pack. |
| `memory_status` | — | This machine's memory as the Studio sees it: `headroom_mb` over the floor it keeps free, its loaded and idle modules, the largest other programs (GPU programs first), and what it unloaded, waited for or refused. Read it before starting a model server of your own. |
| `release_memory` | `modules?` (`voice`, `music`, `comfy`, `bolt`) | Has the Studio unload its idle modules now. The running job's module and a brain that is answering are never touched. |
| `cancel_job` | `id` | Cancels a queued or running job (a render or a Motion Previs analysis). |
| `list_projects` | — | The Editor's projects (timelines), newest first. |
| `add_to_timeline` | `path`, `project?`, `in?`, `out?` | Puts a finished clip at the end of an Editor timeline (the newest, or a new one, when no project is given). |
| `export_timeline` | `project?`, `size?` | Queues the export of an Editor timeline as one .mp4 (needs ffmpeg). |
| `finish_cut` | `project?`, `clips?`, `song?`, `voice?`, `title?`, `subtitle?`, `mix?`, `size?`, `fps?`, `captions?` | Finishes a timeline (or clips) as the final cut and mix through Tesseract: an .mp4, the editable .tsrct project and FCPXML/EDL for DaVinci Resolve. |
| `export_cut` | `id`, `to` (`premiere` / `aftereffects`) | A finished cut (a `finish_cut` job) as a Premiere Pro or After Effects project with its media, zipped. |
| `job_status` | `id` | Status (`queued`, `running`, `done`, `failed`, `cancelled`), progress, outputs, error (a Motion Previs job too). |
| `wait_for_jobs` | `ids`, `seconds?` | Waits until the jobs are done, failed or cancelled, at most `seconds` (default 50, up to 600), and returns them as `job_status` does. |
| `list_jobs` | `limit?` | The queue and finished jobs, newest first. |
| `slate_films` | — | Slate's films (the Production tab), newest first. |
| `slate_film` | `film` | One film by id or name: its scenes and shots with their ids, and each shot's takes with their paths and job ids. |
| `slate_new_film` | `name`, `logline?` | Makes a film and returns its id. |
| `slate_add_scene` | `film`, `name`, `synopsis?` | Adds a scene and returns its id. |
| `slate_add_shot` | `film`, `scene`, `prompt`, `name?`, `intent?`, `seconds?`, `size?`, `angle?`, `movement?`, `soundscape?` | Adds a shot (a sectioned prompt: `# Subject`, `# Composition`, ...; no spoken words) and returns its id. |
| `slate_render_shot` | `film`, `shot`, `first_frame?` | Compiles the shot as the page does, queues its clip into the film's takes folder and logs the take in the shot; `first_frame` opens it on a finished Library picture. |
| `direct_film` | `film`, `logline?`, `scenes?`, `shotsPerScene?`, `seconds?`, `cut?` | The Director: from a logline to a checked, cut film. It writes what the film lacks, renders every shot, checks each take and retakes a failed one, then cuts the best takes together. Follow it with `director_status`. |
| `director_status` | `film` | Where the Director's run stands: stage, shots rendered and checked, retakes, the latest log lines and, when done, the cut's files. |
| `suite_imports` | `import?` | What was brought in from the filmmaker suite (Blockout previs, a screenplay, a board, a Motion Previs pack); with `import`, that one's shots. |
| `suite_attach` | `import`, `film`, `mapping` | Sends shots of a suite import into a Slate film: the still becomes a shot's first frame, a previs clip its motion reference, and the words fill an empty prompt. |
| `train_lora` | `name`, `trigger`, `items`, `kind?`, `steps?`, `captions?` | Trains a LoRA on a DGX Spark from at least 8 Library clips or stills (a look or a character). It runs for hours; follow it with `training_status`. |
| `training_status` | `id` | A training run, by job id or LoRA id: status, step, last checkpoint, and whether the LoRA is installed. |
| `night_shift` | `film`, `shots?`, `start?`, `name?` | Plans a night shift: the film's shots without a good take (or the ones named) rendered one after another, starting when the queue is idle, now, or at a time like `23:00`; a reel, the dailies package and notes are made when it is done. |
| `upload_file` | `path` | Copies a file from the agent's machine into the Library (pictures, video, sound) and returns its path. |

**Render only through these tools (or the Studio's API), never with the render scripts or a ComfyUI of your own:** only the
Studio's queue checks a job's memory peak, unloads its idle modules to make room and keeps a floor under the render
([STUDIO-AGENT.md](../STUDIO-AGENT.md)). Do not start a model server of your own while a job renders.

Jobs run one at a time, in the order queued. A queued job returns at once with its `id`; follow it with `wait_for_jobs`
(or poll `job_status`) until it is `done`, then use its `outputs`. Clip lengths are brought to the nearest the model takes (up to 15 seconds); sizes the
model cannot take are refused with the reason, as on the page.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `the Studio does not answer at …` | Start the Studio (`bash scripts/app.sh`, or the app), or point `STUDIO_URL` at it. |
| `the Studio said: … (400)` | The settings were refused, for the reason given. `studio_state` lists what this machine takes. |
| `python3: command not found` | Use the absolute path of Python 3 as the `command`. |
