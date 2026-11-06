# Mac and NVIDIA Linux installation

[Back to the README](../README.md)

Current platform setup references. For Windows (native only), use [Windows setup](windows.md), not these shell commands.

Moving to another computer? See [Moving an install](moving-an-install.md) for reusing model downloads,
preserving compiled packages on similar machines, and Windows/Mac/Linux format differences.

### Apple silicon

macOS 26 or later, for Metal 4. What runs depends on the memory: **96 GB or more** for video, voice, music and the
Bolt brain (64 GB up to 4.3.0), 48 GB for pictures only. Every figure was measured on a Mac Studio M5 Ultra
with 256 GB; smaller Macs and M1 to M4 follow from those measurements and are untested.

| Memory | M5 family | M1 to M4 | Disk free for `--all` |
|---|---|---|---|
| 48 to 95 GB | Pictures only (`--image-only`) | Pictures only (`--image-only`) | 40 GB |
| 96 GB | Pictures, FastH3 video, voice, music, sound, MOSS-Image, FLUX3-K, the Bolt brain | The same, video on the slower MLX engine, no Bolt | about 400 GB (440 on M1 to M4) |
| 128 GB | Everything above, plus MiniMax H3 Turbo and reference pictures to video (Ref2VA) | The same, slowly | about 530 GB |
| 192 GB or more | **Everything**, including Upscale and Remove a person (peaks of 125 and 135 GB) | The same, slowly | about 535 GB |

`bash oneshot-setup.sh --all --app` installs everything your Mac's memory runs and the double-clickable app. It says
which features it left out and why (training a LoRA always: it runs on a DGX Spark). Before that,
`brew install python@3.11 uv ffmpeg`.

### Install

The one-click install, `bash oneshot-setup.sh` with no flag (on a Mac, a DGX Spark or an RTX Pro 6000; `--app` adds the
double-clickable Mac app), contains: pictures (Qwen-Image-2.1-Turbo) and FastH3 video with sound, the essential LoRA
bundle, motion graphics (HyperFrames, about 400 MB), Finish (Tesseract and its converter, about 150 MB; the fonts
only on a DGX Spark, which finishes the cut on a Mac), phone notifications (`cryptography`, `pywebpush`) and the
speech-to-text for captions and the speech check (`faster-whisper`). `--all` adds every big model the machine runs
(Ref2VA, FLUX3-K, MOSS-Image, MiniMax Music 3, Fish Audio Drama 3, sound effects, Nemotron 3.5 Bolt, Motion Previs)
and, on a DGX Spark, training a LoRA. `--verify` reports each of these parts, and `--upgrade` moves the parts already
installed to this version.

Run in a terminal with no flag, the install first shows a checklist of every part the machine runs, each with its
download size, grouped into what the plain install includes and the larger optional models, with the total beside the
free disk space. Everything starts ticked: type a row's number (or `3 5`, or `4-6`) to untick or tick it, `n` to untick
every optional model, `d` to tick everything again, and press Enter to install what is ticked; the install prints the
same choice as flags so it can be run again without the list. `--yes` (or any flag) skips the list and installs as
before, with the plain install's parts; so does a run that is not in a terminal.
FLUX3-K and sound effects are gated on Hugging Face: before anything is downloaded the install checks that your
signed-in account (`hf auth login`) has accepted each one's licence. One that has not is left out with the page to
accept it, everything else installs, and the end of the run prints the command that adds it afterwards (`--verify`
reports it as waiting for the licence).

### Install flags

For `oneshot-setup-rtx-6000.sh` and `oneshot-setup-spark.sh`; `oneshot-setup.sh` takes the same names on a Mac
(`--train` is said and left out there). A DGX Spark does not set `STUDIO_GPU`. No flag is the core: pictures and
FastH3 video with sound (125 GB), motion graphics, Finish and phone notifications.

