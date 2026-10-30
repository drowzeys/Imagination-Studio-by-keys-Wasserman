# Agent one-shot — Imagination Studio by keys+Wasserman, powered by TensorFold (Mac + DGX Spark)

**Repo:** https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman  
**Carrier image:** `ghcr.io/drowzeys/keys-tensorfold-studio:4.2.1` (one for both builds, not a runtime; for a Mac the TensorFold wheel + lock + render scripts + the native FastH3 engine, for a DGX Spark the prebuilt CUDA engines: H3, MiniMax Music 3, Fish Audio Drama 3 and Nemotron 3.5 Bolt). The Mac's native engine also comes from the v3.2.0 release (the same engine build as 3.1.0), and Bolt on Metal from the v4.2.1 release (its symbol table stripped of build paths; H3 and Drama 3 come from the 4.2.1 carrier, the other engines are the same as 4.2.0's)  
**Engine:** TensorFold 0.6.5 + H3 and Qwen-Image families @ `drowzeys/TensorFold` `a2b592fe`, own venv, int8 tensor-unit kernels

```bash
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
cd Imagination-Studio-by-keys-Wasserman
brew install python@3.11 uv ffmpeg
bash oneshot-setup.sh               # image model, FastH3 and its text encoder and decoders (175 GB) and a test clip; --image-only for 33 GB; --turbo adds MiniMax H3 Turbo (64 GB more, 128 GB of memory); --ref2va adds reference pictures to video (64 GB more, 128 GB)
bash scripts/studio.sh "the picture" "what happens in the clip" out.mp4   # text -> image -> video with sound
bash scripts/scout.sh "the picture" 6 scouts/x      # six candidates + scouts/x/sheet.jpg, 27 s
IMAGE_FILE=scouts/x/scout_s3.png QHD=1 FRAMES=192 bash scripts/studio.sh "" "what happens" final.mp4   # 2560x1440
bash scripts/image.sh "a prompt" out.png
FIRST_FRAME=photo.jpg bash scripts/video.sh "what happens next" out.mp4
REF_IMAGES="face.jpg" bash scripts/video.sh "Use <Picture 1> as the person. The person ..." out.mp4   # needs --ref2va
```

On a **DGX Spark** (Linux, CUDA) the same `bash oneshot-setup.sh` installs the CUDA build (it hands over to
`oneshot-setup-spark.sh`): needs `git`, `ffmpeg` and Docker (for the prebuilt engines; without it the CUDA toolkit's
`nvcc`, 12.9 or newer, builds them), 125 GB of free disk for the core install (about 400 GB with `--all`). It takes
the same flags as on a Mac (README, *Install flags*) plus `--train`; there is no `--turbo`, no `--app` and no 2x
decoder there. Upgrade an install with `git pull && bash oneshot-setup.sh --upgrade`, then restart the app. `bash spark/comfy.sh stop` frees the models' memory after rendering.

## Install for a person and build the one-click app

Do these in order on the person's Mac, and report each result plainly.

1. Check the machine: `uname -m` is `arm64`, `sysctl -n machdep.cpu.brand_string` names an M5-family chip (others run
   without the int8 kernels, far slower: say so and ask before continuing), memory is 96 GB or more (48 GB is enough for `--image-only`, pictures only; 128 GB if they want `--turbo`; on an M1 to M4 say plainly that video will be much slower than on an M5, and that it is untested there), and
   `df -h ~` shows 185 GB free (250 GB with `--turbo`). Stop and report if memory or disk falls short.