| Flag | What it installs |
|---|---|
| `--all` | Every feature below, about 400 GB (on a Mac, every feature its memory runs, without `--train`) |
| `--image-only` | Qwen-Image-2.1 only (26 GB) |
| `--ref2va` | Reference pictures to video (39 GB more) |
| `--swap` | The Library's Character Swap: MiniMax H3 Ref2VA in int8, two LoRAs and SAM 3.1 (23 GB more; 2.1 GB where the int8 Ref2VA is already there). DGX Spark and CUDA only |
| `--h3-base` | MiniMax H3's own transformer, for keyframe LoRAs and the Library actions (40 GB more) |
| `--image-edit` | Qwen-Image-2.1's 40-step model (14 GB more) |
| `--flux3` | FLUX3-K pictures (41 GB more, gated) |
| `--moss` | MOSS-Image (30 GB more) |
| `--music` | MiniMax Music 3 (22 GB more) |
| `--yue2` | YuE2 songs, the Music tab's default engine on Linux with an NVIDIA GPU (about 14 GB more; not on a Mac) |
| `--bolt` | Nemotron 3.5 Bolt, the studio's brain (about 25 GB more) |
| `--sound` | Sound effects and foley (11 GB more, gated) |
| `--motion` | Motion Previs analysis (0.5 GB) |
| `--voice` | Fish Audio Drama 3 (about 41 GB more) |
| `--train` | Train your own LoRA: musubi-tuner, MiniMax H3's video encoder and a training adapter (an 8 GB environment, its two files, about 6 GB, and, when `--h3-base` has not installed it, that 40 GB transformer; the CUDA build, a DGX Spark or a GPU, not on a Mac) |
| `--graphics` | Motion graphics: HyperFrames, GSAP, Node 22 when the machine's is older, and a browser (about 400 MB). In the default install; `--no-graphics` leaves it out |
| `--finish` | Finish: Tesseract 0.4.0 and Tesseract Converter 0.3.0, two fonts (about 150 MB; the fonts only on Linux ARM such as a DGX Spark). In the default install; `--no-finish` leaves it out. Tesseract is free under Mirage's own terms, not open source; the converter is MIT |
| `--no-render` | Skip the test clip |
| `--no-loras` | Skip the essential LoRA bundle (about 4 GB) |
| `--no-workflows` | Skip the Library action packs |
| `--yes` | No checklist: the plain install (or what the other flags say), as in a script |
| `--verify` | Check an existing install and download nothing |
| `--upgrade` | Move an existing install to this version and fetch no weights |