2. `brew install python@3.11 uv ffmpeg` (install Homebrew first only with the person's agreement).
3. Tell the person the download sizes and licenses before starting: Qwen-Image-2.1 33 GB (Qwen Research License,
   non-commercial), MiniMax H3 text encoder and decoders 72 GB (MiniMax H3 Community License, territory limits; 134 GB with `--turbo`), FastH3 65 GB (same license).
   The install also adds motion graphics (HyperFrames, Apache-2.0, about 400 MB) and Finish (about 150 MB: Tesseract is
   free under Mirage's own terms, not open source, and leaves out commercial use by a business with 1 M USD or more a
   year without Mirage's agreement; its converter is MIT). `--no-finish` or `--no-graphics` leaves one out if the person
   asks. `--all` adds the big optional models; training a LoRA is DGX Spark only.
4. `bash oneshot-setup.sh --yes --app` (in a terminal with no other flag the install first shows a checklist of parts,
   everything ticked; `--yes` or any flag leaves it out, and so does a run that is not in a terminal. The list lives in
   `scripts/install-checklist.sh`; `STUDIO_CHECKLIST_INPUT` and `STUDIO_CHECKLIST_DRY=1` drive it in tests. Add `--turbo` only if they ask for MiniMax H3 Turbo and have 128 GB; `--app` does step 5 with a Desktop shortcut). It resumes if interrupted. It ends by
   rendering `outputs/test.mp4`; confirm that file exists and plays.
5. `bash scripts/make-app.sh` builds `~/Applications/Wasserman Slate AI Studio.app`. If the models are not in the default
   folders, export `QWEN_MODEL_DIR`, `H3_MODEL_DIR`, `FASTH3_DIR` first: the app records them. Do not set
   `HOST=0.0.0.0` unless the person asks to reach it from other machines; this computer needs no key, and a phone or
   another computer signs in with the studio's access key (`app/access.py`; `bash scripts/studio-key.sh` prints it and
   the address that opens the page with it).
6. `open "$HOME/Applications/Wasserman Slate AI Studio.app"` (the app bundle keeps that name), then check
   `curl -s http://127.0.0.1:7870/api/state` answers (no key is asked on this computer, `app/access.py`).
7. Tell the person: double-click Wasserman Slate AI Studio in Applications; renders land in `~/ImaginationStudio`; the log is
   `~/Library/Logs/TensorFoldStudio.log`; to stop it run the app's executable with `stop`.

The app is a launcher around this clone, unsigned and built locally, so Gatekeeper does not block it. Do not move
the clone afterwards without rebuilding the app. Do not sign, notarize or redistribute the built app.

Native engine (2.0): FastH3 runs its passes in `$PREFIX/zig-engine/tf-h3-dit` (TensorFold 1.0 Zig +
Metal runtime, fork branch `h3-1.0.4`), driven by `zig_fasth3.py`; `scripts/video.sh` chooses it on an M5, for text
to video and for clips from an image alike, and prints `[tensorfold] engine: zig|mlx (why)`. `FASTH3_ENGINE=mlx|zig` forces one. Image
generation and the Turbo path are MLX. Do not apply the base-model audio step to FastH3 clips:
the owner compared it by ear and chose FastH3's own sound. Use 20 passes when a prompt has several actions.
`scripts/build-zig-engine.sh` builds the payload on a Mac (zig 0.17); never commit the binary.
A style LoRA on FastH3 stays on the native engine when its `h3_case.py` takes `--lora`: the block projections'
factors are added to the bf16 weights in float32 on the GPU and rounded to int8 with them, in memory, as the MLX
engine merges them (never into a saved copy). Measured at 864x480, 22 frames, 4 passes: 16.9 s against 24.3 s on MLX (1.01 s a pass against 1.71 s; the MLX merge took 7.4 to 41.6 s of load);
the merged weights equal the MLX merge's exactly, and the first velocity's error against a float32 reference with
the LoRA (relative 0.378) stays near the engine's own without one (0.337; 0.579 when the LoRA is left out). An
adapter that changes anything else the native tool reads (a norm, a dense correction) is refused there (status 3)
and the clip renders on MLX.

FastH3 and long videos: `scripts/fast.sh` (STEPS=4|8|20, RES=480p|720p, UPSCALE=1); `ENGINE=fasth3` works with
`studio.sh` and `FIRST_FRAME` too, though FastVideo trained it on text to video only. Videos over 15 s are chains of
clips made by the web app's `long` job (each opens on the last frame of the one before); only an 18 s chain has been
rendered, so do not promise the look of a 30 minute one.

Web app: `bash scripts/app.sh` (http://127.0.0.1:7870; `HOST=0.0.0.0` to open it to the LAN; every request carries the access key of `app/access.py`). It runs the
same scripts through a one-at-a-time queue and stores everything under `~/ImaginationStudio`.

Rules:

- The access key (`app/access.py`, README *The access key*) guards remote access: a request to `/api/` or `/files/`
  from another device carries it; a request from this computer itself (a loopback client naming a loopback Host, or,
  opened to the network, a client on one of its own addresses naming its own address or name: `access.local`,
  `server.this_computer`) needs none, by the owner's decision. `tailscale serve` arrives from a loopback address under a
  `.ts.net` name and is asked: keep both conditions. Only `access.OPEN` is answered without it from elsewhere. The key
  lives only in `<studio folder>/access-key` (0600): never put it in a job's params, a log, a saved URL, a report or
  the repo. Settings → Access key shows it, a QR code for a phone, and makes a new one.
- This is not a server. `tensorfold serve` routes neither model; render with the scripts.
- Qwen-Image-2.1-Turbo is non-commercial (Qwen Research License). MiniMax H3 has territory limits.
  Do not redistribute weights, and say so when asked to use the outputs commercially.
- Image sizes are multiples of 16. For the studio chain both models must accept the canvas: multiples of 32, at most
  768x1344 pixels in total. Video frames must be `17n + 5`, from 22 to 362 (`app/lengths.py`): the app takes any
  of the 21 counts (`frames`, or `seconds` brought to the nearest) and always shows the exact seconds, frames / 24.
  "10 s" is 243 frames, 10.125 s. Do not add a length the model does not take, and do not raise the 15 s limit
  without measuring the memory on a Spark (362 frames at 1376x768 left 29.7 GB free on TensorFold's int8 engine).
- Preferred flow: scout at 1280x736, let the person pick, then `IMAGE_FILE=... TWOK=1` (2048x1152, 2.5 min) or `QHD=1` (2560x1440, 6 min) for the 2K final; `X2=1` for a
  one-minute 1344x768 draft. `X2=1` needs WIDTH and HEIGHT in multiples of 64. 720-row canvases are not valid for the
  video model: use the QHD preset, which generates 736 rows and crops.
- The 2x decoder is an upscale, softer than a native generation at the same size. Do not describe it as added detail.
- Standard video = Turbo adapter, 5 passes, then the sound made again by the base model (`REVOICE=20`). Do not go back
  to the adapter's own sound (`REVOICE=0`) unless asked: it was judged poor by ear. `QUALITY=high` = 20 steps, no
  adapter, about three times slower. `POINTS` is passes plus one.
- Reference to video (`REF_IMAGES`, `oneshot-setup.sh --ref2va`) runs MiniMax H3's Ref2VA transformer on the MLX
  engine: up to nine pictures, named `<Picture 1>`, `<Picture 2>`, ... in the prompt in the order given. A reference
  is not a frame: never combine it with `FIRST_FRAME`, and never send it to FastH3. Default is 20 plain passes;
  `REF_TURBO=1` is the 4-step Ref2VA adapter (`POINTS=5`) with `REVOICE=20`, `QUALITY=high` the fast recipe. The FL2VA Turbo adapter is not for this
  transformer. `Ref2VA/` holds only the transformer; its text encoder, tokenizer, processor and decoders are links
  to `FL2VA/`: do not download them twice.
- Reference videos (`REF_VIDEOS`, up to 3) and reference audio (`REF_AUDIOS`, up to 3) are the same model's other
  references: each clip 2 to 15 s, 15 s in all per kind, 12 references of all kinds, audio never
  alone. Named `<Video N>` and `<Audio N>` in the prompt; with `REF_VIDEO_SOUND=1` each video's own sound is a
  reference numbered ahead of the `REF_AUDIOS` clips. They need a TensorFold whose H3 family has
  `packing.reference_layout` on a Mac (engine branch `ref-video-audio`); the renderer says so when it is missing. A 5 s
  reference video about triples the time of a 5 s clip. Do not promise voice cloning. Measured on the Spark: when the
  spoken line is what the recording says, the clip plays the recording back (likeness 0.98) with the mouth in time,
  whoever is pictured; with other words the voice carries when the prompt also describes it ("in the deep male voice of <Audio 1>":
  122 Hz, the recording's, against 156 without the clip), and a man's voice named without a description beside a
  woman's picture changed nothing. A video's own sound carries (likeness 0.81 against 0.34).
  On the Mac the described voice carried too (a man's voice beside a woman's picture: 137 Hz and MFCC distance 5.0 with
  the clip, 169 Hz and 11.6 with the description alone; the recording is at 117 Hz); use a clean recording.
  On a DGX Spark
  the same variables go through ComfyUI's `MiniMaxH3ReferenceToVideo` (`spark_generate.py video --ref-video /
  --ref-audio`), which needs no engine change; the video is scaled to the clip's pixel area before the node sees it,
  and must stay so (`REF_VIDEO_SIZE=full` multiplies the rows and the memory and is refused on a Spark).
  On a Spark the clip's sound is made again from a lossless copy of the decoder's output, limited to -1 dBFS
  (`finish_sound` in `spark/spark_generate.py`; `AUDIO_EQ=off` skips it): keep it, ComfyUI's own track overshoots full scale.
  A reference prompt keeps its sound under an `overall_soundscape:` heading (`compose_references`): on the end of the
  shot description the model reads it out as gibberish speech. Do not add ComfyUI guidance (CFG) to reference renders:
  at 5 it burns the picture with the 4-step adapter.
  In the app a reference clip is measured as it is added (`/api/probe`) and turned away with the reason; *Custom* passes
  is the card beside *20 passes* in every chooser.
- Motion as a reference: the wizard's *Analyse motion* (and *Use as motion reference* in the Motion Previs tab) sends
  one layer of a Motion Previs pack as `<Video 1>`. Defaults by measurement: the same place with a new person takes the
  source clip (written as "`<Video 1>` with its person replaced", never with the first-frame-anchor wording); a new
  place or the camera alone takes depth; the skeleton is offered but is the loosest. On the Mac the Ref2VA Turbo adapter with a
  reference video runs without the int8 kernels (`scripts/video.sh`): with them it lost fast motion (26 % against 3.7 %);
  do not put them back for that case. The pose pass, the skeleton and the distance checker follow one person only; the user says when
  `<Video 1>` has two or more people (`motion.people`) and the prompt then speaks of its people. A skeleton or depth layer goes
  with the control-video prompt (`motion_template` in `app/server.py`: structural template,
  first-frame anchor, legend, "labels only", and the negative): keep that wording, and do not promise figures: one shot was
  measured. Do not promise an exact copy of a voice from reference audio either. On this machine every render runs through the
  ComfyUI server of `spark/comfy.sh`, never as standalone model code: the guard beside the server (`spark/memory_guard.py watch`, started by `comfy.sh`) is the
  only run-time memory guard.
- On a DGX Spark reference to video is `oneshot-setup-spark.sh --ref2va` and the same `REF_IMAGES` / `REF_TURBO=1`
  (`spark_generate.py video --ref`): ComfyUI's `MiniMaxH3ReferenceToVideo` on its own blocks, never
  `TensorFoldH3Blocks`. The adapter is merged at run time, so it needs the bf16 transformer: on an 8-bit
  (`int8_convrot`) file a run-time adapter silently spoils the picture. A ComfyUI that was running before the
  install must be restarted (`bash spark/comfy.sh stop`) to see the new model folders.
- A LightX2V adapter file (diffusers PEFT names) is rewritten with `scripts/convert-h3-adapter.py` before `--lora`
  can read it; the scale (alpha / rank) differs between their files and is read from the file's metadata.
- Levels are handled after the render (no hard clip; bass lifted on thin takes; `AUDIO_EQ=off` to skip). Leave
  `AUDIO_SHIFT` alone.
- The image turbo adapter runs on six fixed nodes (`--nodes 1.0,0.9375,0.875,0.75,0.5,0.25`); do not change the step
  count with it. `TURBO=0` runs the base model at 40 steps.
- Merge adapters with the tools (`--lora`), never by hand into bfloat16.
- The fast path is M5-only (Metal 4 tensor operations).
- The two models never load together; `studio.sh` runs them one after the other.
- Edit a picture (`edit_plan.py`, read by `qwen_image_edit.py` and `spark/spark_edit.py`): the noise is drawn for the
  seed and the pictures together (`noise_seed`). Never seed it with the user's seed alone: an edit that starts from
  the noise its picture was made from returns that picture, over-sharpened. The result has the first picture's own
  size; up to ten pictures go in one edit, and the model knows them as image 1, image 2, ...
- The video model has no negative prompt on any route. `app/negation.py` lists what a prompt forbids by name and the
  page shows the list before the job is queued; never rewrite the user's prompt, and do not add "no X" lines to the
  prompts the app writes, for the picture or the sound: a clip without lines is told `QUIET_PICTURE` ("Mouths stay
  closed.", only where the shot has people) and `QUIET_SOUND` in `app/server.py`, a locked camera is "a locked, still
  camera" (`MOVES` in `app/slate.py`). The tests hold a compiled prompt to the app's own check with `OWN` emptied.
  A text or first-frame prompt handed to `scripts/video.sh` or `spark/video.sh` with no spoken `<d>` line and no
  `overall_soundscape:` heading is given that same line under the heading (`ensure_ambient` in `app/negation.py`).
  The user's own sentences are not changed. A leading "For the target video" paragraph stays ahead of the heading.
  Leave the prompt as written when it has a reference (`REF_IMAGES`, `REF_VIDEOS`, `REF_AUDIOS`, or a `<Picture>`,
  `<Video>` or `<Audio>` tag), when `REF_PLAIN=1`, or when `REF_VIDEO_SOUND=1` (that sound may hold speech).
- Slate (`app/slate.py`, `app/static/slate.js`): a shot's `intent` never goes into the prompt. A sheet of the bible is
  changed in place (`SL_BIBLE` and the `update_*` actions): never add a second one with the same name, the id is what
  shots cast. A slot in bars (`tf.bars`, with `tf.song` on the project) sets the clip's frames through
  `shot_frames`; the tempo is the user's. `tests/test_slate_page.py` runs the page code in node: keep it passing.
- A clip from another machine's app is read through `app/remote.py` (`/api/qa`, `/api/slate/takes`): addresses are
  `http://host:port` only and paths are media files inside that app's folder. Do not widen either.
- Checking every feature on a running app (a Mac or a DGX Spark): `python3 scripts/studio-check.py` on that machine
  queues the jobs each tab queues (smallest sizes, one realistic clip and song), checks the files with ffprobe and
  writes `report.json` and `report.md`; what the platform does not offer is n/a with the app's reason, never a pass.
  `--base` twice runs two machines at once, `--merge` puts two reports side by side. `scripts/studio-check-ui.cjs`
  opens every tab and clicks every button with writes blocked (open it through 127.0.0.1, e.g. `ssh -L`, so the
  local-only routes answer). The first clears what it made, the second writes nothing.
- Nothing a request handler calls may run a tool without a time limit (`tests/test_api_answers.py`); the GPU's name
  is asked of nvidia-smi once and kept.
- `app/shot.py` checks a finished single clip: first and last frame held, no cut inside one shot. Its thresholds are
  set from one production run and are written out, with the figures, at the top of the file: change them only with
  new measurements. A failed check never fails the job; the retake at the next seed is the user's choice.
- On a DGX Spark a crop (the 720p preset) is made inside the workflow (`crop_frames` in `spark/spark_generate.py`),
  not with ffmpeg afterwards. What needs ffmpeg is refused when it is queued (`without_ffmpeg` in `app/server.py`).
- DGX Spark memory (`spark/memory_guard.py`; the README's *The memory guard*): a clip is checked before it is queued
  (`memory_of` in `app/server.py`) and again in `spark_generate.py` before anything loads, and the guard stands beside
  every server `comfy.sh` starts. Never start ComfyUI another way. With `SPARK_GOVERNOR` (a machine-wide workload
  governor, `spark/workload.py`) `comfy.sh` runs the server through `spark/managed_comfy.py`, which starts the same
  guard (and its plain-loop fallback) beside it inside a reservation, and `start_gate.py` runs before every submission;
  `DRAMA3_MANAGED_GUARD=1` does the same for Drama 3 (`spark/managed_voice.py`). Its limits add to the guard's, never
  replace or lower them; unset, nothing of it runs. `SPARK_PROGRESS_FILE` (`spark/progress_file.py`, off by default)
  lets such a governor show what a clip is doing: two lines, passes and engine, then the stage, replaced whole. `ESTIMATES`, `MEASURED_ROWS` and `MEASURED_PIXELS`
  are measurements, one route each. A LoRA on FastH3 is merged into TensorFold's int8 engine (`TensorFoldH3Blocks`
  takes the block projections' patches off ComfyUI and calls `tf_h3_set_weight`; route `int8-lora`, measured to
  1376x768 at 243 frames); on ComfyUI's bfloat16 blocks (an engine without that symbol, `--engine comfy`, Ref2VA,
  `--base h3`) a clip with a LoRA is refused above 1280x736 at 124 frames because a 1376x768 one took a Spark down
  there and nobody knows why from its logs. Never merge a LoRA into the saved `.tf-int8` copy. Raise them only by measuring from below on a worker Spark, in small steps,
  under a second watchdog that kills your own server at 25 GB available; never by trying the size that failed, and
  never on a machine with `vm.panic_on_oom=1`. Do not lower the floor to make a job fit.
- More than one DGX Spark (`app/sparks.py`, `app/static/sparks.js`, `docs/multi-spark.md`): a job's `sparks` counts
  participants including the head. The remote-only limit is `LIMIT`, 5; local workers can raise the combined chooser
  to 8. Only single clips (`video` jobs) go to a worker, one per worker at a time, through its own job API over an ssh tunnel
  the app opens and closes itself: a worker's app listens on its own 127.0.0.1 and is never opened to the network, and
  nothing here runs as root, edits `/etc` or shares a folder. A worker renders only with this Spark's code (the files
  of `CODE`, compared by hash; *Update and start* sends them with rsync) and its own memory guard, which is asked
  before a clip is sent. A worker silent for `LOST` seconds is marked down and its clip goes back to the queue; do
  not make that wait longer, and never retry a worker that went down without a person's *Check*. `MEASURED` is one
  measured run: change it only with a new one. One clip split across Sparks (the engine's `h3-cuda-multispark`
  branch) is measured and is not in the app: do not wire part of it in. The app on a Mac leads Sparks the same way
  (`docs/multi-spark.md`, *A Mac at the head*): `capable` hands a Spark only what it renders with its own settings (the
  presets and qualities its `/api/spark/self` lists; never a text encoder file of the Mac's), and a worker's `via` is
  a Spark the Mac reaches that logs in to the worker with its own key (`Relay`: a socket in `~/.cache/studio-sparks`
  there, ended with the Mac's ssh or its heartbeat). Never add the Mac's key to a worker for it, and never open the
  relay to a network.
- Local discrete NVIDIA GPUs (`app/local_gpus.py`, `docs/rtx-pro-6000.md`): at most four per Studio, including its
  explicitly selected head. Persist worker UUIDs and pin CUDA by UUID, keeping a validated numeric index for guard
  queries. Refuse a stale index after reordering; never substitute another card for a missing UUID. Display workers
  need explicit consent. An unticked but running worker still occupies a slot. Never kill a process merely because
  its port, GPU or PID matches: private launch identity
  and PID/start identity are required. Local child APIs require their private key even on loopback; never expose it
  in public state, URLs or logs. Children do not start workers, schedules, discovery, captions or source updates.
  Core runtime and weights are shared; worker homes and optional LoRA registries remain separate.
  Start shared-RAM admission only with a running local worker, preserving the single-card queue. Honor
  `memory_guard.floor_mb` / `SPARK_MEMORY_FLOOR_MB` and measured peak/error budgets. The plain int8 video host envelope
  is measured separately on a 96 GB card; outside its bounds retain the conservative unified estimate.
  Credit only identity-validated processes' anonymous PSS with an exact matching `comfy.kind` or private native
  model/weight-route marker, never file cache or unrelated processes. Recompute each active reservation's outstanding
  growth; unknown kinds run exclusively with FIFO admission. Fail impossible or stalled admission instead of waiting forever. Default leases/ledger use
  `~/.cache/studio-gpus` regardless of `XDG_RUNTIME_DIR`; explicit overrides must agree between Studios.
  Cancel and drain detached owned running work before releasing ownership; keep queued Director takes intact.
  Shutdown has a deadline and retains leases for undrained work; successful jobs keep model servers warm.
  Required placement never falls back; saved placement applies only to video, and preflight asks the selected worker.
  Tests isolate GPU runtime directories and inventory; actual hardware probes require `STUDIO_TEST_HARDWARE=1`.
  Three/four-card cases are deterministic simulations, not physical throughput measurements.
- Voice (`app/voice.py`, `app/static/voice.js`, README *Voice (Fish Audio Drama 3)*): Fish Audio Drama 3 runs on this
  machine only, as a TensorFold process outside the app, never imported into the app's venv. Never call Fish Audio's
  cloud API. `DRAMA3_BACKEND=mlx|cuda` (`mlx` on a Mac, `cuda` on a DGX Spark). Both engines run as a persistent
  server speaking one protocol (`Served`): `python -m tensorfold.drama3 --serve` in `$DRAMA3_VENV` (drama3-mlx
  92c09a8e or later; an older engine falls back to one process a take with `--stream`) and `drama3-generate --serve 1`
  (`$PREFIX/drama3/bin`, branch `drama3-cuda`; `DRAMA3_CUDA_BIN`, `DRAMA3_CUDA_MODEL`, `DRAMA3_CUDA_CODEC`). Loaded on
  the first take, the server holds `~/studio-gpu.lock.d` for as long as it is loaded, is unloaded after
  `DRAMA3_IDLE_S` (300) idle seconds and **before any other local job of the queue** (`voice.release`, called at the
  top of `server.perform`), on a Spark stops the studio's ComfyUI first when that holds the memory it needs, and stands
  under a memory kill-guard (`DRAMA3_GUARD_GB`, 8; MemAvailable, vm_stat on a Mac). Keep all of it: Drama 3 (42 GB on
  a Spark, about 50 on a Mac) and MiniMax H3 never run together. Answers are the JSON lines on its stderr. A take
  plays while it is made (`pcm` to `/api/voice/takes/{id}/live`). Dialogue goes a turn a line (`dialogue`; on one line
  the model drops later turns). The Mac engine chunks long text and checks its own words (`verify`, `stop_rule`:
  never turn them off); its count is the take's score, and a take with a part short while it played is asked again
  without streaming. On a Spark long text goes in parts (`pieces`, 60 words), and the app runs the Mac engine's check itself (`app/wordcheck.py`, copied from TensorFold's verify.py: keep the rules the engine's) on Whisper's transcript, drawing a take with missing words again, not played, `RETRIES` (2) times, the best kept. The Mac engine's server hears a clip to clone left without its words (8f3d4b33): the app then does not transcribe it first.
  `scripts/setup-voice.sh` (`oneshot-setup.sh --voice`) installs the Mac engine; on a Spark it only checks and prints
  the hand steps. A take with a reference clip needs `consent: true`; keep the consent box and the "Built with Fish
  Audio" line on the tab. A take sent as Ref2VA reference audio keeps MiniMax's 2 to 15 s per clip and 15 s per kind
  (`/api/voice/cut`). The weights are Fish Audio Materials (Fish Audio Community License v1.0): do not redistribute
  them, and say that a hosted TTS or cloning service needs Fish's own licence.
- Picture models (`IMAGE_MODELS` in `app/server.py`, `image_model` on scout and studio jobs, `IMAGE_MODEL` to
  `scripts/image.sh`): `qwen` (Qwen-Image-2.1-Turbo, every install), `qwen-full` (Qwen 2.1 Full: the 40-step base model at true CFG 4, where its weights are, `--image-edit`; on a Mac `qwen_image_generate.py --cfg`, never without guidance by default), `flux3k-fast` and `flux3k-full` (Black Forest
  Labs' FLUX 3 Image Klein, distilled 16 steps and base 50 steps at CFG 3.5, `oneshot-setup.sh --flux3`). Call them
  FLUX3-K in anything a person reads. FLUX3-K takes full scene captions: BFL's prompt upsampler always writes one
  first (`UPSAMPLE=0` only for a prompt that already is one). On a Spark it runs inside ComfyUI's workflow
  (`TextGenerate` with their settings, `flux3_workflow` in `spark/spark_generate.py`), never as standalone code; on a
  Mac on flux3-mlx in `$PREFIX/flux3-venv`, never in the Studio's venv. Edit a picture takes `image_model` too
  (`app/picture_edit.py`, `IMAGE_MODEL` to `scripts/edit.sh`): FLUX3-K edits are `flux3_workflow` in `spark/spark_edit.py`
  (ReferenceMultiLatent, upsampler with the i2i prompt) and `flux3_edit.py` on a Mac; LoRAs, angles and `base` are
  Qwen's and a FLUX3-K edit refuses them. FLUX Kommunity
  License: say so when asked about commercial use, and do not redistribute the weights.
- MOSS-Image (`moss-image` in `IMAGE_MODELS`, `oneshot-setup-spark.sh --moss`, `moss_workflow` in
  `spark/spark_generate.py`): OpenMOSS's model, Apache 2.0, inside ComfyUI's workflow (v0.42.0 and
  later, `COMFY_COMMIT`) on both machines (on a Mac the ComfyUI of the next rule, `oneshot-setup.sh --moss`, through
  `scripts/image.sh`), never as standalone code. It is offered only where its files and ComfyUI's
  `nodes_moss_image.py` are (`moss_ready`). The settings are ComfyUI's template's (25 steps er_sde, CFG 2.5, its
  negative prompt): change them only with new measurements. It does not edit pictures; the prompt goes as written.
- A Mac's ComfyUI (`mac/comfy.sh`, `scripts/setup-comfy.sh`, `oneshot-setup.sh --upscale | --remove | --moss`): Upscale,
  Find and Remove a person and MOSS-Image run the Spark's own graphs (`spark/spark_workflows.py`,
  `spark/spark_generate.py`, `MAC`) and the authors' node packs at the Spark's pins, on PyTorch's Metal device in
  `$PREFIX/comfy-venv`, never in the Studio's MLX venv and never as standalone model code. Keep
  `--use-pytorch-cross-attention`: ComfyUI's default attention on a Mac adds the text mask as a bias. MiniMax H3 is read
  from the Mac's own files through pointer files (`mac/comfy_files.py`, `mac/studio_files`): its fused q/k/v rows are
  regrouped into ComfyUI's order and its text encoder is cut after layer 50 (without either, the pictures are noise);
  the text encoder's tensors are assigned from the file, a LoRA's backup and an unloaded transformer go back to the
  file's pages, and a 3D convolution on Metal is a sum of 2D ones (PyTorch's own took 177 GB for nine frames). Each was
  measured as the reason a run failed or was killed: keep them. The server is stopped after every job
  (`mac_release`, `MAC_COMFY_KEEP=1` keeps it) and holds a kill-guard (`MAC_COMFY_GUARD_GB`, 16). The turnaround sheet
  stays the MLX engine's.
- Music (`app/music.py`, `app/static/music.js`, README *Music (MiniMax Music 3)*): MiniMax-Music3 makes a song from
  lyrics (section tags) and a style caption, on this machine only, never imported into the app's venv. The tab, every
  song and any page that plays one show "MiniMax-Music3" (its licence requires it in a commercial UI): keep it. On a
  Mac it is TensorFold's MLX port (`music3-mlx`, pinned in `scripts/setup-music.sh`, `oneshot-setup.sh --music`) as a
  persistent `--serve` process (`music.Mlx`, voice's `Served`); on a DGX Spark TensorFold's CUDA port
  (`music3-generate --serve 1`, branch `music3-cuda-118` 8cd08c83, the family on TensorFold 1.1.8 since 4.2.0, `spark/build-music3.sh` into `$PREFIX/music3`, carried
  prebuilt in the carrier's `spark-music3`; `music.Cuda`, the same `Served` protocol; int8 by default, `MUSIC3_WEIGHTS=bf16`;
  `MUSIC3_CUDA_BIN`, `MUSIC3_MODEL`) where its binary and the weights are, else ComfyUI's own nodes through
  `spark_generate.py music` (`music_workflow`, the blueprint's layout), never standalone code; `MUSIC3_BACKEND=cuda|comfy`
  chooses. The CUDA engine stops a studio ComfyUI that holds models before it loads. Either way it is loaded
  on the first song, holds `~/studio-gpu.lock.d` while loaded, is unloaded after `MUSIC3_IDLE_S` (300) and **before any
  other local job** (`music.release` at the top of `server.perform`), and Music, Drama 3 and H3 never run together; on a
  Spark a song starts from a ComfyUI with nothing else loaded and its models are freed with ComfyUI's /free.
  `memory_guard.music_check` (`MUSIC_ESTIMATE`, `MUSIC_MEASURED`; the CUDA engine's `MUSIC_CUDA_ESTIMATE`,
  `MUSIC_CUDA_MEASURED`) is a measurement: change it only with a new one.
  Laying a song under a clip (`music.lay`: soundtrack jobs, the Editor's export) ducks it under the clip's speech and
  keeps every peak under -1 dBFS: keep both. On a Mac a song plays while it is made (`pcm` to
  `/api/music/takes/{id}/live`, a stereo 44.1 kHz WAV; Voice's live WAV stays mono): its sound is held back until
  `start_s` (the measured no-stall start, `NO_STALL_S`, and `HOLD_MARGIN_S`) and `lead_s`; those are measurements, change
  them only with new ones. The kept song is the engine's finished WAV. A Spark does not stream, on the CUDA engine or ComfyUI's route.
  Lyric accuracy is Whisper's rough count on sung words.
- The brain engine (`app/bolt.py`, `app/bolt_leash.py`, README *The brain engine*): NVIDIA Nemotron 3.5 Bolt run by
  the Studio as the brains' local server, the lifecycle of Voice and Music: loaded on the first brain or agent request
  (`bolt.request`) on a loopback port, under the leash (it ends with the app) and a kill-guard (`BOLT_GUARD_GB`, 8;
  MemAvailable, vm_stat on a Mac), unloaded after `BOLT_IDLE_S` (600, `never` allowed) and **before any other local
  job of the queue whose measured peak does not fit beside it** (`bolt.admit` at the top of `server.perform`,
  `server.bolt_need`: the memory guard's estimate on a Spark, `MAC_PEAK_GB` on a Mac; unmeasured kinds unload it),
  then held until the queue is empty (`bolt.queue_empty` in `work`). A request in flight is never cut: a release
  waits for it. A local-harness turn's answers wait while Bolt is held (the turn's own clip renders with Bolt
  unloaded), and no idle unload runs while a turn is open (`bolt.turn`). Do not make the render wait for the turn: the
  turn waits for its clip in `wait_for_jobs`, so that deadlocks. It never takes `~/studio-gpu.lock.d` (a resident brain
  must not hold the updater or the render scripts). `BOLT_BACKEND=cuda|metal|llamacpp|external`: `cuda` is Mia's
  recipe (MiaAI-Lab/Nemotron-3.5-Bolt-TensorFold-DGX-Spark 89e1c8a, MIT) rebased onto TensorFold 1.1.8 (drowzeys/TensorFold
  `bolt-lilicorr-118`, since 4.2.0; `bolt-lilicorr-115` on 1.1.5 from 3.3.3), installed by `spark/build-bolt.sh` into `$PREFIX/bolt` (the previous engine kept in
  `$PREFIX/bolt.previous`), never run in Docker; `metal` is TensorFold's Metal port (branch
  `bolt-metal-118` on TensorFold 1.1.8 since 4.2.0, same command line), installed into `$PREFIX/bolt-metal` by `scripts/setup-bolt.sh`; `llamacpp` only when set (a Mac may
  run Bolt in its own llama.cpp service). The engine serves when the brains' address is empty or `studio:bolt`; a
  typed address stays external. Weights: NVIDIA's checkpoint a326822a and LiLiCorr drafter 4c6a5613
  (`scripts/setup-bolt.sh`, `--bolt`), OpenMDW 1.1. `NEED_GB`, `RESIDENT_GB` and `MAC_PEAK_GB` are measurements:
  change them only with new ones. Never start a second Bolt on a machine with `vm.panic_on_oom=1`.
- Sound effects and foley (`app/sound.py`, `sound_generate.py`, `scripts/setup-sound.sh`, `app/static/sound.js`): jobs
  `sfx` (Stable Audio Open 1.0, Stability AI Community License, gated on Hugging Face) and `foley` (MMAudio large_44k_v2,
  weights CC-BY-NC 4.0: non-commercial; say so when asked about commercial use) in `$PREFIX/sound-venv`, never the
  Studio's venv. Foley is laid under the clip's own sound (speech stays, peaks under -1 dBFS) unless asked to replace
  it. On a Mac Stable Audio Open's transformer runs in float16 and MMAudio's modules are built without random initial
  values (`sound_generate.py`; measured on an M5 Ultra: 8 s of sfx in 16 s instead of 32 to 35 s, the same take within
  0.1 dB of long-term spectrum; foley 17.6 s instead of 23.5 s, bit-identical); `SOUND_PRECISION=fp32` keeps float32.
  On a DGX Spark (`oneshot-setup-spark.sh --sound`) both go through the ComfyUI server of `spark/comfy.sh` as
  `spark_generate.py sfx|foley` (the same flags as `sound_generate.py`): ComfyUI's own Stable Audio nodes
  (`sfx_workflow`), and kijai's ComfyUI-MMAudio (pinned `MMA_COMMIT`) with the Studio's `StudioMMAudioSampler`
  (`spark/studio_mmaudio`, `foley_workflow`): kijai's own sampler reads one frame stream as both 8 and 25 fps, so keep
  the Studio's node, which picks frames by the clip's own rate. Never run either as standalone model code there. A
  kind renders on a Spark only when `memory_guard.SOUND_ESTIMATE` has its measured line (`sound_check`, asked when
  the job is queued and in `spark_generate.py` before anything loads); `SOUND_ESTIMATE` and `SOUND_MEASURED` are
  measurements, added only from runs on a worker Spark under the DGX Spark memory rules above.
- A card with memory of its own (an RTX Pro 6000 Blackwell, docs/rtx-pro-6000.md): every room check is the smaller of the
  machine's RAM and the card's free memory (`memory_guard.device`, `voice.mem_available_gb`); keep it that way, a
  check on RAM alone lets Drama 3 load beside a 62 GB ComfyUI on a 96 GB card. `STUDIO_VRAM_GB` pretends to be such a
  card on a Spark (it keeps the GB10's page-cache release). The engines build for the card's own sm (`TF_SM`).
- mflux, minimax-h3-mlx, flux3-mlx, TensorFold's music3-mlx, MMAudio and ComfyUI-MMAudio are pinned by commit and used at run time. Do not unpin.

**No personal information, ever:** no real email, phone number, home or postal address, account name, private IP,
host name, tailnet name, key or token goes into this repo: not in a file, not in a commit's author, committer or
trailer. Commit with a GitHub noreply address; in examples use stand-ins (`example.invalid`, `/Users/you`,
`192.168.1.10`, `tail1234.ts.net`). `tests/test_no_personal_info.py` and the workflow's commit check enforce it. People
reach the project through GitHub only: Issues, Discussions, and private vulnerability reporting (`SECURITY.md`).

**Credits, in the repo and on the page:** anything the repo or the studio uses (a model, LoRA, node pack, engine,
library, conversion, idea, prompt or someone's code) is credited in CREDITS.md in the same change that brings it in,
and the maker is named in the page's Credits list (`app/static/settings.js`); the page's *Every credit* panel shows
CREDITS.md and THIRD_PARTY_NOTICES.md themselves (`/api/credits`). `tests/test_credits.py` checks both.

**Credits:** keep CREDITS.md in sync. Qwen team (Qwen-Image-2.1), Black Forest Labs (FLUX 3 Image Klein), OpenMOSS (MOSS-Image), Stability AI (Stable Audio Open), Ho Kei Cheng et al. (MMAudio), kijai (ComfyUI-MMAudio), felix-ab (flux3-mlx), MiniMax (H3, MiniMax-Music3), NVIDIA (Nemotron 3.5 Bolt), MiaAI-Lab (the Bolt TensorFold recipe), Ash Hart (TensorFold), antirez (h3.c),
RobZombAI (H3MLX), mrbizarro (minimax-h3-mlx / Phosphene), Filip Strand and mflux contributors, Viggle, speach1sdef178, LightX2V,
NVIDIA Research, FastVideo, Sam Wasserman (Slate: the film workflow in Production; keep app/static/slate/NOTICE. Motion Previs Studio: the Motion Previs tab's analysis is a modified Python port of it; keep LICENSES/Motion-Previs-Studio-NOTICE, THIRD_PARTY_NOTICES.md and the headers of app/motion*.py, and keep the link to the original), Apple MLX. Authors only — no credit hyperlinks, except the link to Motion Previs Studio.