Finish (the final cut and mix, to Resolve, Premiere or After Effects) is also its own installer,
`bash scripts/setup-finish.sh` (`--verify` only reports). Details:
[Finish](production-guide.md#finish-the-final-cut-and-mix-to-resolve-premiere-or-after-effects).
## DGX Spark (CUDA) build

The same Studio (web app, scripts, one-shot install) also runs on an **NVIDIA DGX Spark** (GB10, 128 GB unified
memory, CUDA 13). FastH3's transformer runs there on **TensorFold's H3 family for CUDA**: the Mac engine's design
(int8 projections, tile-routed int8 attention with FastH3's own sparse selection) on the GB10's tensor cores, written
in Zig over the CUDA driver. **ComfyUI**, pinned, hosts the rest: the text encoder, the sampler, the decoders and
Qwen-Image-2.1. The header of the web app says which build you are in: a green `DGX SPARK · NVIDIA GB10 · H3-CUDA`
badge, or a purple `MAC · APPLE SILICON MX · H3-METAL`. On a GPU that header is `CUDA · <card> · H3-CUDA`.

**Thank you** to Ash Hart for TensorFold, FastVideo (Hao AI Lab) for FastH3 and its ComfyUI release, MiniMax for H3,
the Qwen team for Qwen-Image-2.1-Turbo, and the ComfyUI team for the runtime this build stands on.

```bash
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git && cd Imagination-Studio-by-keys-Wasserman
sudo apt install -y git ffmpeg
bash oneshot-setup.sh            # the same command as on a Mac: on Linux it runs oneshot-setup-spark.sh
                                 # the core install (125 GB free disk): models, engines, motion graphics, Finish,
                                 # a test image and a 5 second test clip; --all for everything (about 400 GB)
bash scripts/app.sh              # http://127.0.0.1:7870
```

ffmpeg is part of the install, and the installer stops without it. A Spark that was set up some other way (a copy of
another Spark's folders, say) may lack it: the app then starts, says so in its log and in the page's header, and
refuses the jobs that need it when they are queued. The 720p preset is not one of them any more: it generates
1280x736 and crops to 1280x720, and the crop is now made inside the ComfyUI workflow, before the clip is encoded
(one encode instead of two); in 2.6.5 it was an ffmpeg pass after the render, and a Spark without ffmpeg lost the
finished clip there. Without ffmpeg a clip also keeps ComfyUI's own sound track instead of the limited 256 kb/s one,
and the render says so.

#### Upgrade an existing install (DGX Spark)

```bash
cd Imagination-Studio-by-keys-Wasserman && git pull
bash oneshot-setup.sh --upgrade  # on Linux: oneshot-setup-spark.sh --upgrade
bash scripts/app.sh              # restart the app on the new version
```

`--upgrade` moves an existing install to the version of the clone: the environment's packages, ComfyUI at its pinned
commit, the engines (rebuilt or taken from the carrier only when their pinned commit changed), every optional part
already installed (Ref2VA, FLUX3-K, MOSS-Image, Music, Voice, sound, Bolt, training), the Motion Previs environment,
which it adds when it is missing, and motion graphics and Finish, which it adds to an install from before 3.2.0
(`--no-graphics`, `--no-finish` leave them out). Worker Sparks then take the head's code with **Sparks → Update and
start**. It **fetches no weights** (a model that is not on disk is named and left out; an image-only
install stays image-only), does not touch `~/ImaginationStudio` (clips, projects, LoRAs, settings), prints the version
before and after, and ends with `--verify`. Running it a second time changes nothing. Restart the app afterwards
(`bash scripts/app.sh`) so it serves the new pages. Run it when no clip is rendering.


The installer takes the prebuilt engine from the carrier image when Docker is there, and otherwise builds it from
source (`spark/build-engine.sh`: it fetches Zig 0.17.0 and the engine's source,
[drowzeys/TensorFold `h3-1.0.4`](https://github.com/drowzeys/TensorFold/tree/h3-1.0.4), and needs the CUDA toolkit's
`nvcc`, 12.9 or newer). Without it the Studio still works on ComfyUI's own blocks, slower.

Measured on one DGX Spark (2026-10-09, one run each; 124 frames, text to video, models already loaded):

| Size | Passes | TensorFold engine (int8) | A pass | ComfyUI's own blocks (bf16) | A pass |
|---|---|---|---|---|---|
| 864x480 | 8 | **73 s** | 7.1 s | 118 to 121 s | 10.5 s |
| 864x480 | 4 | **45 s** (from the pass time) | | 71 s | |
| 864x480 | 20 | **158 s** (from the pass time) | | 244 s (from the pass time) | |
| 1280x720 (rendered 1280x736) | 8 | **183 s** | 18 s | not measured | |
| 1344x768 | 8 | **about 195 s** | 20.5 s | 289 s | 29.4 s |

The engine's column includes ComfyUI's 8-bit video decoder, the default here: it decodes 124 frames of 864x480 in
14 s where the fp16 one takes 26 s (29 s against 42 s at 1344x768), and the picture matches the fp16 decode to 42 to
44 dB. `SPARK_VAE=minimax_h3_video_vae_fp16.safetensors` selects the fp16 decoder if you download it.

| Also | Time |
|---|---|
| A clip from an image, 864x480, 8 passes | 85 s |
| First clip after a start, 864x480, 8 passes | about 115 s (ComfyUI's blocks: 144 to 156 s) |
| Qwen-Image-2.1-Turbo image, 1344x768 | 9.5 s (20 s with the model load) |
| Three scout images, 864x480 | 23 s |

Lowest free system memory seen with the engine was 49 GB of 121 (35 GB on ComfyUI's blocks): the transformer is held
in int8, 21 GB instead of 42. The first use converts the checkpoint on the GPU (about 90 s) and saves the int8 copy
beside it (`….safetensors.tf-int8`, 21 GB of disk); later starts read that in a few seconds.

How close the engine is: on the same input with dense attention, the stream after all 50 blocks has cosine 0.998
against ComfyUI's bf16 blocks. With sparse attention the same seed gives the same scene as ComfyUI's sparse path,
and a repeat of a seed gives a bit-identical clip.

What is different from the Mac build:

- **Slower than an M5 Ultra**, about 1.35x a pass at 480p (7.2 s against 5.3 s).
- **FastH3 only.** No MiniMax H3 Turbo, and no 2x decoder, so the sizes are 864x480, 1280x720 and 1344x768.
- **Sparse attention at every size** (FastH3 was trained with it: a video tile keeps the best 20% of video tiles).
  `SPARK_SPARSITY=0` renders with dense attention, slower.
- **Clips from an image want the app's prompt.** The web app writes MiniMax H3's first-frame lead line and structured
  prompt; a bare one-line prompt with `FIRST_FRAME` gave a jump cut in a test (on ComfyUI's own sparse path too).
- `SPARK_ENGINE=comfy` renders on ComfyUI's own blocks in bf16 (dense attention under about 720p, its block-sparse
  kernel above). The 8-bit checkpoint through ComfyUI is no faster there (13.6 to 14 s a pass), and FastVideo's
  4-bit Spark recipe is not used: on the test machine it gave flying debris and results that changed between runs.
- **Why int8.** The GB10's tensor cores run int8 products at 245 TOPS and bf16 ones at 122 (measured on registers).
  PyTorch's stock int8 routine reaches about 52; the engine's own kernel 150 to 175 on FastH3's shapes.
- ComfyUI keeps running with the models loaded after a render, and quitting the app stops it.
  `bash spark/comfy.sh stop` frees the memory while the app stays up. It runs at low priority with a memory guard
  beside it, because a Spark that runs out can become unreachable: see *The memory guard* below.

### More than one DGX Spark: the Sparks setting

A job can use more than one DGX Spark. **Sparks**, a number from 1 to 8, is how many machines a job may use, the one
the app runs on counted as one. It is at the top of the Queue on every tab, in the last step of Create, on the
Production board and in a Slate shot; through the API it is `"sparks": 5` beside `kind` and `params`. The app shows
how many Sparks are there and able and uses that many: choose 8 with five machines and it says *5 of 8 available*
and uses five.

- **Several clips** (jobs in the queue, a Production board's segments, a Slate scene's shots) render one clip per
  Spark, side by side. Each clip comes back into this Spark's Library, project or film at the path it would have
  had here, with its seed, its settings and the Spark that rendered it; the QA gate and the shot check run here.
- **One clip** renders on one Spark. So do a long video (a chain of clips), pictures, edits and Library actions, on
  this Spark; the page says which and why before the job is queued.
- **The Sparks tab** is the list of the other Sparks: add one by the address you would type after `ssh`, and
  **Update and start** sends it this Spark's Studio code (rsync, as you; models are not sent) and starts its app.
  Each row shows whether the Spark answers, its Studio's version, memory and disk free, what it can render
  (FastH3 clips, Ref2VA, the image model, LoRAs, ffmpeg) and whether it is busy. It needs a key login to each Spark
  and the Studio installed there once; nothing runs as root.
- **From a Mac** the Studio leads the Sparks the same way. A Spark is given the single clips it renders with its own
  settings (FastH3 at the sizes made without the 2x decoder); MiniMax H3 Turbo qualities, the 2x decoder presets and a
  text encoder file of the Mac's stay on the Mac, and the page says why. A Mac that reaches only the Spark that leads
  the others gives it as **Through**: that Spark logs in to each worker with its own key, so no key is added to a
  worker. One-time setup and the measured run: [A Mac at the head](multi-spark.md#a-mac-at-the-head).
- **Safe by construction.** A worker's app listens only on its own `127.0.0.1`; this app reaches it through an ssh
  tunnel it opens and closes itself. One job runs on a Spark at a time. Every clip passes the worker's own memory
  guard before it is sent, and a clip with a LoRA goes only to a Spark that has the LoRA and whose guard accepts it.
  A worker that stops answering for 60 seconds is marked down and its clip is rendered elsewhere; cancel reaches
  the worker.

Measured through the app's own API on 2026-10-13, eight 56-frame 864x480 FastH3 clips, models loaded, one run each:

| Sparks | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Wall time, 8 clips | 263 s | 130 s | 98 s | 69 s | 65 s |
| Against one Spark | 1.00x | 2.03x | 2.70x | 3.81x | 4.05x |

Five were barely better than four for eight clips (two rounds of clips either way); the fifth pays from ten clips.
Six to eight are not measured: five machines exist here. One clip split across Sparks is measured outside the app
(1.40x, 1.73x and 1.77x on two, three and four) and is not built in. The whole of it, with what is not built:
[docs/multi-spark.md](multi-spark.md).

### The memory guard (DGX Spark)

On a Spark the GPU and the system share one pool of memory. On 2026-10-13 one clip with a LoRA at 1376x768 took a
worker Spark down: the app accepted it, and the server's check (once a second, against 4 GB) did not act. The guard
in `spark/memory_guard.py` replaces that check, in three layers that every way of starting a render goes through
(the app, `scripts/video.sh`, `spark_generate.py`, a worker's copy of this folder):

1. **Before a clip is queued, and again before anything is loaded**, its peak is estimated from measured peaks. A
   clip that would leave the machine under its floor is refused, with the reason and the nearest size and length
   that fit. In the app the settings step shows the same line before you queue; a clip that fits shows "about N GB of
   this machine's memory".
2. **While the server runs**, the guard reads the machine's memory twenty times a second. It stops the server when
   available memory is under the floor (a tenth of the machine: 12.4 GB; `SPARK_MEMORY_FLOOR_MB`), or is falling so
   fast that it would be under a third of the floor within a second, or when the kernel reports work stalled on
   memory. The job fails with that reason. It also gives the model files' page cache back when little memory is
   free (`SPARK_MEMORY_FREE_MB`, 8.3 GB): the GPU driver takes its memory from what is free, not from what is
   available.
3. The server is marked as the first process the kernel ends if memory runs out all the same.

| Measured on a 124.5 GB Spark, nothing else running (118.7 GB available) | Rows a pass | Taken at the lowest point |
|---|---|---|
| TensorFold int8 engine, 864x480, 124 frames | 15.4K | 57.6 GB |
| TensorFold int8 engine, 1376x768, 124 frames | 39.2K | 62.2 GB (62.1 to 64.2 in a production run) |
| TensorFold int8 engine, 1376x768, 192 and 243 frames (that run, sampled every 11 s) | 59.9K, 75.3K | 67.1 GB, 69.0 to 70.9 GB |
| With a LoRA merged into the int8 engine (2.7.1), 864x480, 124 frames | 15.4K | 57.6 GB |
| With a LoRA merged into the int8 engine, 1280x736 and 1376x768, 124 frames | 35.0K, 39.2K | 61.4 GB, 62.3 GB |
| With a LoRA merged into the int8 engine, 1376x768, 192 and 243 frames | 59.9K, 75.3K | 66.5 GB, 69.7 GB |
| With a LoRA on ComfyUI's own blocks (bfloat16; before 2.7.1), 864x480, 56 and 124 frames | 7.3K, 15.4K | 75.4 GB, 75.9 GB |
| The same, 1280x736, 22, 56, 90, 107 and 124 frames | 7.4K to 35.0K | 74.8, 75.8 to 76.2, 76.2, 76.5, 76.6 GB |

The estimate is a straight line through those points for each route plus 6 GB (no point is more than 1.7 GB from its
line; most are one run). Three things it does not know, said plainly:

- **A clip with a LoRA is refused above the largest one measured on its route.** Since 2.7.1 a LoRA on FastH3 is
  merged into TensorFold's int8 engine and takes what a clip without one takes (the table: within 0.6 GB at every
  size), measured from 864x480 up to 1376x768 at 243 frames (75.3K rows), as far as the engine itself was measured:
  every size the app offers, up to 10 s at the Sharp Spec size. On ComfyUI's own bfloat16 blocks (a Spark whose engine
  predates 2.7.1, or `--engine comfy`) it stays refused above 1280x736 at 124 frames (35.0K rows), whatever the line
  says. By that line the clip that took the Spark down needs 77 GB and leaves 41, and that is what the machine showed
  for a minute before it was lost, so its end is not explained by how much memory the clip takes and the size is not
  offered there. Its logs were never read; what is known is in the next paragraph.
- Reference clips (Ref2VA), MiniMax H3's own transformer and a Spark without the engine were not measured here; they
  are estimated on the bfloat16 LoRA line with their references' rows added.
- Pictures, edits and Library actions are not estimated: the running guard covers them.

What was found about the old check: memory falls far faster than once a second can follow (7.2 GB a second while
ComfyUI loads a model from the page cache, 13 GB in half a second while TensorFold's engine loads its weights), and
"available" counts the page cache. With a LoRA, ComfyUI loads until nothing is free: free memory sat at 1.2 to 2.5 GB
through every such render while 43 GB read as available, and the kernel logged the GPU driver being refused memory
(`NVRM ... NV_ERR_NO_MEMORY`) at 2 GB free and 54 GB available. With the guard beside the server the same renders
kept 8 GB or more free, took the same time (146 s against 145 s), and the driver logged nothing. Proof of the two
refusals, without a machine near its limit: the app and `scripts/video.sh` both refuse the lost clip before anything
loads, and a server started with `SPARK_MEMORY_FLOOR_MB=70000` was stopped during a small clip's load at 69.8 GB
available (lowest point 69.77 GB while memory fell at 28 GB a second; gone in 0.5 s, the job failed with the reason).
`python3 spark/memory_guard.py check --width 1376 --height 768 --frames 124 --lora` prints a verdict.

**Refusals on an idle Spark with a newer NVIDIA driver (590 and later).** These drivers keep the memory a CUDA
process frees in their own system memory pools after it ends, and the kernel counts it nowhere: not as available,
not as cache, not as any program's. ComfyUI stops before some renders, so the next clip can be refused for memory that
is not in use, often with a whole batch failing within seconds (issue #59). `/proc/driver/nvidia/params` shows
`EnableSystemMemoryPools` set to a non-zero value. When a refusal finds much of the machine held outside every program
and no GPU program running, it says so. Two fixes:

- Install the driver NVIDIA validates for the DGX Spark, the R580 branch (the DGX Dashboard's update, or DGX OS's own
  packages). The studio's Sparks run 580.173.02, which keeps no such pools.
- Or turn the pools off: `echo "options nvidia NVreg_EnableSystemMemoryPools=0" | sudo tee /etc/modprobe.d/nvidia-no-sysmem-pools.conf`,
  then `sudo update-initramfs -u` and restart the Spark. Memory comes back within a second of a process ending.

Leave `nvidia-persistenced` running: on a Spark without a display, stopping it broke GPU start-up
(`RmInitAdapter failed`). `sudo sysctl vm.drop_caches=2` gives the pooled memory back once, without a restart.

Longer clips on the engine: 10 seconds at 1280x720 (243 frames) took 444 s, a pass 43.7 s, against 378 s on an M5 Ultra
for the same prompt; a 30 second chained clip at 864x480 (two 15 second parts) took 611 s.

Not tested: clips longer than 15 seconds in one piece, chains longer than 30 seconds, a download of the models from nothing
(the files were already on the test machine), any GPU other than the GB10, and two Sparks working on one clip.

### A Spark shared with other work: the workload governor (optional)

A Spark that also runs other model servers can have a machine-wide workload governor: a small service that hands
out memory reservations through an admission socket, so two heavy jobs never start on the same memory. Point the
studio at its folder with `SPARK_GOVERNOR` and the studio takes part (`spark/workload.py`); leave it unset and nothing
changes.

- **The renderer starts inside a reservation of its own** (`spark/managed_comfy.py`, used by `spark/comfy.sh`). It
  first releases its own reservations whose servers have ended, waits for the governor to allow the start, then
  starts ComfyUI with the same memory guard beside it and the governor's telemetry as well: memory under the floor, a
  GPU at 90 C or throttling, a platform zone near its trip point or a minute of memory pressure stop the server, and
  the render says why. `bash spark/comfy.sh stop` ends that server only (each port has its own
  `$PREFIX/managed-comfy-<port>.json`).
- **Every job waits for a cool, calm start** (`spark/start_gate.py`, before each submission): the server must still
  hold its reservation, the GPU must be under 80 C and memory not under pressure. It waits up to three minutes, then
  the job fails with the reasons.
- **Drama 3 too**, with `DRAMA3_MANAGED_GUARD=1` (`spark/managed_voice.py`): the engine starts in its own
  reservation, after the studio's renderer is stopped when idle (never while it renders), keeps its own 8 GB
  kill-guard, and each part of a take waits for the same start checks.

`SPARK_GOVERNOR_MIN_FREE_GIB` (12, never lower), `SPARK_GOVERNOR_RESERVE_GIB` (85 for a render),
`SPARK_GOVERNOR_VOICE_GIB` (55), `SPARK_GOVERNOR_OWNER` and `SPARK_GOVERNOR_VOICE_OWNER` (the names it reserves
under), `SPARK_GOVERNOR_SOCKET`, `SPARK_GOVERNOR_FREE_URL` (another ComfyUI on the machine whose idle model cache is
freed before a reservation; a busy one is never touched) and `SPARK_THERMAL_ZONES` adjust it. `spark/workload.py`
says what the governor folder must hold.

`DELIVERY_AUDIO_RATE=48000` (also 44100 or 96000) delivers a Spark clip's sound as stereo AAC at that rate for an
editor's timeline; ComfyUI's H3 decoder makes 32 kHz. The picture is copied unchanged, and resampling adds no detail.
## Install, and the one-click app

Everything the studio makes and keeps (renders, projects, uploads, voice, music, LoRAs, settings) is in
`~/ImaginationStudio`, or in `STUDIO_HOME` when that is set. An install from before the name kept it in
`~/TensorFoldStudio`: the studio moves that folder once to `~/ImaginationStudio` when it starts (a rename on the same
disk, nothing copied) and leaves `~/TensorFoldStudio` as a link to it (`app/studio_home.py`).

### Minimum requirements

| What you want to run | Memory | Disk | Chip |
|---|---|---|---|
| Images only (`--image-only`) | **48 GB** | 33 GB | Any Apple silicon; M5 for the int8 kernels |
| The default: images and FastH3 video, voice, music, the Bolt brain, the web app | **96 GB** | 175 GB | M5 family: the native engine, fastest |
| The default on an M1 to M4 | **96 GB** | 212 GB | FastH3 on the MLX engine with 8-bit weights, much slower |
| With `--turbo`: MiniMax H3 Turbo and its 20-step modes as well | **128 GB** | 240 GB | M5 family for the fast path |
| With `--ref2va`: reference pictures to video (MiniMax H3 Ref2VA) | **128 GB** | 64 GB more | M5 family for the fast path |

The setup enforces the memory figures (the default's was 64 GB up to 4.3.0; under 96 GB it now offers
`--image-only`). All of this was measured on one machine, a Mac Studio M5 Ultra with 256 GB: the figures below are what each stage of a job used there (`/usr/bin/time -l`, peak memory footprint), not a run
on a smaller Mac.

| FastH3 job, native engine (M5 Ultra, 2026-10-08) | Prompt export | Passes | Decode | **Peak** |
|---|---|---|---|---|
| 864x480, 5 s, from text | 27.7 | 23.0 | 14.0 | **28 GiB** |
| 864x480, 5 s, from an image | 27.9 | 23.1 | 14.0 | **28 GiB** |
| 864x480, 5 s, 20 passes | 28.0 | 23.2 | 13.9 | **28 GiB** |
| 864x480, 15 s | 27.8 | 28.2 | 16.9 | **28 GiB** |
| 1280x720, 5 s | 27.8 | 26.4 | 15.8 | **28 GiB** |
| 1280x720, 10 s | 27.9 | 32.1 | 19.6 | **32 GiB** |
| 1280x720, 5 s, 2x decoder to 2560x1440 | 27.8 | 26.4 | 32.1 | **32 GiB** |

The three stages run one after another, so a job's peak is its largest stage. An image job peaks at 28 GiB
(1344x768). Before 2.1 the same FastH3 jobs peaked at 48 to 69 GiB. MiniMax H3 Turbo reaches 103 GiB while its
adapter is merged, which is why `--turbo` asks for 128 GB.

A chip without tensor units (M1 to M4) cannot use the int8 kernels or the native engine. Emulated on the M5 Ultra by
switching both off (MLX engine, FastVideo's SIMD-group routing, float decoder), 8 passes:

| FastH3 on the MLX engine, as an M1 to M4 runs it | Peak | A pass (on the M5 Ultra) |
|---|---|---|
| bfloat16 weights, 864x480, 5 s (before 2.2) | 64.0 GiB | 29.6 s |
| **8-bit weights**, 864x480, 5 s, from text | **37.1 GiB** | 31.2 s |
| **8-bit weights**, 864x480, 5 s, from an image | 34.3 GiB | 39.6 s |
| **8-bit weights**, 1280x720, 5 s | 39.6 GiB | 107 s |

The last two rows were run with a smaller buffer cache (4 GiB instead of the 8 GiB that ships), which is slower by
about a tenth and peaks about 3 GiB lower. Against the bfloat16 render of the same seed the 8-bit clip shows the
same scene, person and framing with different fine detail (18 dB between the two: few-step sampling amplifies any
small change, as it does between bfloat16 and int8 on the M5).

| Machine | Status |
|---|---|
| Mac Studio M5 Ultra, 256 GB, macOS 27 | **Tested.** The Mac measurements in this section are from this machine. |
| M5 Max or M5 Pro, 96 GB or more | Untested. Meets the memory check for the default install with room to spare; less GPU, so expect longer times. |
| M5 family, 48 to 95 GB | Untested. Images only (`--image-only`); the setup refuses the default install (video, voice, music) under 96 GB, though a video job's measured peak is 32 GiB. |
| M1 to M4, 96 GB or more | Untested on real hardware; emulated on the M5 Ultra. No tensor units: FastH3 runs on the MLX engine with 8-bit weights, about six times slower a pass than the native engine even on an M5 Ultra's GPU, and slower again on an older one. |
| macOS 26 | Untested. The kernels need Metal 4, so it is the likely minimum. |

Installed size: about 175 GB, nearly all model weights (FastH3 65 GB, MiniMax H3's text encoder and decoders 72 GB,
Qwen-Image-2.1-Turbo 31 GB, the 2x decoder 6 GB, Python environment 1.3 GB). `--turbo` adds 64 GB
(MiniMax H3's transformer 62 GB and the Turbo adapter 2 GB). The Studio's own code and the native engine are a few
megabytes. On an M1 to M4 the setup also writes the 8-bit FastH3 weights (37 GB); `Q8_ONLY=1` then deletes the
65 GB they were made from. If you run it on another machine, a report of the chip, memory and times is welcome as an issue.
Before each download the setup searches this machine for the same weights, models and LoRAs, and a copy it finds
elsewhere (another folder, a disk, a Hugging Face cache) is linked where the Studio looks instead of being downloaded
again (`python3 app/discover.py --scan` shows the table; nothing is moved or copied). The Studio itself searches again
shortly after every start, on a Mac and on a DGX Spark alike, with nothing to configure, and links what it can prove is
the same file. **Machine → Models & weights** lists what it found, what it linked and what still needs a download or a
conversion, with *Use it* and *Unlink*.

```bash
brew install python@3.11 uv ffmpeg
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
cd Imagination-Studio-by-keys-Wasserman
bash oneshot-setup.sh --app             # engine, image model, FastH3, 2x decoder, motion graphics, Finish, the app; ends with a test clip
bash oneshot-setup.sh --turbo --app     # the same plus MiniMax H3 Turbo (64 GB more on disk, 128 GB of memory)
bash oneshot-setup.sh --ref2va --app    # the same plus reference pictures to video (64 GB more on disk, 128 GB of memory)
```

#### Upgrade an existing install (Mac)

```bash
cd Imagination-Studio-by-keys-Wasserman && git pull
bash oneshot-setup.sh --upgrade         # add --app to rebuild the double-clickable app as well
```

On a Mac under 96 GB with an install made under the former 64 GB minimum, `--upgrade` says that the minimum is now
96 GB, upgrades and keeps what is installed, and adds nothing that needs 96 GB. Video keeps running; the Voice and
Music tabs say they need 96 GB.

`--upgrade` moves an existing install to the version of the clone: it replaces the TensorFold engine and the native
FastH3 engine in the install's own environment when they are older than this version pins, copies the render
scripts, moves the optional parts it finds installed to this version's pins, adds the Motion Previs environment when it is missing (0.5 GB), and adds motion graphics and Finish to
an install from before 3.2.0 (about 550 MB; `--no-graphics`, `--no-finish` leave them out). It **fetches
no weights**: a model that is not on disk is named and left out, and an image-only install stays image-only. It does
not touch `~/ImaginationStudio` (clips, projects, LoRAs, settings), prints the version before and after, and ends with
`--verify`. Running it a second time changes nothing. Quit and reopen the app afterwards. A plain
`bash oneshot-setup.sh` after `git pull` upgrades the engine too, and also fetches any model the install lacks.


**Step-by-step tutorial with screenshots: [TUTORIAL.md](../TUTORIAL.md).**

Then **double-click Wasserman Slate AI Studio** on the Desktop or in `~/Applications` (drag it to the Dock if you like). `bash scripts/make-app.sh` rebuilds the app on its own. It starts the studio
if it is not running and opens it in your browser; double-click again to come back to it. Without the app:
`bash scripts/app.sh`, then open http://127.0.0.1:7870.

**Or hand it to your coding agent.** Paste this:

> Clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman and follow its AGENTS.md section "Install for a
> person and build the one-click app". Tell me before any large download starts and when the app is ready.

`--turbo` adds MiniMax H3's own transformer and its Turbo adapter, and with them a second engine card in the app;
`--image-only` installs the image model alone (33 GB). An install made before 2.1 keeps Turbo. The app keeps the clone where it is: rebuild it with `make-app.sh` if you move the folder. To
stop the server: `"$HOME/Applications/Wasserman Slate AI Studio.app/Contents/MacOS/TensorFoldStudio" stop`. There is **no
login**, and by default it listens on this Mac only; `HOST=0.0.0.0 bash scripts/make-app.sh` opens it to your
network, which you should do only on a network you trust.

### Updating Studio

Open **Updates** in the header, choose **Check for updates**, then **Update and restart**. The page reconnects after
the restart and offers **Reload Studio** when you have saved your work. The update source is the GitHub repository
you installed (`origin`), and Studio follows the branch you have checked out: the branch it tracks on `origin`, or the
repository's default branch when it tracks none. The Updates panel shows the installed version and commit, the branch
it follows, and the newest GitHub release of that repository for information; what is installed is always the latest
commit of the followed branch. A fork follows its own branches; it does not automatically merge its parent.

A development checkout updates the same way: check out the development branch so that it tracks `origin` (for
example `git switch --track origin/<branch>`), and the panel follows that branch. To follow a particular branch
whatever is tracked, start Studio with `STUDIO_UPDATE_BRANCH=<branch>`; the checkout must be on that branch. A branch
that tracks another remote, or a local branch, is not updated from the app.

**Automatic updates** is on by default, so an install stays on the latest version: Studio checks every six hours while
it is open and installs when its queue and render engine are idle. Turn it off in Settings → Updates; the setting is
saved for this installation. Updates preserve projects, media,
models and runtime environments. Updates only move the branch forward: local code changes, untracked files, commits
of your own that are not on GitHub, or a detached checkout pause updates without discarding work. Updates requiring changes to installation
dependencies show a message asking you to run the installer first, as 3.1 to 3.2 does: `git pull && bash
oneshot-setup.sh --upgrade` in the clone (on a Mac or a DGX Spark), then restart the app (quit and reopen the Mac
app, or `bash scripts/app.sh`). See *Upgrade an existing install* under each build.

Start Studio using `bash scripts/app.sh` or the existing Mac app launcher to support an update and restart. The launcher
waits for its server to exit before changing source, saves the old commit under `refs/studio-backups/`, and checks the
new server's startup. If startup fails, it restores the previous code and turns automatic updates off. It does not
discard files changed by somebody during recovery. Git archive downloads and an app started directly with
`python app/server.py` can still run; use a Git clone and `scripts/app.sh` for in-app installation of updates. Each
checkout runs one launcher. Manage update controls on the local computer, through an SSH tunnel, or through its
Tailscale address; an exposed LAN app cannot manage source updates.

Worker Sparks keep the existing **Sparks → Update and start** workflow. They do not independently auto-update to
different code while the head Studio is dispatching work. Model and engine installation remains a separate setup
step; this updater never downloads weights, changes memory guards, or runs setup scripts.
