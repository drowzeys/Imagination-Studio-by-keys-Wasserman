# Imagination Studio by keys+Wasserman powered with TensorFold (Mac + DGX Spark)

*Formerly keys+Wasserman Slate AI Studio, built with integrated Fish Audio Drama 3.*

**Keys:** [X @u1tra_instinct](https://x.com/u1tra_instinct) · [GitHub](https://github.com/drowzeys)

**Sam Wasserman / Wasserman Productions:** [wassermanproductions.com](https://wassermanproductions.com) ·
[X @SamJWasserman](https://x.com/SamJWasserman) · [GitHub](https://github.com/wassermanproductions)

**An AI film studio on your own machine: plan a film the way a crew does in the Wasserman Slate AI Studio, and render
it here. Pictures from Qwen-Image-2.1-Turbo, FLUX3-K (Black Forest Labs' FLUX 3 Image Klein) or MOSS-Image, MiniMax H3 / FastH3
video with sound and Fish Audio Drama 3 voices, on TensorFold: Apple-silicon Macs (Metal) and the NVIDIA DGX Spark
(CUDA).**

![Imagination Studio by keys+Wasserman powered with TensorFold: a local creative all-in-one studio for NVIDIA DGX Spark or Apple silicon](docs/screenshots/imagination-studio.webp)

*Named at the request of the TensorFold project: this is an app built on TensorFold, not a TensorFold product.*

*Name, credits, colours and voice for anything public: the [brand guide](docs/BRAND.md).*

*Credit first: **Keys (drowzeys)** built this studio and its engines; **Sam Wasserman and Wasserman Productions** helped
design and build the studio in collaboration with Keys, made Wasserman's Filmmaker Suite, and shaped the production side (Slate, the Sharp Spec, Motion Previs). See
[Big credit: Sam Wasserman and Wasserman Productions](#big-credit-sam-wasserman-and-wasserman-productions-wassermanproductionscom), below the release notes, and [CREDITS.md](CREDITS.md).*

**3.0.1** · Apple-silicon Macs (measured on a Mac Studio M5 Ultra 256 GB) and the NVIDIA DGX Spark ·
[TensorFold](https://github.com/ashhart/TensorFold) **0.6.5** + two families, and TensorFold **1.0**'s native Zig
runtime for FastH3 on Metal and on CUDA:
**Qwen 2.1 Turbo**, **Qwen 2.1 Full**, **FLUX3-K Fast** and **FLUX3-K Full** (text to image, and Edit a picture) · **MOSS-Image** (text to image, DGX Spark) · **MiniMax H3**
(video with sound) · **Fish Audio Drama 3** (Voice) · **MiniMax-Music3** (Music: songs with sung vocals) · int8 kernels on the M5 tensor units and on the GB10's tensor
cores · a 2x video decoder for 2K finals on the Mac

## New in 3.0.1

- **MOSS-Image**, a fifth picture model, on a DGX Spark: OpenMOSS's 5B model (Apache 2.0, commercial use allowed),
  through ComfyUI's own support. It is the best of the picture models at lettering: posters, title cards, signs and
  menus came out exactly as written, in English and Japanese. About 45 s for 1344x768. Install it with
  `oneshot-setup-spark.sh --moss` (30 GB); see [MOSS-Image on a DGX Spark](#moss-image-on-a-dgx-spark).
- **ComfyUI moves to v0.42.0** (`b9b290e4`) on a DGX Spark, for MOSS-Image: `oneshot-setup-spark.sh --upgrade` moves
  an existing install. FLUX3-K, Qwen and FastH3 clips render as before on it.
- **Credits:** OpenMOSS in the README, CREDITS.md, NOTICE and the app's About.
- Nothing changes on a Mac: MOSS-Image has no Mac build yet.

## New in 3.0 (since 2.9.3)

**A new studio around the same engines**
- **Imagination Studio**: a new look (the poster's serif, blue, green and amber; Geist and Instrument Serif), Sam's
  art on every engine, card and look, and a sidebar of **Creative Engines** and **Studio Vaults** with Home on top.
- **Six engines, one page each**: Story, Image, Video, Previs, Sound and Edit, each opening on its own cards.
- **Home**: one composer makes a clip, a picture, a voice line or a song straight away; start cards, recent takes,
  the machine in one line.
- **Settings**: the look, which rooms show, what a new take starts from, every connection, and Models & weights,
  Sparks, Brains & tools and Updates in one place.
- **Made for phones too**: the whole studio works from a phone's browser. The six engines sit along a bottom bar,
  a **More** sheet holds the Agent, the vaults, Settings, search and updates, the queue folds into one line above it,
  and *Add to Home Screen* opens it full screen like an app. See [Using it on your phone](#using-it-on-your-phone).

**Agents and your own sign-ins**
- **The Agent room**: pick Claude Code, Codex, Hermes, Gemini, Grok or a local model and talk it through; it keeps
  its own session, asks when a choice matters and drives the studio's tools (stills, clips, voice, songs, the
  timeline), Blender and Blockout.
- **One-click Connect** for every agent, from any page that needs one: finds it or installs it, runs its own
  sign-in (the page opens by itself, with the code, a QR code and Copy link), checks it answers, makes it the default.
- **Link another computer**: a DGX Spark uses the agents signed in on your Mac over SSH, and the studio sets up both
  directions itself. Claude Code's Mac Keychain over SSH is solved with **Allow over SSH** (`BRAINS.md`).
- The ChatGPT app's own Codex is used when the one on PATH is older, here or over SSH.

**Models, looks and installs**
- **Models & weights** finds every model the studio needs anywhere on the machine and links it instead of
  downloading it again (**Use everything found here**), and **Install** runs the right installer for what is missing.
- **Gated weights** (Keyframe Animation, 3D Animation Style, Stable Audio Open): Install signs you in to Hugging Face
  once, opens the licence page, and downloads by itself once access is granted.
- **Looks**: the LoRA hub as a style library, one card a look.

**Sound, previs and the library**
- **Sound effects and foley**: MMAudio makes a clip's own sound from its picture, Stable Audio Open makes a sound from
  words (Mac; on a DGX Spark through ComfyUI once its memory is measured).
- **Motion Previs from a link**: paste a video page or a video file's address and its depth, pose and camera are read.
- **The Library** lists voice lines, songs and sound effects beside clips and stills, each with its own filter.
- **Nodes**: a ComfyUI-style canvas over the studio's own jobs, run in order.
- **Wasserman's Filmmaker Suite** built in: bring work in from Sam's apps, send the cut to Resolve as a ZIP with media.

**Machine, updates and safety**
- **Machine**: live GPU, memory, temperature, power and storage; Sparks; an optional workload governor for a shared Spark.
- **Updates** in the app: follows the installed branch, waits for idle, shows the latest release.
- Everything is kept in **`~/ImaginationStudio`** (an older `~/TensorFoldStudio` is moved there once, with a link left behind).
- Tighter safety: this computer or its Tailscale name only, same-origin writes, size limits, no hidden files served.

## Screenshots

| | |
|---|---|
| ![Home: the composer, start cards and the machine](docs/screenshots/home.webp) | ![Story: the film board, Wasserman Slate, First AD](docs/screenshots/story.webp) |
| **Home** | **Story** |
| ![Image: stills, scouts, edits and looks](docs/screenshots/image.webp) | ![Video: clips with sound, long takes, references, Sharp Spec](docs/screenshots/video.webp) |
| **Image** | **Video** |
| ![Previs: Motion Previs and your agent](docs/screenshots/previs.webp) | ![Sound: voice lines and songs](docs/screenshots/sound.webp) |
| **Previs** | **Sound** |
| ![Edit: timeline, Resolve, dailies, Library](docs/screenshots/edit.webp) | ![Nodes: a canvas over the studio's jobs](docs/screenshots/nodes.webp) |
| **Edit** | **Nodes** |
| ![Looks: the LoRA hub as a style library](docs/screenshots/looks.webp) | ![Looks: one card a look, with its licence and size](docs/screenshots/looks-cards.webp) |
| **Looks** | **Looks, the cards** |
| ![Wasserman's Filmmaker Suite, built in](docs/screenshots/filmmaker-suite.webp) | ![Machine: live readings and storage on a DGX Spark](docs/screenshots/machine.png) |
| **Wasserman's Filmmaker Suite** | **Machine** |

## Using it on your phone

The models run on your Mac or DGX Spark; the phone is only the screen and the remote. Everything works from it:
write a prompt, roll a clip, watch the queue, play takes from the Library, plan in Story, talk to your agent.

1. **Put the computer and the phone on the same [Tailscale](https://tailscale.com) network** (free for personal use):
   install Tailscale on both and sign in with the same account.
2. **Share the studio on that network**, once, on the computer running it:

   ```bash
   tailscale serve --bg 7870
   ```

   Tailscale prints the address, `https://<computer>.<your-tailnet>.ts.net`. Only devices on your own tailnet can
   open it, over HTTPS, and the studio accepts its Tailscale name by itself. (On a trusted home network,
   `HOST=0.0.0.0 bash scripts/app.sh` and `http://<computer's address>:7870` work too; there is no login, so never on
   a shared or public network.)
3. **Open that address on the phone** in Safari or Chrome, then *Share → Add to Home Screen* (iPhone) or *⋮ → Add to
   Home screen* (Android). The studio then opens full screen from its own icon.

On the phone:

- **Bottom bar**: Story, Image, Video, Previs, Sound and Edit, one tap each. **More** opens the Agent, the Library,
  Looks, Wasserman's Filmmaker Suite, Nodes, Machine, Learn, Settings, search and updates.
- **Home** keeps the composer on top: pick *Clip with sound*, *Picture*, *Voice* or *Song*, describe it, *Generate*.
- **The queue** is the line above the bar; tap the arrow to see what is rolling and what is next. Renders keep going
  on the computer when the phone sleeps or leaves.
- **Connect an agent from the phone**: *Connect* shows the sign-in code in large type with *Copy code* and *Copy
  link*, and a QR code; sign in on the phone itself, or scan the QR with another device.

| ![Home on a phone: the composer, the queue line and the bottom bar](docs/screenshots/phone-home.webp) | ![The More sheet: the Agent, the vaults, Settings, search and updates](docs/screenshots/phone-more.webp) | ![The Video engine on a phone](docs/screenshots/phone-video.webp) |
| :---: | :---: | :---: |
| **Home** | **More** | **An engine: Video** |

## License & credits

**Apache License 2.0** — see [LICENSE](LICENSE). Free to use, modify, fork and build on, commercially or otherwise;
the models it downloads keep their own licences (below).

**Attribution required:** per the [NOTICE](NOTICE) file (Apache 2.0 §4(d)), any use, fork or redistribution must retain
the NOTICE file and credit **Keys ([github.com/drowzeys](https://github.com/drowzeys))** and **Sam Wasserman / Wasserman
Productions ([wassermanproductions.com](https://wassermanproductions.com))** in its documentation and about/credits
surface. To cite it: [CITATION.cff](CITATION.cff).

The same credits the studio shows in Settings → About; [CREDITS.md](CREDITS.md) has each one in full.

- **Keys (drowzeys)** — the studio itself, its TensorFold families and int8 kernels, and its Mac and DGX Spark engines.
- **Sam Wasserman and Wasserman Productions** — helped design and build the studio in collaboration with Keys;
  Wasserman's Filmmaker Suite (ScriptBreak, Cork Board, Master Canvas, Storyboard Reference Studio, Blockout, Motion
  Previs Studio, Slate, Circle Take, Stem Studio, Call Sheet, DaVinci MCP), the Sharp Spec, the 80s Horror LoRA and the
  studio's art.
- **Qwen team** — Qwen-Image-2.1. **Black Forest Labs** — FLUX 3 Image Klein (FLUX3-K). **OpenMOSS** — MOSS-Image.
  **MiniMax** — MiniMax H3 and MiniMax-Music3. **Built with Fish Audio** — Fish Audio Drama 3.
- **Stability AI** — Stable Audio Open · **Ho Kei Cheng et al.** — MMAudio · **kijai** — ComfyUI-MMAudio (sound effects
  and foley).
- **Ash Hart** — TensorFold.
- **antirez** — h3.c · **RobZombAI** — H3MLX · **mrbizarro** — minimax-h3-mlx (Phosphene) · **Filip Strand and the mflux
  contributors** · **felix-ab** — flux3-mlx.
- **Comfy Org** — ComfyUI · **Apple** — MLX · **NVIDIA Research** · **FastVideo** · **LightX2V** · **Viggle** ·
  **speach1sdef178** · **rookiestar28** · **ddalcu**.
- **The agents you connect** — Claude Code (Anthropic), Codex (OpenAI), Gemini CLI (Google), Grok CLI (xAI), Hermes
  Agent (Nous Research); Ollama, LM Studio, vLLM, llama.cpp; Blender and blender-mcp.
- OpenAI **Whisper** and **faster-whisper**, **yt-dlp**, **FFmpeg**, **PyTorch**, Hugging Face **diffusers** and
  **transformers**, **FastAPI**, **Geist** and **Instrument Serif**.
- **The community LoRA authors**, by name in Looks and in [CREDITS.md](CREDITS.md).

**Licences.** The studio's code is Apache-2.0. The models keep their own terms; do not redistribute their weights.
Qwen-Image-2.1: Qwen Research License (non-commercial) · MiniMax H3: MiniMax H3 Community License (territory limits) ·
MiniMax-Music3: its licence asks for its name in a commercial UI · FLUX3-K: FLUX Kommunity License · MOSS-Image:
Apache 2.0 (commercial use allowed) · Fish Audio Drama
3: Fish Audio Community License v1.0 (a hosted voice service needs Fish's own licence) · Stable Audio Open 1.0:
Stability AI Community License (free under US$1 million of yearly revenue) · MMAudio's weights: CC-BY-NC 4.0,
non-commercial · Wasserman's Filmmaker Suite: each app under its own licence, Apache-2.0 (Master Canvas: MIT).

**Links.** Keys: [X @u1tra_instinct](https://x.com/u1tra_instinct) · [GitHub](https://github.com/drowzeys).
Sam Wasserman / Wasserman Productions: [wassermanproductions.com](https://wassermanproductions.com) ·
[X @SamJWasserman](https://x.com/SamJWasserman) · [GitHub](https://github.com/wassermanproductions).

**Powered by** TensorFold (Ash Hart and the TensorFold contributors) · built with integrated Fish Audio Drama 3 · Built
with Qwen · Built with Fish Audio · MiniMax-Music3.

## New in 2.9.3

- **FLUX3-K installs again on a DGX Spark.** On 2026-10-17 Black Forest Labs renamed the Klein checkpoints on Hugging
  Face (`flux3-image-klein-bf16.safetensors`, `flux3-image-klein-base-bf16.safetensors`) and removed the old
  underscore names, so `oneshot-setup-spark.sh --flux3` could not download them. The setup now fetches the new names
  and renames an older install's files in place instead of fetching 22 GB again: they are the same files (the same
  SHA-256 on Hugging Face). The app reads either name, so a Spark updated from the Sparks tab keeps FLUX3-K before
  the setup runs. Nothing changes on a Mac.

## New in 2.9.2

- **Mac: Music runs on TensorFold's native Metal engine.** On an M5 or later the Music tab now runs MiniMax Music 3
  on `music3-generate` built for Metal (Zig and Metal, no Python or MLX; TensorFold pull request #987, commit
  8c9559da), when `oneshot-setup.sh --music` has built it (`scripts/build-music3-metal.sh`). On the Mac Studio M5
  Ultra, against the MLX port int8, 60 s hip-hop, seed 7, three runs each: the autoregressive model 69 frames/s
  against 65, the whole song 59.5 to 63.0 s against 62.3 to 63.9 s, peak 17.8 GB against 28.1 GB. Through the app a
  song the model ended at 33.4 s took 35.2 s, load included, at 16.1 GB. Limits: it does not stream yet, so *Play
  while it's made* is MLX-only (`MUSIC3_BACKEND=mlx`); an M1 to M4 keeps the MLX port; measured on an M5 Ultra only.
  See [Music](#music-minimax-music-3).

## Imagination Studio 3.0: the studio

The web app is **Imagination Studio by keys+Wasserman powered with TensorFold**, built with Fish Audio Drama 3.
Everything it did before is still here, arranged to be quicker to find and calmer to use.

- **Creative Engines.** Story (the film board, Wasserman Slate, the First AD), Image (stills, scouts, edit a picture),
  Video (clips with sound, long takes, references), Previs (Motion Previs, Ask your agent), Sound (Voice, Music) and
  Edit (the Timeline and its exports). Each opens on a page of picture cards; a card opens the view that does it,
  already set up. In Create a small switch at the top says where the prompt goes: video with sound, an image, or an
  edit of a picture.
- **Studio Vaults.** Library (every take), Looks (the LoRA hub as a library of styles, one card each), Suite (bring work
  in from the filmmaker suite, send the cut to Resolve and dailies to Circle Take), Machine (telemetry, Sparks, brains
  and tools, updates) and Learn.
- **Home**, under the wordmark, starts the common jobs in one click and shows recent takes and the machine.
- **Updates.** Machine → Updates shows the installed version and commit, the branch it follows, the newest GitHub
  release (for information) and one *Update and restart* button. Installing is always a fast-forward of the followed
  branch, and waits until jobs and the render engine are idle.
- **Render bay and ⌘K.** The queue is docked along the bottom of every room. ⌘K (or `/`) searches and runs anything;
  ⌥1 to ⌥9 and ⌥0 jump between rooms. Below 1100 px the sidebar is an icon rail; on a phone the rooms sit along the
  bottom.
- **Pictures.** The cards' key art fills from `app/static/art`: each slot is listed in `art/ART-MANIFEST.json` and
  shows a plain panel until its picture is there. Model and tool marks in `app/static/brand` identify what the Studio
  runs and belong to their owners (`brand/THIRD-PARTY-MARKS.md`).

The typefaces (Geist, Geist Mono, Instrument Serif) ship in `app/static/fonts` under the SIL Open Font License.

## New in 2.9.1

Three changes to Music (MiniMax-Music3), one for each machine and one for the Mac's player. Every figure is one run
unless it says otherwise.

- **Mac: int8 weights by default.** The Studio starts the MLX engine (`music3-mlx`, now pinned at 6844b413) with
  `--quant int8`: the autoregressive model, its code-0 head and the depth decoder at 8 bits. On the Mac Studio M5
  Ultra the autoregressive model went from 40 to 65 frames a second and the peak from 27.6 to 20.5 GB; a 60 s song
  took about 63 s through the app (75.8 s in 2.9.0). Against ComfyUI's reference the code-0 logits keep cosine
  0.99994 (bf16 0.99997), 98.0 % greedy agreement, and Whisper's lyric scores over six same-seed songs stayed within
  their noise. `MUSIC3_QUANT=none` runs the bf16 weights. Limits: measured on an M5 Ultra only; other
  M-series chips are not measured.
- **Mac: play while it's made.** With the box ticked (the default where the engine streams) the song plays in the tab
  while it is made, from `GET /api/music/takes/{id}/live`, one stereo 44.1 kHz WAV. The sound is held back until
  playing will not run dry (the engine's measured no-stall start plus 3 s). Through the app, 60 s songs: first sound
  at about 7 s (7.6 and 6.4 s), playing from about 13 s (13.4 s both), no stall (the least buffer 4.0 s), made in
  63.4 and 61.0 s; a 30 s song played from 8.1 s. The kept take is the engine's finished WAV, the same samples that
  were played. Limits: a Mac only (no route on a Spark streams); only 30 s and 60 s were measured through the app, and
  the wait grows with the length (about 56 s for a 300 s song, from the engine's own figure); nobody has listened for
  seams. Details in [Music](#music-minimax-music-3).
- **Music on a DGX Spark: TensorFold's CUDA engine for MiniMax Music 3 (MiniMax-Music3).** The Music tab on a Spark now
  runs `music3-generate`, TensorFold's native CUDA port (branch `music3-cuda`, 6b714531: Zig + CUDA, no Python at run
  time), wherever it is built and the weights are there; otherwise it falls back to ComfyUI's nodes as in 2.9.0.
  `MUSIC3_BACKEND=cuda|comfy` chooses; the tab's state says which route runs and, after a fall-back, why.
  - **int8 weights by default**, made on the GPU as the engine loads (no cache file): the autoregressive model, its
    head and the depth decoder at 8 bits. `MUSIC3_WEIGHTS=bf16` runs the checkpoint's own weights. It reads
    Comfy-Org's three files as they are (`~/spark-studio-models/MiniMax-Music3-Comfy`, `MUSIC3_MODEL` names another).
  - **Same lifecycle as the Mac engine:** a persistent server, loaded on the first song, holding
    `~/studio-gpu.lock.d` while loaded, unloaded after five idle minutes and before any other job, under the
    memory kill-guard; a studio ComfyUI that holds models is stopped before it loads. Never beside H3, Drama 3 or
    ComfyUI's models.
  - **Installed by** `bash oneshot-setup-spark.sh --music` (and `--upgrade` where the weights are): `spark/build-music3.sh`
    takes the prebuilt binary from the carrier image, or builds it with nvcc 12.9+ and Zig 0.17 (50 s on a GB10).
  - **Measured on one GB10** (int8, hip-hop lyrics, seed 7, 30 steps; one run each; memory is MemTotal - MemAvailable
    above the idle machine):

    | Song | TensorFold CUDA (int8) | ComfyUI's nodes (2.9.0) |
    |---|---|---|
    | 30 s, engine | 60.3 s | 131.0 s |
    | 60 s, engine | **123.9 s** (AR 73.9 s at 20.4 frames/s, DiT 46.4 s, DAV 3.6 s) | 260.6 s |
    | 120 s, engine | 252.0 s | 527.6 s |
    | 60 s through the app | **149.2 s** for the song (load 37.3 s, AR 68.2 s, DiT 40.4 s, DAV 3.3 s; the model ended it at 55.2 s), 171.4 s with Whisper's check (97 %, 164 of 169 words) | 270.6 s for the song (ComfyUI started for it; AR 166.5 s, DiT 94.3 s, DAV 4.0 s; 59.99 s made), 297.0 s with Whisper (96 %, 162 of 169), 29.4 GB at the peak: the same run of the 2.9.1 app with `MUSIC3_BACKEND=comfy` |
    | Peak memory, 60 s | 17.9 GB (18.7 GB through the app) | 29.3 GB |

    bf16 weights: a 60 s song in 194.3 s and 25.5 GB. Parity against ComfyUI's nodes and Whisper's lyric count
    passed on the engine branch (docs/recipes/minimax-music3-cuda.md there).
  - **Honest limits.** No streaming on the Spark: a song is heard when it is done. Built and measured on a GB10
    only (the build refuses another GPU unless `MUSIC3_ANY_GPU=1`; elsewhere use `MUSIC3_BACKEND=comfy`). Loading
    takes about 37 s each time the engine starts (int8 is made at load), so the first song after five idle minutes
    pays it again. Songs over 120 s are not measured on this engine; the memory estimate goes on at the measured slope.
- **Carrier image** `ghcr.io/drowzeys/keys-tensorfold-studio:2.9.1` adds the prebuilt Spark `music3-generate`
  (`spark-music3/`) beside the H3 engine.

## New in 2.9.0

- **Music: MiniMax Music 3 (MiniMax-Music3).** A new **Music** tab beside Voice makes a whole song with sung vocals
  from your lyrics and a style, on this machine: up to five minutes, 44.1 kHz stereo. See
  [Music (MiniMax Music 3)](#music-minimax-music-3).
  - **Lyrics** take section tags (`[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Bridge]`, `[Instrumental]`,
    `[Outro]` ...) on lines of their own; the tab has a button for each and says when a tag looks wrong.
  - **Style** is a caption: genre, tempo, key, mood, vocals, instruments. A helper writes one from those six boxes.
  - **Use as soundtrack:** a song can be laid under a long video, the Editor's export or any clip of the Library. It
    sits under the clip's own sound, is lowered up to 12 dB while someone speaks, and no peak passes -1 dBFS.
  - **Mac:** TensorFold's MLX port (`music3-mlx`, pinned at 6844b413), installed by `bash oneshot-setup.sh --music`
    into its own environment, with the 22 GB of weights. **DGX Spark:** ComfyUI's own MiniMax Music 3 nodes,
    `bash oneshot-setup-spark.sh --music`.
  - **Measured through the app, one 60 s song on each machine** (the same pop lyrics and style, seed 7, 30 steps; one run
    each):

    | 60 s song | Mac Studio M5 Ultra (MLX) | DGX Spark (ComfyUI, CUDA) |
    |---|---|---|
    | Whole job, song made | **75.8 s** (59.99 s of song), files in the page cache | **289.0 s** (59.99 s of song), ComfyUI started and the files read from an empty page cache |
    | Words and melody (autoregressive model) | 37.8 s | 176.3 s |
    | Sound (DiT, 30 steps) | 34.6 s | 96.3 s |
    | Decoder | 2.0 s | 4.1 s |
    | Model load | 0.9 s (first song) | inside the stages above (ComfyUI loads as it goes); about 12 s more than with the files cached |
    | Peak memory | 27.6 GB (MLX's own peak); the machine's in use rose 32.9 GB | 29.3 GB taken from the machine (MemTotal - MemAvailable, 5.1 GB before) |
    | Whisper on the take | 96 % of the words sung (128 of 134); the song reached 43 % of the lyrics at 60 s | 1 %: on this take Whisper heard only "We'll be right back" twice. A second take through the app (hip-hop lyrics, seed 11, 273.2 s): 98 % (166 of 169), all the lyrics reached |

  - **Honest limits** (as of 2.9.0; 2.9.1 plays a song while it is made on a Mac and adds TensorFold's CUDA engine on
    a Spark, above). A song is not played while it is made (no streaming yet). Whisper's count on sung words is
    rough, and one Spark take in three was barely heard by it (above). Lengths measured: on the Spark 30, 60, 120 and
    300 s asked (one run each; the 300 s one ended itself at 189.8 s, so no song over 120 s was made to its full length
    there); on the Mac songs of 10, 30 and 60 s. Other lengths are untested, and so is anything but the default
    settings. On the Spark the autoregressive stage runs in ComfyUI's Python loop at about 9 frames a
    second, so a song takes about 4.4 times its length there against 1.25 on the Mac.
- **Upgrade** with `git pull`, then `bash oneshot-setup.sh --upgrade --music` (on either machine) to add the Music tab's
  engine and weights; restart the app.
- **Licence:** MiniMax-Music3 Community License. Commercial use is allowed, but a commercial product must show
  "MiniMax-Music3" in its interface (the tab does). Above US$20 million of yearly revenue you need MiniMax's written
  authorisation, and a hosted service must keep safeguards against misuse.

## New in 2.8.0 and 2.8.1

- **2.8.1: Qwen 2.1 Full.** The picker now offers Qwen-Image-2.1 in two versions, as it does FLUX3-K: **Qwen 2.1
  Turbo** (8 steps, the quickest) and **Qwen 2.1 Full**.
  - **What Full is:** the 40-step base model with true guidance (CFG 4 with an empty negative prompt, as Qwen's own
    pipeline runs it). It gives more detail than Turbo.
  - **Where it is offered:** wherever the base weights are installed, which is the same files Edit a picture's 40-step
    choice uses (`oneshot-setup.sh --image-edit`).
  - **Mac:** the Mac's generator gained `--cfg` for it.
  - **DGX Spark:** it runs ComfyUI's 40-step CFG-4 workflow.
  - **Times on an M5 Ultra at 1344x768:** Turbo 12 s, Full 47 s, both peaking at 15.3 GiB. Full run without guidance
    took 24 s and was barely better than Turbo, so guidance stays on.
- **A shootout of all four picture models, Mac against DGX Spark**, with 128 pictures, says which to use for what:
  see [Which picture model to use: the evidence](#which-picture-model-to-use-the-evidence).


**[▶ Watch the 60 s demo](https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3/releases/download/v2.8.0/wasserman-studio-mac-demo.mp4)**:
the Studio on one Mac Studio M5 Ultra, recorded live on 2026-10-16. It shows the app at work, two clips with sound,
and the times and memory measured during those renders.

![Two demo clips made on the M5 Ultra: a FLUX3-K Fast picture to 864x480 video, a Qwen 2.1 Turbo picture to 1344x768 video](samples/demo_mac_280.jpg)

- **Pick the picture model.** Wherever the Studio makes a picture you can choose **Qwen 2.1 Turbo** (the quickest),
  **FLUX3-K Fast** (the distilled checkpoint, 16 steps) or **FLUX3-K Full** (the base checkpoint, 50 steps at CFG 3.5).
  That covers *Image only*, scouts, the first frame of a clip and a last frame. Install them with
  `bash oneshot-setup.sh --flux3` (41 GB); first accept the FLUX Kommunity License on Hugging Face.
  - **How each prompt is handled:** BFL's prompt upsampler writes a full scene caption from your prompt first.
  - **On a DGX Spark:** FLUX3-K runs on ComfyUI's own FLUX 3 nodes, with ComfyUI moving to `5a665890`.
  - **On a Mac:** it runs on flux3-mlx in an environment of its own.
- **Edit a picture with FLUX3-K.** It also takes up to nine further reference pictures, for example a person from one
  picture put into the place of another. The LoRAs, the camera ring and the 40-step base model stay Qwen's.
- **Better text and whole figures.** On the same prompts and seeds, FLUX3-K:
  - spelled a poster's title and tagline where Qwen wrote "SOME ONE IS STOLL LISTENNING";
  - drew whole figures for character sheets where Qwen cut them at the waist;
  - kept two characters recognisable in a two-reference shot.
- **New carrier image.** `ghcr.io/drowzeys/keys-tensorfold-studio:2.8.0` now holds the DGX Spark's LoRA engine
  (`h3-cuda-lora` `1f7ecc75`), so a Spark install no longer builds it from source.
- **Upgrade** with `git pull`, then `bash oneshot-setup.sh --upgrade`. Add `--flux3` for the FLUX3-K weights.

![FLUX3-K against Qwen 2.1 Turbo on the same poster prompt and two seeds](samples/flux3k_vs_qwen_poster.jpg)

*The poster prompt, two seeds. Left: Qwen 2.1 Turbo, on the Mac. Middle and right: FLUX3-K Full ("Base") and FLUX3-K
Fast ("Distilled"), here run through diffusers on a Spark, so the times are diffusers' (91 s and 15.5 s). In the
Studio, through ComfyUI, they take 57.5 s and 10.5 s.*

**Picture times**, 1344x768, model loaded, bf16:

| | DGX Spark | Mac Studio M5 Ultra |
|---|---|---|
| Qwen 2.1 Turbo, 8 steps | 9.5 s | 9.7 s |
| Qwen 2.1 Full, 40 steps at CFG 4 (2.8.1) | | 47 s (whole run, model load included) |
| FLUX3-K Fast, 16 steps | 10.3 to 10.7 s | 14.9 s |
| FLUX3-K Full, 50 steps | 57.5 s | 53.6 s |
| FLUX3-K prompt upsampler, once per prompt | about 18 s | 5 s |
| Edit a picture: Qwen / FLUX3-K Fast / FLUX3-K Full | 24 / 37 / 139 s | 10 / 37 / 240 s |
| Two-picture edit: Qwen / Fast / Full | 23 / 44 / 188 s | 13 / 42 / 427 s |

FLUX3-K Full is slow for edits on a Mac, so Fast is the FLUX3-K to use there.

**A whole clip, text to picture to 5 s of video with sound**, measured through the app. On the Mac these are the demo
renders; on the Spark, the 2.8.0 tests.

| | Picture | Video (FastH3, 8 passes) | Total | Peak memory of the render |
|---|---|---|---|---|
| Mac, 864x480, FLUX3-K Fast | 11 s | 70 s (5.93 s a pass) | **81 s** | 29.0 GB |
| Mac, 1344x768, Qwen 2.1 Turbo | 11 s | 201 s (20.1 s a pass) | **212 s** | 33.0 GB |
| DGX Spark, 864x480, FLUX3-K Fast | about 32 s (upsampler included) | 70 to 96 s | **102 to 128 s** | |
| DGX Spark, 864x480, FLUX3-K Full | 53 s | 96 s | **149 s** | |

Peak memory is the render process's macOS footprint, GPU memory included, sampled every second. The peak comes at the
handover from the picture model. FastH3 itself holds 23 GB at 864x480 and 27 GB at 1344x768.

![Memory of the render process during the two demo renders on the M5 Ultra](samples/demo_mac_280_memory.png)

**The Studio's TensorFold work, submitted upstream.** At the TensorFold project's invitation, the engines behind
this Studio are open as pull requests to [ashhart/TensorFold](https://github.com/ashhart/TensorFold/pulls?q=author%3Adrowzeys).
Each lists what was tested and what was not:

| Pull request | What |
|---|---|
| #949 #950 #959 #960 #961 | MiniMax H3 / FastH3 on Metal in bf16 (10.2 s a pass against 27.9 s for MLX bf16), from an image, any step count, re-voicing, Ref2VA |
| #947 #958 #946 | MiniMax H3 / FastH3 on CUDA, one GPU or one pass split over several DGX Sparks (3 Sparks: 3.0 s a pass in int8, 5.0 s in bf16), and the runtime hook it uses |
| #939 #956 | Fish Audio Drama 3 on CUDA (34 frames/s) and on Metal (84 frames/s, first audio 0.15 s, 9.5 GB) |
| #948 | Qwen-Image 2.1 on Metal (0.74 s a step, 43 dB PSNR against mflux) |
| #941 to #944 | This Studio: Slate, Motion Previs, FLUX3-K and Voice |

These are proposals: this repository's install still pins the engines on the `drowzeys/TensorFold` fork.

## Big credit: Sam Wasserman and Wasserman Productions (wassermanproductions.com)

**Sam Wasserman shaped the production side of this studio: the AI film workflow and the ideas behind it.** Slate, his
prompt studio for AI filmmaking, is the workflow inside the Wasserman Slate AI Studio tab. The Wasserman Sharp Spec,
the QA gate that follows it, and the 80s Horror LoRA that opens the Keys and Wasserman LoRA Hub are his too. The Motion Previs tab's analysis is a
Python port of his [Motion Previs Studio](https://github.com/wassermanproductions/motion-previs-studio). The aim
we share is the best AI film studio there is, and this pack is where his planning tools meet a render engine. See
[CREDITS.md](CREDITS.md).

**Wasserman's Filmmaker Suite, by Sam Wasserman and Wasserman Productions**, is built in: ScriptBreak, Cork Board,
Master Canvas, Storyboard Reference Studio, Blockout, Motion Previs Studio, Slate, Circle Take, Stem Studio, Call Sheet
and DaVinci MCP, each under its own licence (Apache-2.0; Master Canvas MIT). The Studio reads and writes their files.

**Keys (drowzeys)** built the studio itself: the app, both builds, the TensorFold families and int8 kernels, and the
Metal and CUDA engines it renders with. **Sam Wasserman and Wasserman Productions** helped design and build it in
collaboration with Keys. Imagination Studio is by keys+Wasserman.

**Thank you to everyone this stands on:** the Qwen team at Alibaba (Qwen-Image-2.1, Qwen3-VL), the MiniMax team
(MiniMax H3), Ash Hart and the TensorFold contributors, antirez (h3.c), RobZombAI (H3MLX), mrbizarro (minimax-h3-mlx /
Phosphene), Filip Strand and the mflux contributors, Viggle (the image turbo adapter up to 2.2), speach1sdef178 (the 2x video
decoder), LightX2V (the video Turbo adapter), NVIDIA Research (Sol-Engine, Sol-Attn, Sol-H3), FastVideo (FastH3), and Apple's MLX team and every MLX
contributor, Fish Audio (Drama 3, text to speech), the MiniMax team again (MiniMax-Music3, the Music tab's songs), Black Forest Labs (FLUX 3 Image Klein) and felix-ab (flux3-mlx), and OpenMOSS (MOSS-Image). This pack is their work, ported, pinned and measured. See
[CREDITS.md](CREDITS.md). Built with Qwen. **Built with Fish Audio.**

## Which picture model to use: the evidence

**The test:** a shootout on 2026-10-16 with Studio 2.8.1, run on a Mac Studio M5 Ultra and one DGX Spark.
- **Pictures:** 8 prompts × 2 seeds × 4 models × 2 machines = **128 pictures at 1344x768, 0 failed jobs**.
- **Judging:** by eye, from the full-size pictures.
- **Full report:** every grid, the raw numbers and the method are in
  **[docs/picture-shootout.md](docs/picture-shootout.md)**.

**Speed and memory**, per picture with the model loaded (median):

| Model | Mac M5 Ultra | DGX Spark | Peak memory (Mac / Spark) |
|---|---|---|---|
| Qwen 2.1 Turbo, 8 steps | **4.7 s** | 8.6 s | 33 / 31 GB |
| Qwen 2.1 Full, 40 steps at CFG 4 | 43.3 s | 80.1 s | 33 / 30 GB |
| FLUX3-K Fast, 16 steps | 9.8 s | 10.5 s | 38 / 37 GB |
| FLUX3-K Full, 50 steps | 59.9 s | 58.7 s | 40 / 37 GB |
| FLUX3-K prompt upsampler, once per prompt | 5.2 s | 12.5 s | |

- **Qwen:** runs about 2x faster a step on the M5 Ultra.
- **FLUX3-K:** runs at the same speed on both machines.

**Quality, the winner per prompt:**

| Prompt | Winner | The evidence |
|---|---|---|
| Poster with title and tagline | Qwen 2.1 Full | Most coherent scene. All 32 posters, every model, spelled both lines exactly |
| Photoreal portrait | Qwen 2.1 Full | Real skin and the whole face. FLUX3-K cropped off the mouth in 8 of 8 |
| Character turnaround | Qwen 2.1 Full, narrowly | Gear consistent across views. FLUX3-K wins for a labelled line-art sheet |
| Crowded night market | FLUX3-K Full | Lanterns, steam, mostly real words on signs. Qwen drew pseudo-script |
| "Red cube on a blue sphere, left of a green pyramid" | FLUX3-K Fast | Right in **8 of 8**; Qwen 2 of 8 |
| Food photography | FLUX3-K Fast | More professional styling. No model reliably drew the three pancakes asked for |
| Watercolor | FLUX3-K Full | Truest washes and paper texture |
| Hands at a piano | FLUX3-K Full | Five fingers on each hand in 8 of 8. Qwen drew a third hand 3 times |

![The spatial prompt: rows are Mac s101, Mac s102, Spark s101, Spark s102; columns Qwen Turbo, Qwen Full, FLUX3-K Fast, FLUX3-K Full](docs/shootout/5-spatial.jpg)

*The spatial prompt. Every FLUX3-K picture (right two columns) has the pyramid on the right, as asked. Six of the
eight Qwen pictures put it on the left.*

**Conclusion: which to pick**
- **Drafts and scouts:** use **Qwen 2.1 Turbo**. It is the quickest, and its quality is visibly below the other three.
- **The everyday default:** use **FLUX3-K Fast**. It is right on layout and good on hands and crowded scenes, at about
  10 s on either machine.
- **Faces, portraits, posters and character sheets:** use **Qwen 2.1 Full**. It is twice as fast on a Mac.
- **Final frames of complex scenes, illustration and hands:** use **FLUX3-K Full**.
- **Machine:** for Qwen, the Mac when you have the choice. For FLUX3-K, the machine does not matter.

**Limits:** two seeds a prompt and one judge, by eye. These model times leave out the text encode, so they are lower
than the whole-run times in the table above.

### MOSS-Image on a DGX Spark

**MOSS-Image** is OpenMOSS's 5B text-to-image model (Apache 2.0: commercial use is allowed). On a DGX Spark it is a
fifth choice in the picture picker, for scouts, studio jobs and Nodes. It makes pictures from words only: it does not
edit pictures.
- **Install:** `bash oneshot-setup-spark.sh --moss` (30 GB: the SFT checkpoint and its Qwen3.5 9B text encoder in bf16,
  and the FLUX.2 small decoder, from Comfy-Org/MOSS-Image). It needs ComfyUI v0.42.0 or later, which this setup moves to.
- **Settings:** ComfyUI's own template: 25 steps of er_sde at CFG 2.5, with its negative prompt. The prompt goes to the
  model as written.
- **Mac:** not available yet; there is no MLX build.

Tested on one DGX Spark on 2026-10-19 (bf16, one seed per prompt, judged by eye):

| | MOSS-Image | FLUX3-K Full | Qwen 2.1 Turbo |
|---|---|---|---|
| 1344x768, model loaded | 45 s | 69 s | 10 s |
| 1024x576 (a scout picture) | 26 s | | |
| Memory taken, from nothing loaded | 26 GB | | |
| Poster: title, tagline and date | All three lines exact, cleanest layout | Title cut off at the top | Exact |

- **Best at:** lettering. Posters, title cards, signs, menus with prices and labels came out exactly as written. A
  Japanese station sign was right too.
- **Also good:** a photoreal portrait at dawn (warm, filmic, the light as asked) and anime key art.
- **Not tested yet:** hands, the same character across pictures, and MOSS-Image's JSON layout prompts.
- **Use it for:** posters, title cards, signage and anything with words in the frame, and pictures meant for
  commercial use.

![MOSS-Image, FLUX3-K Full and Qwen 2.1 Turbo on the same poster prompt](docs/shootout/moss-poster.jpg)

![The same film-still prompt: MOSS-Image at 25 and 50 steps, FLUX3-K Full, Qwen 2.1 Turbo](docs/shootout/moss-still.jpg)

## Speed: the first version on each machine, and 2.3

One run each unless a range is given; 5 second clips are 124 frames. Mac: Mac Studio M5 Ultra, 256 GB. Spark: one
NVIDIA DGX Spark (GB10), models already loaded.

| 5 second clip, 864x480 | Mac, first version (1.0, 2026-10-04) | Mac, 2.3 | DGX Spark, first build (ComfyUI's own blocks, bf16) | DGX Spark, 2.3 |
|---|---|---|---|---|
| What makes the video | MiniMax H3 + Turbo adapter on MLX, 3 passes | FastH3, 8 passes, TensorFold's Zig + Metal engine | FastH3, 8 passes, ComfyUI | FastH3, 8 passes, TensorFold's CUDA engine in int8 |
| Image, then video from it | 74 s (103 s with the sound made again, the later default) | **79 s** (image 6 s, video 73 s) | 155 s | **115 s** (measured before the faster decoder below, which takes about 10 s more off) |
| Text straight to video | not offered | **66 s** | 118 to 121 s | **73 s** |
| One denoising pass | 9.5 s | **5.3 s** | 10.5 s | **7.1 s** |
| Video decode | not timed apart | 13 s | 26 s | **14 s** |

| Other sizes and lengths | Mac, first version | Mac, 2.3 | DGX Spark, first build | DGX Spark, 2.3 |
|---|---|---|---|---|
| 864x480, 4 passes, text to video | | 43 s | 71 s | 45 s (from the pass time) |
| 1280x720, 5 s, text to video | | 199 to 217 s | not measured | **183 s** |
| 1344x768, 5 s, text to video | | 175 s | 289 s | **about 195 s** |
| 1344x768, 8 s, image then video | 364 s (709 s with the later default) | not re-measured | | |
| 1280x720, 10 s (243 frames), text to video | | 378 s | not measured | 444 s |
| One 15 s part of a chained 864x480 clip | | about 249 s | not measured | about 304 s |
| One image, 1344x768 | 9.2 s (Viggle adapter, 6 steps) | **8.9 s** (Qwen-Image-2.1-Turbo, 8 steps; peak 15 GiB against 28) | 18 to 21 s with the model load | **9.5 s** |

- The Mac's first version and 2.3 use different video models, so the 74 s and 79 s rows are not the same work: the
  first is 3 passes of MiniMax H3 with its Turbo adapter, whose sound was judged poor by ear; 2.3 is 8 passes of
  FastH3 with its own sound. Per pass the Mac went from 9.5 s to 5.3 s.
- On the Spark the model is the same in both columns. The gain is the engine (10.5 s a pass to 7.1 s) and the 8-bit
  video decoder (26 s to 14 s).
- Both engines are TensorFold H3 families on the TensorFold 1.0.4 base, from one commit of one branch. 1.0.3 and 1.0.4
  are language-model releases, so the move changed nothing here: on each machine the same seed gives a bit-identical
  clip to the 1.0.2-based engine, at the same speed.

## Which Mac runs what

Every row is Studio **2.3**. "Default" means images (Qwen-Image-2.1-Turbo) and FastH3 video with sound, the web app and
the one-click app. Only the M5 Ultra with 256 GB has actually run it; every other row follows from memory measured
on that machine and is marked untested.

| Chip | Memory | What runs | Install command | Video engine it uses | Peak memory | Status |
|---|---|---|---|---|---|---|
| M5 family | 64 GB | Default | `bash oneshot-setup.sh --app` | FastH3, native Zig engine | 32 GiB | Untested |
| M5 family | 96 GB | Default | `bash oneshot-setup.sh --app` | FastH3, native Zig engine | 32 GiB | Untested |
| M5 family | 128 GB | Default, and MiniMax H3 Turbo if wanted | `bash oneshot-setup.sh --turbo --app` | FastH3 native; Turbo on the MLX engine with int8 kernels | 32 GiB; 103 GiB for Turbo | Untested |
| M5 family | 256 GB or more | Everything | `bash oneshot-setup.sh --turbo --app` | The same | The same | **Tested** (M5 Ultra, 256 GB) |
| M1 to M4 | 64 GB | Default, slowly | `bash oneshot-setup.sh --app` (add `Q8_ONLY=1` in front to save 65 GB of disk) | FastH3, MLX engine, 8-bit weights | 37 GiB at 480p, 40 GiB at 720p | Untested; emulated on the M5 |
| M1 to M4 | 96 GB | Default, slowly | `bash oneshot-setup.sh --app` | FastH3, MLX engine, 8-bit weights | 37 to 40 GiB | Untested; emulated on the M5 |
| M1 to M4 | 128 GB | Default, slowly. Turbo installs but is not recommended | `bash oneshot-setup.sh --app` | FastH3, MLX engine, 8-bit weights | 37 to 40 GiB | Untested; Turbo's memory without the int8 kernels is not measured |
| M1 to M4 | 256 GB or more | Default, slowly; Turbo should fit | `bash oneshot-setup.sh --turbo --app` | FastH3 MLX 8-bit; Turbo on the MLX engine in bfloat16 | 37 to 40 GiB; Turbo not measured | Untested |
| Any | 48 GB | Images only | `bash oneshot-setup.sh --image-only` | none | 28 GiB | Untested below 256 GB |

How to configure it:

- **Nothing to set on a normal install.** The setup reads the chip and the memory and picks the engine: the native
  engine on an M5, the 8-bit weights on an M1 to M4 (it converts them once, about 37 GB). It stops with a message if
  the Mac has too little memory for what was asked.
- **Disk:** 175 GB for the default on an M5; 212 GB on an M1 to M4 (147 GB with `Q8_ONLY=1`); 64 GB more with
  `--turbo`; 33 GB for `--image-only`.
- **Speed on an M1 to M4:** the 8-bit path took 31 s a pass at 864x480 on the M5 Ultra's GPU, against 5.3 s for
  the native engine there. An older GPU is slower again. Start with 480p, 5 seconds and 4 passes
  (`STEPS=4 bash scripts/fast.sh "a prompt" out.mp4`, or the 4 passes card in the app).
- **Overrides:** `FASTH3_Q8=0` runs an M1 to M4 with the uncompressed weights (64 GiB peak: a 96 GB Mac or larger;
  no faster). `FASTH3_ENGINE=mlx` forces the MLX engine on an M5. `ENGINE=h3` asks for Turbo where it is installed.

| Studio version | Smallest Mac for video |
|---|---|
| 2.0 and earlier | 128 GB (M5 for the fast path) |
| 2.1 | 64 GB on an M5; 96 GB on an M1 to M4 |
| 2.2 to 2.5 | 64 GB on any Apple silicon |

On a **DGX Spark** the setup asks for 100 GB of memory for video (a Spark has 128 GB; about 70 GB was in use during a
clip). No NVIDIA machine with 64 GB has been tried.

Measured memory for each kind of job is under [Minimum requirements](#minimum-requirements).

## DGX Spark (CUDA) build

The same Studio (web app, scripts, one-shot install) also runs on an **NVIDIA DGX Spark** (GB10, 128 GB unified
memory, CUDA 13). FastH3's transformer runs there on **TensorFold's H3 family for CUDA**: the Mac engine's design
(int8 projections, tile-routed int8 attention with FastH3's own sparse selection) on the GB10's tensor cores, written
in Zig over the CUDA driver. **ComfyUI**, pinned, hosts the rest: the text encoder, the sampler, the decoders and
Qwen-Image-2.1. The header of the web app says which build you are in: a green `DGX SPARK · NVIDIA GB10 · H3-CUDA`
badge, or a purple `MAC · APPLE SILICON MX · H3-METAL`.

**Thank you** to Ash Hart for TensorFold, FastVideo (Hao AI Lab) for FastH3 and its ComfyUI release, MiniMax for H3,
the Qwen team for Qwen-Image-2.1-Turbo, and the ComfyUI team for the runtime this build stands on.

```bash
git clone https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3.git && cd keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3
sudo apt install -y git ffmpeg
bash oneshot-setup.sh            # the same command as on a Mac: on Linux it runs oneshot-setup-spark.sh
                                 # 99 GB of models, the engine build, a test image and a 5 second test clip
bash scripts/app.sh              # http://127.0.0.1:7870
```

ffmpeg is part of the install, and the installer stops without it. A Spark that was set up some other way (a copy of
another Spark's folders, say) may lack it: the app then starts, says so in its log and in the page's header, and
refuses the jobs that need it when they are queued. The 720p preset is not one of them any more: it generates
1280x736 and crops to 1280x720, and the crop is now made inside the ComfyUI workflow, before the clip is encoded
(one encode instead of two); in 2.6.5 it was an ffmpeg pass after the render, and a Spark without ffmpeg lost the
finished clip there. Without ffmpeg a clip also keeps ComfyUI's own sound track instead of the limited 256 kb/s one,
and the render says so.

#### Upgrade from 2.5 or 2.6 (DGX Spark)

```bash
cd keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3 && git pull
bash oneshot-setup.sh --upgrade  # on Linux: oneshot-setup-spark.sh --upgrade
```

`--upgrade` moves an existing install to the version of the clone: the environment's packages, ComfyUI at its pinned
commit, the engine (rebuilt only when its pinned commit changed) and the Motion Previs environment, which it adds
when it is missing. It **fetches no weights** (a model that is not on disk is named and left out; an image-only
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
- ComfyUI keeps running with the models loaded after a render (`bash spark/comfy.sh stop` frees the memory). It runs
  at low priority with a memory guard beside it, because a Spark that runs out can become unreachable: see
  *The memory guard* below.

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
[docs/multi-spark.md](docs/multi-spark.md).

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

## What changed

> **2.9.3 (2026-10-17): FLUX3-K's new file names.** BFL renamed the Klein checkpoints; the Spark setup fetches the
> new names and renames old ones. Details: [New in 2.9.3](#new-in-293).

> **2.9.2 (2026-10-17): Music on TensorFold's native Metal engine on an M5 Mac.** No live playback there yet; an M1 to
> M4, or `MUSIC3_BACKEND=mlx`, keeps the MLX port and its live playback. Details: [New in 2.9.2](#new-in-292).

> **2.9.1 (2026-10-16): Music on both machines.** int8 weights and play while it's made on a Mac; TensorFold's CUDA
> engine for MiniMax-Music3 on a DGX Spark. Details: [New in 2.9.1](#new-in-291).

> **2.9.0 (2026-10-16): Music, MiniMax Music 3.** A Music tab: a song from lyrics and a style, laid under a video if
> you like. Details: [New in 2.9.0](#new-in-290).

> **2.8.1 (2026-10-16): Qwen 2.1 Full.** The picture-model choice adds Qwen-Image-2.1's 40-step base model as
> **Qwen 2.1 Full**, beside **Qwen 2.1 Turbo**, as FLUX3-K has Fast and Full.
> - **Guidance:** it runs with true classifier-free guidance at 4 and an empty negative prompt, as Qwen's own pipeline
>   does: ComfyUI's CFG-4 workflow on a DGX Spark, and `qwen_image_generate.py --cfg 4` on a Mac. The Mac's
>   generator had no guidance before.
> - **Where it is offered:** where the base weights are installed (`--image-edit`).
> - **Measured on an M5 Ultra at 1344x768:** Turbo 12 s, Full 47 s, both peaking at 15.3 GiB. Full without guidance
>   took 24 s and looked barely better than Turbo.
> - **Docs:** the README opens with what is new, the 2.8.0 demo and a new banner.

> **2.8.0 (2026-10-15): pick the picture model: Qwen 2.1 Turbo, FLUX3-K Fast or FLUX3-K Full.** Black Forest Labs'
> FLUX 3 Image Klein ("FLUX3-K") joins Qwen-Image-2.1-Turbo wherever the Studio makes a picture from a prompt: *Image
> only*, the scouts a clip starts from, and a last frame. **FLUX3-K Fast** is the distilled checkpoint (16 steps),
> **FLUX3-K Full** the base one (50 steps at CFG 3.5); both get a full scene caption from BFL's prompt upsampler first.
> On a DGX Spark they run on ComfyUI's own FLUX 3 nodes (ComfyUI moves to `5a665890`, v0.41.0 and later), upsampler
> included, so the render stays under the memory guard; on a Mac on flux3-mlx (Apache-2.0, a port of BFL's reference
> code) in an environment of its own. `oneshot-setup.sh --flux3` installs them (41 GB; the Hugging Face repos are gated:
> accept the FLUX Kommunity License first). Measured at 1344x768 with the model loaded, bf16: Qwen Turbo 9.7 s on an
> M5 Ultra; FLUX3-K Fast 10.3 to 10.7 s on a Spark (ComfyUI; diffusers took 15.5 s for the same picture) and 14.9 s on
> an M5 Ultra; FLUX3-K Full 57.5 s on a Spark and 53.6 s on an M5 Ultra; the upsampler, once per prompt, about 18 s on
> a Spark and 5 s on an M5 Ultra. Against Qwen on the same six prompts and two seeds, FLUX3-K spelled a poster's title and tagline
> right where Qwen wrote "SOME ONE IS STOLL LISTENNING", drew whole figures for character sheets where Qwen cut them at
> the waist, and kept two characters recognisable in a two-reference shot; Fast looked as good as Full. The same seed
> draws a different picture on the two machines (the runtimes draw their noise differently). **Edit a picture** offers
> the same choice: FLUX3-K takes the picture, and up to nine more beside it, as reference latents (BFL's edit layout:
> every picture on the prompt and on the negative, the result at the first picture's size), and the upsampler sees the
> pictures before it writes the caption of the result (its i2i system prompt). Image LoRAs, the camera ring and the
> 40-step base model stay Qwen's. Measured with the job's own scripts, seed 7, 1344x768, upsampler included: an edit
> took 24 s with Qwen, 37 s with FLUX3-K Fast and 139 s with Full on a DGX Spark, 10, 37 and 240 s on an M5 Ultra; two
> pictures (a person from one put in the place of another) 23, 44 and 188 s on a Spark, 13, 42 and 427 s on the Mac.
> The reference rows lengthen every step, so Full is slow for edits on a Mac (about four times its text-to-image
> time): Fast is the FLUX3-K for edits. FLUX3-K follows "switch the neon off" where Qwen left the signs lit on the
> Spark. FLUX 3 Image Klein is under the FLUX Kommunity License: non-commercial use, and commercial use of the
> outputs only for Qualifying Users (under US$5M revenue). The carrier image moves to
> `ghcr.io/drowzeys/keys-tensorfold-studio:2.8.0`: the same Mac payload, and the DGX Spark's prebuilt engine is now the
> LoRA build of 2.7.1 (`h3-cuda-lora` `1f7ecc75`), so a Spark install takes it instead of building it from source.

> **2.7.8 (2026-10-15): no dropped lines on the Spark, a cloned voice at once on the Mac.** On a DGX Spark the app
> now gives every take the Mac engine's end-of-text check (`app/wordcheck.py`, its rules copied from TensorFold's
> `drama3-mlx`): Whisper hears the take, and when words are missing (under 85 % heard, three in a row, or the ending)
> the take is made again with another seed, not played, twice at most, the best kept and the first one's check kept
> beside it. The two-speaker example on seeds 1 to 8 through the head app: before, seed 2 lost its last line (15 of
> 20 words) and the other seven were whole; after, all eight whole, seed 2 drawn again once (42.7 s for the take
> against 18 to 25 s for the others). On the Mac a voice to clone left without its words is heard by the engine's own
> Whisper on the GPU (TensorFold `drama3-mlx` 8f3d4b33), not by the app's on the CPU first: the first sound of a
> cloned take reaches the page **0.56 to 0.63 s** after the request, against 10.6 to 10.8 s in 2.7.7.
> **Built with Fish Audio.**

> **2.7.7 (2026-10-15): Voice answers at once on the Mac, and dialogue keeps its lines.** The Mac's Drama 3 now runs
> as a persistent server, like the Spark's (TensorFold `drama3-mlx` 92c09a8e, `--serve`): the first take loads it
> (5.2 s, about 50 GB with its word check), later takes start at once, and it is unloaded after five idle minutes and
> before any video or picture job, holding the studio's GPU lock while loaded. **Once loaded, a take's first sound
> reaches the page 0.23 to 0.30 s after it is asked for** (5.4 s on the first take, 2.7.6's every take). A two-speaker
> line is now sent a turn a line on both machines (on one line Drama 3 dropped later turns; Fish's own prompt format),
> and the tab's example is written so: on the M5 Ultra the two-speaker example came back whole on seeds 1 to 4, three
> of them while playing and one made again without playing. The Mac engine also redraws a part whose last word is
> missing. Measured through the Mac app: a 46 s read in 16.8 s, a Slate cast line in 1.5 s, every take's words all
> heard. **Built with Fish Audio.**

> **2.7.6 (2026-10-15): the Mac's Voice tab catches up with the Spark's.** On a Mac a take now **plays while it is
> made** (the engine's `--stream`, through the same `/api/voice/takes/{id}/live` as on a Spark) and the engine
> **checks its own words** as it goes (TensorFold `drama3-mlx` 4a8a982f: Whisper on MLX after each part, a runaway tail
> cut, a part with missing words drawn again, and a stop rule on the model's own end signal). The check's word count
> is the take's score, so the take is no longer transcribed a second time, and the tab shows it part by part, with
> the time to the first sound. A part cannot be drawn again once it has been heard: a take that comes out short while
> playing is made once more without playing (*Play while it is made* off does that from the start). Measured on the
> M5 Ultra through the app: 8 s of speech in 8.7 s from the click, the first sound 5.3 s after it (start-up and the
> 3.7 s load, then 0.22 s), a 46 s read in 21.9 s. **Both engines are now public** on
> [drowzeys/TensorFold](https://github.com/drowzeys/TensorFold) (`drama3-mlx` for the Mac, `drama3-cuda` for the
> Spark), and `oneshot-setup.sh --voice` installs the Mac one (`scripts/setup-voice.sh`); on a Spark it checks the
> hand-built engine and prints the steps. **Built with Fish Audio.**

> **2.7.5 (2026-10-15): Voice on the DGX Spark.** The Voice tab now works on a DGX Spark as on the Mac, through
> TensorFold's native CUDA port of Fish Audio Drama 3 (`drama3-generate`, Zig + CUDA, no Python at run time; nothing is
> sent to Fish Audio). The engine is a persistent server: the first take loads it (about 18 s, 41.6 GB at the peak),
> later takes start at once, and it is unloaded, its memory given back, after five idle minutes and at once before any
> video or picture job of the queue starts, so it never stands beside MiniMax H3. It holds the studio's GPU lock for as
> long as it is loaded and stands under its own memory guard. A take **plays while it is made** (the first sound
> about 0.3 s after the take starts on a loaded engine). Long text goes in parts and keeps one voice. Measured on a
> GB10 through the app: 28 to 31 frames a second (a take made in 0.7 to 0.8 times its length), Whisper 92 to 100 %
> on a plain line, a cloned voice, two speakers, a Slate cast voice and a 56 s read. See
> [Voice on a DGX Spark](#voice-on-a-dgx-spark-cuda). **Built with Fish Audio.**

> **2.7.4 (2026-10-15): Voice, with Fish Audio Drama 3.** A new **Voice** tab speaks text with Fish Audio Drama 3 on
> the Mac itself, through TensorFold's MLX port (nothing is sent to Fish Audio): up to 16 speakers in one take
> (`<|speaker:0|>` ... `<|speaker:15|>`), `[directions]` for the delivery (`[whispering]`, `[sighs]`, or a description of
> the voice), and a voice cloned from a clip, with a consent box that must be ticked. Each take runs in the render
> queue, one at a time and never beside a video (Drama 3 peaks at about 50 GB), under the studio's GPU lock; Whisper
> then checks its words. *Use as Ref2VA voice reference* puts a take into the create wizard as reference audio
> (`<Audio N>`; a take over 15 s is cut to its first 15). In Slate's **Sound** tab a voice sheet has **Cast voice**: its
> sample line spoken in the voice the sheet describes, kept on the sheet, and sent as that speaker's reference audio in
> shots that open on cast stills. On a DGX Spark the tab is there and says the CUDA port is not available yet. Measured
> on the M5 Ultra: 63 frames a second (about 2.9 times real time), 3 to 4.5 s of engine time for a 7 to 11 s line, 16 to
> 18 s a take through the app with the word check. See [Voice (Fish Audio Drama 3)](#voice-fish-audio-drama-3).
> **Built with Fish Audio.**

> **2.7.3 (2026-10-15): the new name.** The studio is now *Wasserman Slate AI Studio, powered by TensorFold, built
> with integrated Fish Audio Drama 3*, and the repository moved to
> `drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3` (the old
> address redirects). Fish Audio Drama 3, a 35B text-to-speech model with up to 16 voices per scene, voice cloning and
> direction tags, is being ported to run locally on TensorFold: natively on CUDA for the DGX Spark and on MLX for the
> Mac. **The voice step is not in this release yet**; it arrives when both ports pass their parity checks against
> Fish's reference. No Fish Audio cloud API is used, and the pack ships no Drama 3 weights.

> **2.7.2 (2026-10-14): typing a custom size.** The Width and Height boxes of a custom resolution, and the box of
> a custom number of passes, took one digit at a time: every key redrew the form and the box under the cursor with it.
> The value still changes with every key; the form is redrawn once the box is finished (Enter, Tab or a click elsewhere).
> The seed was checked at the same time, on both machines: two seeds give two different clips on a Mac (Zig engine), on a
> DGX Spark (TensorFold int8 engine) and on a Spark with a LoRA merged into that engine. The Create tab, each Production
> segment and each Slate shot keep a seed of their own, and the Sharp Spec sets every segment or shot to 42.

> **2.7.1 (2026-10-14): a LoRA on a DGX Spark renders on TensorFold's int8 engine again.**
> - **LoRA clips are merged into the engine** (`TensorFoldH3Blocks`): the node takes the LoRA's patches for the block
>   projections off ComfyUI, merges them into the bfloat16 weights as ComfyUI does, and the engine quantizes them
>   (`tf_h3_set_weight`, engine `1f7ecc75`). The memory and the speed are the engine's own: 1376x768 at 124 frames
>   took 62.3 GB against 62.2 without a LoRA, and 864x480 at 124 frames 114 s against 140 s on ComfyUI's blocks.
> - **The memory guard takes LoRA clips up to 1376x768 at 243 frames**, measured from below on a worker Spark (a route
>   of its own, `int8-lora`); the Sharp Spec size and 1344x768 take a LoRA again. A Spark whose engine is older
>   renders a LoRA on ComfyUI's bfloat16 blocks with the old limit (1280x736, 124 frames), and the app says which.
> - Same seed, the engine's LoRA clip and ComfyUI's bfloat16 one show the same shot (*LoRAs* in the hub section).
>   Reference clips (Ref2VA), MiniMax H3's own transformer (`--base h3`) and the Turbo path keep ComfyUI's blocks.

> **2.7.0 (2026-10-13): the Sparks setting, one to eight DGX Sparks for a job.**
> - **A Sparks tab**: add a worker DGX Spark by the address you would give `ssh`, check it, update its Studio code and
>   start its app, stop it. Each row shows its version, memory guard, memory and disk, and what it renders.
> - **A Sparks chooser, 1 to 8**, at the top of the Queue, in Create, on the Production board and on a Slate shot
>   (`sparks: N` in the API). The app uses as many as are up and able to do the job and says so ("5 of 8 available").
> - **Several clips go one per Spark, in parallel**, and land in the head's Library and project as if rendered there,
>   with seed, settings and Spark on each take. One clip stays on one Spark; a long video stays on the head (each
>   piece opens on the last frame of the one before); a clip with a LoRA or reference pictures goes only to a worker
>   that has them and whose memory guard accepts it.
> - **Measured**, the same eight clips (56 frames, 864x480, FastH3 8 passes) through the app: 263.2 s on one Spark,
>   129.8 s on two (2.03x), 97.5 s on three (2.70x), 69.0 s on four (3.81x), 65.0 s on five (4.05x). Five is little
>   better than four for eight clips: they go in two rounds either way.
> - **How it connects**: each worker's app listens on its own 127.0.0.1 and the head opens one ssh tunnel to it. A
>   worker silent for 60 s during a job is marked down and its clip rendered elsewhere.
> - **Not built**: one clip split across several Sparks (measured at 72.2 s on one against 40.9 s on four, but the
>   installer does not build that engine and the app does not run its ranks; see `docs/multi-spark.md`). **Not
>   tested**: six to eight Sparks (five exist here), LoRA and reference clips on real workers, Production and Slate
>   clicked through with real workers. If the head app restarts during a job, a worker's finished clip is not fetched.
>
> **2.6.6 (2026-10-13): what two music-video productions found.**
> Two three-minute videos were made through the app on up to five DGX Sparks; this release fixes what they ran into.
> - **A memory guard on a DGX Spark.** A clip with a LoRA, at 1376x768, took a Spark down. A job is now checked
>   before it is queued and again before anything loads: the estimated peak comes from measured renders (int8 engine
>   54.5 GB + 0.215 GB per 1000 rows; ComfyUI's bf16 blocks, which a LoRA uses, 75.0 GB + 0.047 GB), and a LoRA clip
>   larger than the largest one measured (1280x736, 124 frames) is refused with the size that fits. Beside every
>   render server a watch reads memory twenty times a second (the old loop read once a second) and stops the render
>   under a floor of a tenth of the machine. That exact job is now refused by the app and by the scripts. Why the
>   Spark died is only partly established: its logs were never read.
> - **Edit a picture**: any seed gives an edit (the seed that made the source used to return a sharpened copy), up to
>   nine further pictures, and the result has the source's own size. A given seed gives a different picture than in
>   2.6.5.
> - **Any clip length the model takes**: 21 lengths from 22 to 362 frames, in place of 5, 8, 10 and 15 s.
> - **A prompt check for negations**: "no photographs" produced photographs. The page lists negated things before a
>   job is queued; it never changes the prompt. The app's own added lines are now positive ("Mouths stay closed.").
> - **A shot check after a render**: whether the first and last keyframe were held, any hard cut inside the clip, and
>   the longest stretch without one, with an optional retake at the next seed. Thresholds are set on the first
>   production's clips (12 of 12 cuts found, none invented in 7081 frame pairs). A dissolve to a close-up is not
>   detected, and no wording or setting tried stops the model from cutting.
> - **Slate for music videos**: tempo and bar grid on the project, shot slots in bars, a LoRA per shot (through the
>   guard), a change action for characters and places, the shot's intent kept out of the prompt, and takes rendered
>   on another machine imported into a shot.
> - **720p on a DGX Spark without ffmpeg**, a QA gate that says when it did not run (and can run on a head node for a
>   clip from a worker), and an API that keeps answering while a LoRA job starts.
> - Not tested: the new pages in a browser beyond a load, a Mac under 96 GB, reference clips at the new lengths, LoRA
>   clips above 1280x736 (on purpose), and the First AD's new actions with a live brain. On a Mac one short clip was
>   rendered through this build; the memory guard does nothing there.
>
> **2.6.5 (2026-10-13): 3D Animation Style, and Multiple Angles in the essential package.**
> - **3D Animation Style** (SOLRICKS) installs and renders: its file is in kohya's own layout
>   (`lora_unet_blocks_0_attn_qkv_proj`), which the hub refused. Measured on a DGX Spark, same seed with and without
>   at strength 1.0: the clip becomes a 3D animated film. On a Mac the file is rewritten for the engine; no clip has
>   been rendered with it there. Its author approves each download, so it is not in the hosted package.
> - **The essential package is at its third build**: 14 LoRAs per platform (2.1 GB), now with Multiple Angles.
>   The hub counts 22 of 22 presets usable on a DGX Spark and 20 on a Mac (Upscale and Remove a person are DGX
>   Spark only).
> - Carrier image `ghcr.io/drowzeys/keys-tensorfold-studio:2.6.5`: the same engines, with this release's render
>   scripts.
>
> **2.6.4 (2026-10-13): Edit a picture, the Multiple Angles LoRA, and what each machine can use.**
> - **Edit a picture**: a third thing a prompt can go to (Create, *The prompt goes to*). Qwen-Image-2.1-Turbo re-draws
>   a picture you give it, from words or, with the **Multiple Angles** LoRA (akhaliq), from a camera ring of 12
>   directions and 4 heights with a close-up switch. *edit* and *new angle* on a Library picture open it; a result
>   goes to the Library and can be a clip's first or last frame. Through both apps' own queues: an edit in words 17 s
>   on the Mac and 29 s on the DGX Spark, five views 57 s and 85 s, and a 5 second clip from one view to the next
>   98 s and 133 s.
> - **What Multiple Angles does not do** (measured on both machines, on Turbo's 8 steps, with its author's own
>   phrases): the quarter views turn the way asked, but a straight left and a straight right side view can come out
>   facing the same way, and a picture of a whole scene is often reduced to one thing in it on a plain background.
>   Its author's setting is 40 steps on the base model (`--image-edit` on either installer; not measured here).
> - **The hub counts what this machine can use**: 21 of the 22 presets on a DGX Spark and 19 on a Mac (Upscale and
>   Remove a person are DGX Spark only; 3D Animation Style waits on its author's approval of the download). A LoRA
>   that a Library action or Edit a picture uses was counted as not usable in 2.6.3; the bundle script's menu and
>   `--json` summary say the same now (`usable_here`).
> - **Remove a person leaves less behind**: the frames that kept green go through the remover a second time in up to
>   three stretches of 22 frames (`--again`, about 70 s each), and dull green patches a window leaves at a clip's end
>   are painted over from the frames before. On the test clip the second look brought back 5 of 21 frames clean in
>   208 s more, and the blocks on the last frames are gone; a faint green cast on the floor of the last frames stays.
>   Through the DGX Spark app the whole removal of the 5 second clip took 1051 s (820 s without the second look).
> - **Upscale then Remove a person, and Remove then Upscale, back to back** through the DGX Spark app: 229 s, 870 s,
>   219 s, lowest free memory 27 GB. Each starts from a ComfyUI server the other has not run in.
> - **A picture edit straight after a Library action** starts from a fresh ComfyUI server (one run had dipped to
>   12.7 GB free). Through the app, an edit queued right after a removal: 41 s with the restart, lowest 35 GB free.
> - **A fresh DGX Spark install of this version** (a clean clone into an empty prefix, the weights already on the
>   disk): `oneshot-setup-spark.sh --no-render --ref2va --lora-bundle=PATH` cloned the three node packs at their
>   pins and installed the LoRAs from the bundle; a Turnaround sheet through that copy's app took 92 s. Not tested:
>   the weight downloads themselves, and a fresh Mac install of this version.
> - **Through the apps (2.6.3's Library actions, measured after its release)**: Turnaround sheet 512 in 55 s on the
>   Mac and 89 s on the DGX Spark, 1024 in 160 s and 174 s; Upscale of a 5 second 864x480 clip to 1984x1088 in 319 s;
>   Find the person 58 s; Remove a person 820 s.
>
> **2.6.3 (2026-10-13): the LoRA hub with twenty-one community LoRAs, a last frame, LoRAs on clips from references, and Library actions.**
> - **Keys and Wasserman LoRA Hub** in seven groups (01 Camera & Viewpoint to 06 Support & Finishing, and 07 Fun), with
>   the one-command essential bundle (`oneshot-essential-bundle.sh`): thirteen LoRAs from one hosted package per
>   platform, the rest from their authors' pages, every file checked, then no network needed again.
> - **A last frame**: a clip can end on a picture; 360° Orbit and Transformation use it.
> - **LoRAs on a clip from references**: Character Swap, Lineart Anime (with *Make line art of Video 1*), Keyframe
>   Animation and the Anime Slider.
> - **Library actions**: a Turnaround sheet from a picture, Upscale a clip, and Remove a person from a clip.
> - **In the wizard**: camera moves as a choice, a slider centred on 0, and one list of what a picked LoRA still needs.
> - **Edit a picture**: a third thing a prompt can go to. Qwen-Image-2.1 re-draws a picture you give it, from words
>   or, with the Multiple Angles LoRA, from a camera ring of 12 directions and 4 heights. The hub now tells LoRAs for
>   pictures from LoRAs for clips.
> - **Tested through both apps' own queues** (the table under *Keys and Wasserman LoRA Hub*). 3D Animation Style
>   installs once its author approves the download.
> - `oneshot-setup.sh --upgrade` keeps a helper folder that is not a clone (a prefix layered on another install).
>
> **2.6.2 (2026-10-12): a fresh DGX Spark install, checked from a clean clone.**
> - **Tested:** a clone of the `v2.6.1` tag installed into an empty prefix on a DGX Spark with
>   `oneshot-setup-spark.sh --ref2va --motion` (the weights already on the disk, so no weight was downloaded; every
>   weight address the installer fetches answers without a login, and so do the carrier image and the engine's
>   commits on the fork), then the installer's own test clip from that copy: 5 s at 864x480 in 116 s on TensorFold's
>   CUDA engine, with picture and speech. Not tested: the weight downloads themselves from an empty disk.
> - **`SPARK_COMFY_PORT` now reaches the renderer too.** `spark/comfy.sh` started ComfyUI on that port while the
>   renderer still asked on 8190 (only a second install beside a running one hit this; `SPARK_COMFY_URL` still wins).
> - **`spark/build-engine.sh` reads the prebuilt Spark engine from the `2.6.1` carrier image** (the same engine build
>   as the `2.5` one it read before).
>
> **2.6.1 (2026-10-12): reference videos and audio on a freshly installed Mac.**
> - **The Mac installer installs the engine that reference videos and audio need** (TensorFold fork branch
>   `ref-video-audio`, pinned to `a2b592f`; carrier image `ghcr.io/drowzeys/keys-tensorfold-studio:2.6.1`). 2.6
>   installed the engine before it, so on a new Mac those two kinds of reference stopped with a message. Running
>   `oneshot-setup.sh` again on an existing install replaces the engine. The DGX Spark build is unchanged.
> - **Library tiles have two rows:** the file name, then the buttons. A long name pushed *+ timeline* out of the tile.
> - **One command upgrades an existing install:** `git pull && bash oneshot-setup.sh --upgrade` on either machine.
>   No weights are fetched and the studio folder is not touched. See *Upgrade from 2.5 or 2.6* under each install.
> - **A Motion Previs tutorial:** a 13-step lesson in the app (*Tutorial → Motion Previs*) and
>   [TUTORIAL.md, section 9](TUTORIAL.md#9-motion-previs-make-a-clip-that-moves-like-another), with two worked examples.
>
> **What was tested for 2.6.1** (2026-10-12). *Mac Studio M5 Ultra:* a fresh clone installed into an empty prefix
> (weights already on the disk, engine from the public fork at `a2b592f`), then one reference-video clip (284 s) and
> one picture-with-reference-audio clip (91 s) through that copy's app, Turbo 4 passes, 5 s at 864x480; a 2.6 install
> (engine `a2068c0`, on which both requests stop with the message) moved up with `--upgrade` in 19 s, the same two
> clips after it (288 s and 91 s), a second `--upgrade` in 3 s with nothing changed, the studio folder and the model
> folders untouched. *DGX Spark:* `--upgrade` twice on the existing install (the first run added the QA gate's
> speech-to-text packages, the second changed nothing), `--verify`, and one 5 second clip after it (109 s). The
> tutorial's 13 steps were walked in headless Chromium on the Spark app: each link lands on its screen. Not tested:
> an upgrade of an image-only install and of a Mac without tensor units (a fresh DGX Spark install: see 2.6.2).

> **2.6 (2026-10-12): reference videos and audio, Motion Previs, and the two joined in the create wizard.**
> **What's new in 2.6:**
> - **Reference videos and reference audio** for MiniMax H3 Ref2VA beside reference pictures (`REF_VIDEOS`,
>   `REF_AUDIOS`; *The clip starts from → References* in the app): a video carries a subject and how it moves, an
>   audio clip a voice or music. See [Reference videos and reference audio](#reference-videos-and-reference-audio-ref2va).
> - **Motion Previs**, a new tab: a Python port, modified, of the analysis of Sam Wasserman's Motion Previs Studio.
>   A shot becomes a pose skeleton, depth, edges, a camera move in words and a production pack. See
>   [Motion Previs](#motion-previs-a-shot-into-control-layers-and-a-camera-move). Installed with `--motion`.
> - **The two joined.** On the references screen of the create wizard, *Analyse motion* runs Motion Previs on
>   `<Video 1>` and offers what to carry over (camera only, actor motion, object motion, full scene) and what goes to
>   the model as `<Video 1>`: the source clip (the default for the same place with a new person), the depth layer
>   as a control video (the default for a new place or the camera alone), or the skeleton (offered, the loosest);
>   the camera move is added to the prompt. *Use as motion reference* in the Motion
>   Previs tab opens the wizard filled in the same way. Without the motion environment a reference video is a plain
>   reference, as before.
> - **Sound and movement agree.** *Sound and movement* under *Analyse motion*: **Impacts** (fast strikes and blocks,
>   with their sound), **Flow** (slow movement, silent or under music) or **Automatic**, which never lays hits over a
>   reference that moves slowly. Every finished reference clip is measured for it and flagged when it does not agree.
>   A source clip's mouth is kept out of a clip in which nobody speaks. See
>   [Sound and movement of a motion reference](#sound-and-movement-of-a-motion-reference-impacts-or-flow).
> - **Clear and delete.** Every prompt form has a *Clear* with an *Undo* (a wizard step, the whole draft, a segment,
>   every segment, a Slate shot's prompt). In the queue, 🗑 deletes a finished job with its clip, check sheets and
>   the first frame only it used; *Delete selected*, *Clear failed* and *Delete all finished* do the same for
>   several. The Library deletes one or several ticked files. Everything goes to the trash folder and the last
>   deletion can be put back; *Empty trash* shows the size and erases it after a confirmation. See
>   [Clearing prompts and deleting clips](#clearing-prompts-and-deleting-clips).
> - **The reference step checks each file as you add it**: a clip under 2 or over 15 seconds, more than 15 seconds of
>   one kind, a tenth picture, a thirteenth reference or a silent "audio" file is turned away with the reason, and
>   *the videos' own sound* can only be ticked when every video has sound. A motion layer (skeleton, depth) now
>   carries the shot's own sound, so that box works with any layer. A failed job shows the renderer's own last
>   words, not "exited with status 1".
> - **No more voice that nobody asked for in reference clips:** the app writes the sound of a reference prompt under
>   its own `overall_soundscape` heading. Written on the end of the shot, the model sometimes read it out.
> - **Sound on a DGX Spark** is made from a lossless copy of the decoder's output and limited to -1 dBFS; it used to
>   be ComfyUI's own track, which overshot full scale.
> - `STEPS` sets the passes of a reference clip; *Custom* is the card beside *20 passes* in each passes chooser; a first frame or a
>   reference picture reaches the Mac's text encoder with the checkpoint's own normalisation (the DGX Spark build
>   never had that fault: ComfyUI's encoder already used the checkpoint's values).
>
> **What was tested where.** *Mac Studio M5 Ultra:* reference videos, reference audio, `STEPS`, the normalisation fix
> and the Motion Previs analysis, as their sections say; the source clip, a depth layer and a skeleton as
> `<Video 1>` with a picture, measured against the source shot (the figures are in the Motion Previs section). The Mac renders run on the TensorFold engine branch
> `ref-video-audio`, which the installer has installed since 2.6.1. *DGX Spark (GB10):* everything in
> [On a DGX Spark](#reference-videos-audio-and-motion-on-a-dgx-spark): reference videos and audio through ComfyUI's
> node, the Motion Previs analysis on Linux, and the wizard's flow (upload, analyse, choose, compose, render) driven
> through the app's HTTP API with the source clip and with the skeleton layer.
> **Checked on the Spark after the join:** the new screens by hand in a headless Chromium at desktop and phone
> width (every card of the reference step, the motion panel, the Motion Previs tab and its hand-off, the limits,
> one clip made start to finish through the page); a video's own sound, a voice recording, and both together, by
> measurement ([what a sound reference does](#reference-videos-audio-and-motion-on-a-dgx-spark)); two reference videos
> in one request; an 8 second clip from an 8 second shot; the object-motion and camera-only modes in a render.
> **Sound and movement** (Impacts, Flow with and without music, the soft mouth) was rendered on both machines
> through the wizard's API calls and looked at frame by frame. The controls were then clicked in a headless Chromium
> against both machines' running apps: a reference video added, *Analyse motion*, each of *Automatic*, *Impacts* and
> *Flow*, the mouth switch off and on, and one Flow clip queued from the page (DGX Spark 206 s, Mac 242 s, 5 s at
> 864x480, 4 passes; both measured slow and silent, "agree"). **Clear and delete** were driven the same way on both
> builds against a studio folder of test clips (25 checks each).
> **Not tested on either machine:** the motion figures on more than a few shots or seeds; voice cloning (not
> promised: see the measurements); three reference videos at once; a real Motion Previs Studio export ZIP;
> `REF_VIDEO_SIZE=full` (refused on the Spark, for memory). Not tested on the Mac: the join after the merge (the Mac
> tests were made on the two branches before they were joined).

> **2.5 (2026-10-11): Wasserman Slate AI Studio, the Wasserman Sharp Spec with its QA gate, and the Keys and
> Wasserman LoRA Hub.** On both machines, with Sam Wasserman (wassermanproductions.com). **Wasserman Slate AI
> Studio** is a second way to plan a film in Production: a port of the workflow of his Slate (First AD, Bible,
> Casting, Art Department, Locations, Lookbook, coverage, six-section shot prompts) wired to the render queue, with
> notes and **Regenerate** on every take; see [Wasserman Slate AI Studio](#wasserman-slate-ai-studio). **Wasserman
> Sharp Spec** is one button for his locked recipe (1376x768, FastH3 8 passes, seed 42), followed by his QA gate on
> each clip: the spoken line by transcript, three frames by a vision model, one retake at the next seed; see
> [Wasserman Sharp Spec](#wasserman-sharp-spec). **Keys and Wasserman LoRA Hub** is a library of style LoRAs a
> clip, a board or a film renders with, starting with his 80s Horror LoRA; see
> [Keys and Wasserman LoRA Hub](#keys-and-wasserman-lora-hub). Also: a Home button and a glow on the active tab, a
> way to delete Library files (they go to a trash folder), and Tutorial lessons for the hub and for a whole film in
> Slate. Not tested: the Codex and local-model brains, a LoRA stacked with another or added through the form, and the
> QA gate's retake on a real failed clip.
>
> **Since 2.5:** the app is named *Wasserman Slate AI Studio, powered by TensorFold*.

> **2.4 (2026-10-11): reference pictures to video, a Tutorial tab, your own size and passes.** On both machines. **Reference pictures** (`--ref2va`, MiniMax H3 Ref2VA): up to nine pictures of who or
> what must look the same, in a scene the prompt describes; see
> [Reference pictures to video](#reference-pictures-to-video-ref2va). **Tutorial** is a fifth tab in the app: short
> lessons for Create, Production, Editor and Library, where *Show me in the app* opens the tab and outlines the
> control a step is about, and a bar at the bottom walks the lesson from there. **Custom** takes a
> width, a height and a number of passes. In the app: a running job shows the time left, not the time spent; a clip
> added to the timeline shows at once; step 5 has a way back to step 1; the two boxes of step 1 are named for what
> goes in them. When a clip has spoken lines the prompt now asks the speaker to stay silent after the last one (a
> line shorter than its clip was sometimes followed by invented speech; three words a second is the rule of thumb).
> Speed is unchanged. Not tested: `--ref2va` from an empty disk (the files were already on the
> test machines), and whether the new prompt wording stops the invented speech in every case.
>
> **2.3 (2026-10-09): a DGX Spark build on TensorFold's own CUDA engine, Qwen-Image-2.1-Turbo for images, one install
> command.** The same Studio now runs on an NVIDIA DGX Spark: FastH3's transformer on a new CUDA family of TensorFold
> (the Mac engine's design in int8 on the GB10's tensor cores), with ComfyUI hosting the text encoder, the sampler
> and the decoders. A 5 second 864x480 clip takes **73 s** there (118 to 121 s on ComfyUI's own blocks), 1280x720
> 183 s and 1344x768 about 195 s; see [DGX Spark (CUDA) build](#dgx-spark-cuda-build). `bash oneshot-setup.sh` picks
> the Mac or the Spark build by itself, and the web app's header says which one you are in: a purple
> `MAC · APPLE SILICON MX · H3-METAL` or a green `DGX SPARK · NVIDIA GB10 · H3-CUDA`.
> **Images come from Qwen-Image-2.1-Turbo**, the Qwen team's own 8-step checkpoint, in place of the base model with
> Viggle's adapter: on the M5 Ultra a 1344x768 image takes 8.9 s (9.4 s before) and peaks at 15 GiB instead of 28,
> since no adapter is merged. An install from before keeps working on what it has; the setup fetches Turbo's
> transformer (14 GB) and links the text encoder and decoder it already holds. Video on the Mac is unchanged, and
> so is its speed (TensorFold 1.0.4 was tried under the engine: the same 5.3 s a pass).
> **For people at 864x480, start from an image** (the default flow: prompt, still, then video). Text straight to
> video at that size gave distorted close-up faces in tests on both ComfyUI's blocks and the engine; from a still the
> face holds. **A shot with several linked actions works better as one action per segment**, each opening on the
> last frame of the one before (the Production tab), than as one prompt.
>
> **2.2 (2026-10-08): 8-bit FastH3 weights, so a 64 GB M1 to M4 Mac fits too.** Those chips have no tensor units, so
> they run FastH3 on the MLX engine, which held the whole checkpoint in bfloat16: 64 GiB. The setup now writes the
> 50 blocks' weights once in MLX's 8-bit quantized form (37 GB on disk, converted a block at a time in 9 GiB of
> memory) and a clip peaks at **37 GiB** (40 GiB at 720p). Emulated on the M5 Ultra with its int8 kernels and native
> engine switched off: the same scene and framing as the bfloat16 render with different fine detail, and no slower
> (31 s a pass against 30 s). It is slow next to an M5's native engine (5.3 s a pass), and **no M1 to M4 Mac has run
> it**: expect a 5 second 480p clip to take many minutes there.
>
> **2.1 (2026-10-08): the default install is FastH3 only, and a clip now peaks at 32 GiB instead of 61.** FastH3 is
> the faster engine, so MiniMax H3's own transformer and its Turbo adapter are now an option (`--turbo`) instead of
> part of every install: 64 GB less to download, and the memory check drops from 128 GB to **64 GB on an M5-family
> Mac** (96 GB on earlier chips). Three changes made the room, none of which changes a pixel or a sample: the text
> encoder runs one layer at a time (47 GiB down to 2.4), the prompt export reads only the FastH3 weights it uses
> (69 GiB down to 28), and the decoders stop hoarding freed buffers (up to 60 GiB down to 14 to 32). Measured on
> the M5 Ultra; no 64 GB or 96 GB Mac has run it yet.
>
> **2.0 (2026-10-08): FastH3 runs on TensorFold 1.0's native Zig + Metal engine, from text or from an image.** Thank you to
> **Ash Hart** for the TensorFold 1.0 runtime and to **FastVideo / Hao AI Lab** for FastH3. Every transformer pass of
> a FastH3 clip now runs in a small native program with no MLX in the loop: **5.33 s a pass against 7.12 s** on the
> MLX engine at 864x480, 124 frames (6.14 s against 8.08 s from a Qwen-Image first frame), and a 10 second 1280x720
> clip in **388 s**. Prompt encoding and the video and audio decoders are still Python. Image generation and the
> Turbo adapter path are unchanged, on the MLX engine. `oneshot-setup.sh` installs the engine from the prebuilt
> carrier; nothing else changes in how you use the studio. See [The native engine](#the-native-engine-new-in-20).
>
> **1.9 (2026-10-07): a restyled web app with every choice on one screen, a one-click Mac app, and long videos.**
> Pick where the prompt goes (video or image only), the engine, passes, resolution, 2x upscale and a length from 5 s
> to 30 min (chained 15 s clips); `bash scripts/make-app.sh` builds a double-clickable app. See
> [Install](#install-and-the-one-click-app) and [Using the studio](#using-the-studio).
>
> **1.8 (2026-10-07): FastH3 text to video, 4, 8 or 20 passes, at 480p or 720p with an optional 2x decoder.**
> FastVideo's distilled FastH3 8-Step V2 now runs on a TensorFold tile-sparse attention kernel for the M5 tensor
> units: a 5 second 480p clip with sound in **51 s** (4 passes) or **78 s** (8), 720p in 112 s or 192 s, and 2K
> (2560x1440, through the 2x decoder) in 203 s. `bash scripts/fast.sh "a prompt" out.mp4`; see
> [FastH3](#fasth3-new-in-18). It also animates a Qwen-Image first frame: image and 5 second 480p clip in 103 s.
>
> **1.7 (2026-10-07): the 20-step mode is twice as fast.** `QUALITY=high` now uses a velocity cache and attention
> reuse after mlx-serve's fast recipe: a 10 second 1312x736 clip in 1,301 s against 2,763 s for plain 20 steps, with
> stills that hold up beside it. `QUALITY=full` keeps the plain 20 steps.
>
> **Update, 1.5 (2026-10-04): a new standard setting, and the best result this pack has made.** Video now takes
> **5 Turbo passes**, and the **sound is made again by the base model** against the finished picture. Judged by eye
> and ear by the pack's owner on speech and singing clips: 5 passes gave the best picture among 3, 4, 5 and 6, and
> the base-model sound fixed what was wrong with the adapter's (harsh, echo-like, thin). A 15 second clip takes 219 s
> this way against 606 s for the full 20 steps, which remain one switch away: `QUALITY=high`.

## Install, and the one-click app

Everything the studio makes and keeps (renders, projects, uploads, voice, music, LoRAs, settings) is in
`~/ImaginationStudio`, or in `STUDIO_HOME` when that is set. An install from before the name kept it in
`~/TensorFoldStudio`: the studio moves that folder once to `~/ImaginationStudio` when it starts (a rename on the same
disk, nothing copied) and leaves `~/TensorFoldStudio` as a link to it (`app/studio_home.py`).

### Minimum requirements

| What you want to run | Memory | Disk | Chip |
|---|---|---|---|
| Images only (`--image-only`) | **48 GB** | 33 GB | Any Apple silicon; M5 for the int8 kernels |
| The default: images and FastH3 video, the web app | **64 GB** | 175 GB | M5 family: the native engine, fastest |
| The default on an M1 to M4 | **64 GB** | 212 GB | FastH3 on the MLX engine with 8-bit weights, much slower |
| With `--turbo`: MiniMax H3 Turbo and its 20-step modes as well | **128 GB** | 240 GB | M5 family for the fast path |
| With `--ref2va`: reference pictures to video (MiniMax H3 Ref2VA) | **128 GB** | 64 GB more | M5 family for the fast path |

The setup enforces the memory figures. All of this was measured on one machine, a Mac Studio M5 Ultra with 256 GB:
the figures below are what each stage of a job used there (`/usr/bin/time -l`, peak memory footprint), not a run
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
| Mac Studio M5 Ultra, 256 GB, macOS 27 | **Tested.** Every number in this README is from this machine. |
| M5 Max or M5 Pro, 64 GB or more | Untested. Meets the memory check for the default install with half the memory to spare; less GPU, so expect longer times. |
| M5 family, 48 GB | Untested. Images only (`--image-only`); the setup refuses video under 64 GB, though the measured peak is 32 GiB. |
| M1 to M4, 64 GB or more | Untested on real hardware; emulated on the M5 Ultra. No tensor units: FastH3 runs on the MLX engine with 8-bit weights, about six times slower a pass than the native engine even on an M5 Ultra's GPU, and slower again on an older one. |
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
git clone https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3.git
cd keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3
bash oneshot-setup.sh --app             # engine, image model, FastH3, 2x decoder, the app; ends with a test clip
bash oneshot-setup.sh --turbo --app     # the same plus MiniMax H3 Turbo (64 GB more on disk, 128 GB of memory)
bash oneshot-setup.sh --ref2va --app    # the same plus reference pictures to video (64 GB more on disk, 128 GB of memory)
```

#### Upgrade from 2.5 or 2.6 (Mac)

```bash
cd keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3 && git pull
bash oneshot-setup.sh --upgrade         # add --app to rebuild the double-clickable app as well
```

`--upgrade` moves an existing install to the version of the clone: it replaces the TensorFold engine in the
install's own environment when it is older than this version needs (2.6.1: the build with reference videos and
audio), copies the render scripts, and adds the Motion Previs environment when it is missing (0.5 GB). It **fetches
no weights**: a model that is not on disk is named and left out, and an image-only install stays image-only. It does
not touch `~/ImaginationStudio` (clips, projects, LoRAs, settings), prints the version before and after, and ends with
`--verify`. Running it a second time changes nothing. Quit and reopen the app afterwards. A plain
`bash oneshot-setup.sh` after `git pull` upgrades the engine too, and also fetches any model the install lacks.


**Step-by-step tutorial with screenshots: [TUTORIAL.md](TUTORIAL.md).**

Then **double-click Wasserman Slate AI Studio** on the Desktop or in `~/Applications` (drag it to the Dock if you like). `bash scripts/make-app.sh` rebuilds the app on its own. It starts the studio
if it is not running and opens it in your browser; double-click again to come back to it. Without the app:
`bash scripts/app.sh`, then open http://127.0.0.1:7870.

**Or hand it to your coding agent.** Paste this:

> Clone https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3 and follow its AGENTS.md section "Install for a
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

**Automatic updates** is off by default. Turn it on to check every six hours while Studio is open and install when
its queue and render engine are idle. The setting is saved for this installation. Updates preserve projects, media,
models and runtime environments. Updates only move the branch forward: local code changes, untracked files, commits
of your own that are not on GitHub, or a detached checkout pause updates without discarding work. Updates requiring changes to installation
dependencies show a message asking you to follow that version's setup instructions.

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

## Using the studio

![Create: text to video with FastH3](samples/ui_create_fasth3.png)

Step 1 of **Create** is where every choice lives. Pick one card in each row; the bar under them shows what will run
and roughly how long it takes.

| Choice | What it does |
|---|---|
| **The prompt goes to** | *Video with sound*; *Image only*: the picture model makes the pictures and stops there; or *Edit a picture*: it re-draws a picture you give it. |
| **Picture model** (where more than one is installed) | *Qwen 2.1 Turbo*, *Qwen 2.1 Full*, *FLUX3-K Fast* or *FLUX3-K Full*, for images, scouts, a last frame and edits: see [Which picture model to use](#which-picture-model-to-use-the-evidence). |
| **The clip starts from** | *Text only*; *a scout image* (step 2 makes several with the picture model, you pick one); *your own image* (step 2 uploads it); or, with `--ref2va` installed, *references* (step 2 takes up to nine pictures: who or what must look the same, in a scene the prompt describes; where the engine packs them, also up to three reference videos and three audio clips: see [Reference videos and reference audio](#reference-videos-and-reference-audio-ref2va)). |
| **Video engine** (shown only with `--turbo` installed) | *FastH3*: fastest; text to video runs on the native Zig engine, and the bar under the cards says which engine a clip will use. *MiniMax H3*: the Turbo adapter with the sound made again by the base model, or the full 20 steps. |
| **Passes** (FastH3) | 4 (fastest, a little softer), 8 (what it was trained for), 20 (for prompts with several actions; slowest). |
| **Quality** (MiniMax H3, with `--turbo`) | Turbo 5 + base sound (the standard), Turbo 3, 20 steps with the fast recipe, or plain 20 steps. |
| **Resolution** | 480p (864x480), 720p (1280x720), the fixed outputs Draft, 2K 2048x1152 (Mac) and Native 1344x768, Wasserman Sharp 1376x768, or Custom (your own width and height). |
| **2x upscale** | For 480p and 720p: the 2x decoder turns 480p into 1728x960 and 720p into 2560x1440, for about 10 s more. |
| **Length** | 5, 8, 10 or 15 s as one clip, or *Another length…* for any length the model takes (below); 30 s to 30 min as a chain of clips (further below). |
| **Seed** | Same seed and settings give the same clip. |

Then write the scene, what happens to the characters, and optionally the camera move. Steps 3 to 5: add spoken or sung lines and
the soundscape, review the exact prompt the model will receive (edit it freely), and queue the render. The **Queue**
on the right shows progress by pass, a time estimate, the log, and a cancel button.

### Clip lengths: any length the model takes

MiniMax H3 and FastH3 make clips of `17n + 5` frames at 24 frames a second and nothing in between. The four usual
lengths are four of those counts, and their names are rounded: "10 s" is 243 frames, 10.125 s. *Another length…*
in the Length box opens a slider over every count, and the page writes the frames and the exact seconds beside it.

| | Frames | Seconds |
|---|---|---|
| Shortest | 22 | 0.917 |
| The usual four | 124, 192, 243, 362 | 5.167, 8, 10.125, 15.083 |
| All 21 | 22, 39, 56, 73, 90, 107, 124, 141, 158, 175, 192, 209, 226, 243, 260, 277, 294, 311, 328, 345, 362 | frames / 24 |

Through the API a job carries `frames` (one of the counts; another number is refused with the two nearest) or
`seconds` (any number from 0.9 to 15.1, brought to the nearest count: `seconds: 6` is 141 frames, 5.875 s). The end
moment in a last-frame prompt is the clip's true length. Production segments and Slate shots take any length the
same way. The longest clip stays the model's 15 seconds on every size of both builds. On a DGX Spark that was
measured at the largest canvas, 1376x768 with a first and a last frame: 362 frames rendered with 29.7 GB of the
machine's 121 GB still free at the lowest point (one run, TensorFold's int8 engine, 4 passes; the memory does not
depend on the passes). A clip with a LoRA renders on the same engine with the LoRA merged into it (2.7.1) and is
taken up to 1376x768 at 243 frames, the largest it was measured at, and refused above that with the reason and what
fits (*The memory guard*, in the DGX Spark section). On the Mac
22, 141 and 345 frames were rendered at 864x480 through the app (26 s, 72 s and 226 s a job on an M5 Ultra, native
engine, 8 passes); its limits are the ones it had.

### What the app tells you about a prompt and a clip

**Words that forbid.** The video model is run without a negative prompt, so every word in a prompt is something to
show. In the production run that led to this check, a clip told "No photographs or portraits appear anywhere" hung
the wall with photographs, twice, and came out clean once every mention of photographs was gone. Step 4 and step 5 of
*Create* (and a Slate shot's preflight) now list what a prompt forbids by name ("no X", "without X", "not X",
"never X") with the reason, in the box it was written in. Nothing is taken out or rewritten: the prompt goes as you
wrote it, and the job in the queue repeats the list. The Studio's own sentences forbid nothing by name any more: a clip without spoken
lines used to be told "Nobody speaks." and "No voices, no speech.", and a locked camera in Slate "the camera does not
move". They now read "Mouths stay closed." (only where the shot has people: Slate's cast, or a person in the words),
"Only ambient sound is heard." and "Static shot on a locked, still camera.", and a prompt the app writes passes its
own check with nothing passed over. One comparison was made, not a study: the same two-person shot at 864x480, 5 s,
seeds 42 and 43, in the old words and the new; in all four clips the speech-to-text model found no speech, and the
mouths stayed closed in the frames looked at. A prompt that still carries the old sentences is not flagged for them.
No route of the app has a negative prompt field: FastH3, MiniMax H3 Turbo and Qwen-Image-2.1-Turbo all run one forward a pass.
`POST /api/prompt/check {"prompt": "..."}` gives the same list for a prompt written by hand.

**The shot check.** After a single clip is rendered the app looks at its frames (`app/shot.py`) and writes the
verdict beside the clip in the queue:

- *First frame, last frame.* A keyframe is something the model reads, not a frame written into the clip, and
  almost always the clip opens and ends on it. In the same run 63 of 64 keyframes were held (a mean difference of
  1.4 to 3.9 grey levels of 255 between the frame and the picture); one clip opened on another picture (74.4), so its
  join with the clip before jumped. Rendered again on another Spark at the same seed it came out the same, frame for
  frame: it is what the model does at that seed. The check compares frame 0 with the first picture and the last frame
  with the last, and says "not held" over 12 grey levels.
- *A cut inside the shot.* FastH3 sometimes cuts to a close-up that is in neither keyframe: 12 cuts in 9 of that
  run's 32 clips. The check finds a frame that is unlike the one before it (a correlation under 0.70) and changes at
  least 1.75 times as much as the frames around it; it found all 12 and nothing else in the 7081 frame pairs. A
  prompt that writes a `[Shot 2]` asks for a cut and is not checked for one. A cross-fade is not a cut.
  No wording or setting we tried stops it. One of those clips (a couple dancing in a hall, small in a wide frame,
  8 seconds at 1280x736) was rendered again at its own seed eight ways: as it was; with a camera line in plain
  positive words ("one continuous wide shot from a locked-off camera…"); with the run's "No cut, no close-up" line;
  on ComfyUI's own bfloat16 blocks in place of TensorFold's int8 engine; with dense attention; at 20 passes; at 5
  seconds; and with the first frame alone. All eight left the wide shot for close-ups of the two faces. Five did it
  with a hard cut, which the check reports. Three (the "no close-up" line, the first frame alone, 20 passes) did it
  with a dissolve through a double exposure, **which the check does not see**: half a second of a dissolve measures
  like half a second of honest fast movement in the run's other clips, so there is no threshold to set. The plain
  positive camera line was also tried on the two other dining-hall clips at their seeds: both cut, twice each. So
  look at a clip yourself, whatever the check says: it reports hard cuts and nothing subtler.
- *The retake.* Tick "If the shot check fails, render it once more at the next seed" in step 5 (`retake: true` in a
  job), and a clip that fails is rendered a second time at seed + 1, like the Sharp Spec QA gate; the failed take is
  kept in the `qa` folder. Without the tick the verdict comes with a button, *Render it again at seed N*. The
  thresholds come from one run on one model at one size; `shot_check: false` in a job turns the check off.

- *Where to trim.* FastH3 also opens some clips on an establishing shot nobody asked for and then cuts to the shot (4
  of 36 clips in a later run, the cut at frames 10 to 28). The check's report carries `window`: the longest stretch of
  the clip without a cut, as frames (`start`, `end`) and as the timeline's `in` and `out` seconds. The job card says
  it and has *+ timeline, trimmed to it*; a job's result has it under `shot.takes[].window`, and
  `GET /api/shot/window?path=renders/x.mp4` gives it for any clip in the studio's folder. It knows hard cuts only.
  The other thing that run met, an action carried on past where the prompt stops, is not detected by anything here.

**The Sharp Spec's QA gate, where its tools are missing.** The gate needs ffmpeg (the contact sheet, the sound) and a
speech-to-text tool. On a machine without ffmpeg, a worker Spark as installed for one, the clip is made and the job's
result now says that the gate did not run and why (`qa.skipped`, `qa.note`); so does a job queued with `qa: false`.
`/api/state` has `qa.runs` and `qa.missing`. A machine that has the tools runs the gate on a clip from one that has
not: `POST /api/qa {"from": "http://worker:7870", "job": "<its job id>"}` (the clip, the prompt and the seed are read
from that job), or `{"from": ..., "path": "renders/x.mp4", "prompt": "..."}`, or `{"path": ...}` for a clip already
here. The answer is a job to poll, outside the render queue (no GPU); its `qa` is the gate's report, with the clip
kept under `qa/fetched`. Tried between two Sparks: the head fetched four clips from a worker's app and gave each a
sheet and an identity verdict in 8 to 28 s. The transcript half had been failing on the Spark build with every clip
(faster-whisper opening the sound through an older PyAV); it now runs, 12 s a clip on the CPU.

**Without ffmpeg.** The header shows a line when ffmpeg is missing, with what then does not work (long videos, the
timeline export, a frame taken from a clip, reference videos and audio), and such a job is refused when it is queued,
with the command that installs ffmpeg, not after its render. Single clips and pictures render without it.

### Image only

![Create: image only](samples/ui_create_image.png)

Choose *Image only*, describe the picture, pick a size and how many takes. Each image has **Download** and
**animate this**, which carries it into a video as the first frame. Where FLUX3-K is installed (`--flux3`), *Picture
model* chooses between **Qwen 2.1 Turbo** (the quickest), **FLUX3-K Fast** and **FLUX3-K Full**; the same choice is
offered for scouts and for a last frame made from a prompt. FLUX3-K writes a full scene description from your prompt
before the first picture (5 to 20 s, once per prompt), then about 10 to 15 s a picture for Fast and a minute for Full.

### Edit a picture

![Create: Edit a picture, the camera ring](samples/ui_edit_camera.png)

Choose *Edit a picture* and give the picture: an upload, one from the Library, or a frame of a Library clip. Then say
what changes. Qwen-Image-2.1 makes and edits pictures with one set of weights, so this step needs nothing more than
the image model the Studio already has: the picture goes to the text encoder's eyes and into the model's sequence as
latents, and the model draws the picture again as the prompt describes it.

- **Change it in words.** Describe the result ("The same statue at night under a full moon, wet paving stones, a
  lantern on the wall behind it"). What is not mentioned is meant to stay; small things still shift.
- **Move the camera.** With akhaliq's Multiple Angles LoRA (LoRA hub, *Fun*) the step shows a ring of 12 directions,
  30° apart with the front at the top, 4 heights (eye-level, elevated, high-angle, top-down) and a close-up of each.
  Pick angles one by one or take a set (*Five views around*, *Full orbit*, *Four heights*, *Wide and close*): each
  angle is one picture from one load of the model, up to 12 a job. The ring writes the author's captions, such as
  `<mva> right side view, high-angle shot close-up`.
- **Picture model.** Where FLUX3-K is installed, *Picture model* above the picture offers **Qwen 2.1 Turbo**,
  **FLUX3-K Fast** and **FLUX3-K Full**. FLUX3-K edits from words, with further pictures too; BFL's upsampler looks
  at the pictures and writes the caption of the result first, so a short request is enough. It follows "switch the
  neon off" where Qwen left the signs lit, and is slower: an edit took 24 s with Qwen, 37 s with Fast and 139 s with
  Full on a DGX Spark, and 10, 37 and 240 s on an M5 Ultra, where Full is too slow for edits (427 s with two pictures). The LoRAs, the camera ring and the 40-step choice are Qwen's and are hidden for FLUX3-K.
- **Several pictures.** The model takes several pictures at once, so two references need no side-by-side montage
  made elsewhere. Under the picture, *Further pictures* adds up to nine more. The first picture is the one that is
  edited and is image 1 to the model; the others are image 2, image 3, … in the order listed. Name them that way:
  "The man of image 2 stands beside the bed in the bedroom of image 1". Each further picture adds its rows to every
  step: at 1280x736 with Turbo's 8 steps, one picture took 9 s on the Mac (M5 Ultra) and 15 s on the DGX Spark, two
  13 and 22 s, three 15 and 30 s, ten 36 and 230 s (the Mac's peak was 41 GiB up to three pictures and 55 GiB with
  ten; the Spark had 40.7 GB of 121 free at its lowest with ten). One set of pictures and one seed each. Give the
  pictures that are needed: with ten, several of them showing the same two people, the two were drawn twice.
- **The size of the result** is the picture's own: a 1280x736 picture comes back 1280x736 (2.6.5 returned 1344x768,
  the model's own "about 1024x1024, in the picture's shape"). A side that is not a multiple of 32 is edited on the
  model's grid and brought back (1280x720 is made at 1280x704). A picture under 512x512 or over 1536x1536 pixels in
  all is edited at the nearest of the two in its own shape and comes back at that size, and the log says so
  (2560x1472 came back 2016x1152); nothing is enlarged afterwards. *Size of the result* also offers the model's own
  sizes, and a job may carry `width` and `height`.
- **The seed.** The noise an edit starts from is drawn for the seed and the pictures together. Before, it was drawn
  for the seed alone, and an edit made with the seed its picture had been made with started from that picture's own
  noise: it came back as the same picture, over-sharpened (12 of 71 edits in one production run). Any seed now
  gives an edit. The same seed, pictures and prompt still give the same result; a seed gives another picture than
  it did in 2.6.5.
- **Results** go to the Library. Under each: *first frame* opens a clip on it, *last frame* ends a clip on it, *edit
  again* feeds it back in. A front and a back view of one subject are a first and a last frame for a turn.

![One picture, five views and an edit in words, Mac](samples/edit_angles_mac.jpg)

Measured through the app's own queue, a generated picture of a statue at 1024x1024, seed 7, Turbo's 8 steps
(below). What we saw, one subject and one seed each:

| | Mac (M5 Ultra, MLX, bfloat16) | DGX Spark (ComfyUI, bfloat16) |
|---|---|---|
| One picture, model loaded | 10 s | 15 s |
| Five views around, one job | 55 s | 86 s (25 s for the first, with the load) |
| An edit in words, one job | 14 s | 16 s |
| Base model, 40 steps, one picture | not run (the base transformer is not on this Mac) | 49 to 54 s |
| Memory | 43 GiB at its peak | 82 GB of 121 left at the lowest |

![The same on a DGX Spark, with two views from the 40-step base model](samples/edit_angles_spark.jpg)


- The picture input works without any LoRA: the edit in words kept the statue and its plinth and changed the light.
- Without the LoRA the model already turns the subject for a side or a back view and keeps the scene. With it the
  height and the close-up are followed too (an *elevated* view is from above, a *close-up* fills the frame), and a
  side view often comes back on a plain studio background: its author trained it on single objects and characters.
- *Left side* and *right side* both came out facing the same way, on both machines. Check a set before you build
  on it.
- On the DGX Spark every view with the LoRA came back on the plain background, the front view too; on the Mac the
  front, front-right and back views kept the scene.

Scripts: `bash scripts/edit.sh photo.png "what changes" out.png`; `SET_FILE=edits.json` makes several from one load;
`LORA=path:0.9` adds an image LoRA. `BASE=1` runs the 40-step base model, which is what image LoRAs are trained on:
`oneshot-setup.sh --image-edit` (13 GB more on a Mac) or `oneshot-setup-spark.sh --image-edit` (14 GB) fetches it.
It is optional: the LoRA works on Turbo as shipped. Both checkpoints are under the **Qwen Research License
Agreement: non-commercial use only**; the LoRA's own file is Apache-2.0.

On a Mac the step runs mflux's Qwen-Image-2.1 edit pipeline in bfloat16 (43 GiB at its peak); under 96 GB of memory
the app asks for 8-bit weights (30 GiB at its peak in one run on a 256 GB Mac; not run on a smaller Mac). On a DGX
Spark it is one more ComfyUI graph, with the LoRA merged at run time on the bfloat16 file.

### A scout image, then video

![Create: Qwen scout to video](samples/ui_create_qwen_h3.png)

Choose *A scout image*. Step 2 makes as many candidates as you ask for, at the size the video model starts from, with
the picture model you pick: Qwen 2.1 Turbo (six take about half a minute), Qwen 2.1 Full, FLUX3-K Fast or FLUX3-K Full. Click the
one you want. Either engine animates it. With FastH3 this is an
untrained use that worked in our one test (see [FastH3](#fasth3-new-in-18)).

### Long videos, up to 30 minutes

![Create: a 30 minute chain](samples/ui_create_long.png)

The model makes at most 15 s at a time, so anything longer is a **chain**: each 15 s clip opens on the last frame
of the one before, and the clips are joined into one file (the repeated frame at each join is dropped). Pick a
length from 30 s to 30 min. **Story beats** are optional, one line per clip, saying what happens in that part;
without them every clip gets the same prompt. Finished clips can be watched from the queue while the rest render.

- Checked: an 18 s chain of two clips (FastH3, 4 passes, 480p) in 183 s; the join is not visible in stills and the
  second clip followed its own beat. **Nothing longer has been rendered.** The times shown for long lengths are
  extrapolated from 5 s clips: about 7 hours for 30 minutes at 480p with 4 passes, about 11 hours with 8.
- Expect drift: each clip only sees one frame of the last, so faces, clothes and light can wander over many clips,
  and sound does not carry across a join. For a planned story with different shots, use Production instead.

### Production, Editor, Library, Tutorial

![Production board](samples/ui_production.png)

**Production** is for a video planned shot by shot: a project of segments, each with its own scene, action, line and
length. A segment can open on the last frame of the one before; one button renders every segment not yet done.

![Editor](samples/ui_editor.png)

**Editor** is a single-track timeline: order the clips, set in and out points, preview the sequence, export one file.
Once the Music tab has a song, *Song under the export* lays it under the whole export.

![Library](samples/ui_library.png)

**Library** lists every render, scout and export. Under a picture: *use* makes it the first frame of a new clip,
*last frame* ends a clip on it, *edit* opens Edit a picture, and *Turnaround* makes five views. Under a clip: *continue*
starts a new clip on its last frame, *+ timeline* sends it to the editor, *Upscale* and *Remove a person*. 🗑 moves a
file to `~/ImaginationStudio/trash`.

![Tutorial (screenshot from 2.4; the tab now has a part for each tab of the app)](samples/ui_tutorial.png)

**Tutorial** teaches the rest of the app in short lessons, one step on screen at a time, with an example and a tip
where they help. They are grouped in eight parts, one per tab: Create, Production, Motion Previs, Voice, Music,
Editor, Library and Sparks. A full install shows 22 lessons on a Mac and 23 on a DGX Spark (the memory guard has a
lesson of its own there); lessons for parts that are not installed, such as Ref2VA or the Sharp Spec, are left out.
Every feature through 2.9 has one: the picture models, Edit a picture, references and a last frame, long videos,
the LoRA hub, the Sharp Spec, Voice, Music, the Library actions and the Sparks setting. *Show me in the app* opens
the tab a step is about and outlines the control; a bar at the bottom then carries the lesson forward from inside
the app. Finished lessons are remembered in the browser.

### Library actions: Turnaround sheet, Upscale, Remove a person

Three LoRAs of the hub each do one job that is not "make a clip", through a workflow their authors built. The Library
has a button for each, under the file it works on, and queues it like any render. On a DGX Spark each runs its
author's own ComfyUI nodes, cloned at a pinned commit by `spark/setup-workflows.sh` (`oneshot-setup-spark.sh --ref2va`
runs it: three node packs, SAM 3.1 and the H3 latent upscaler, 2.5 GB); all three render on MiniMax H3 Ref2VA in
bfloat16. Credit and licences: [CREDITS.md](CREDITS.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

| Action | On | Does | With | Mac | DGX Spark |
|---|---|---|---|---|---|
| **Turnaround** | a picture | Five views of the picture's subject, turning from where it faces now, made together so they agree. Each is a picture of its own: a first frame, or reference pictures of one character. | Turnaround LoRA by matlod; contact-sheet nodes by MATLOWAI | yes: the MLX engine packs and decodes the five views itself (`h3_generate.py --sheet`) | yes |
| **Upscale** | a clip | The clip made larger and sharper: a latent upscale, then one light re-draw in tiles that the clip itself guides. Motion, mouths and the clip's own sound stay. | Video Upscaler LoRA and H3 Video Upsampler by dntpi; latent upscaler by LBH-123-AI | no: the upscaler is a ComfyUI node pack in PyTorch and the MLX engine has no latent upscaler or guide layout (a Mac doubles a clip with the 2x decoder: the 2K and QHD sizes) | yes |
| **Remove a person** | a clip | One person taken out and the background drawn again. The person is found from a few words and followed through the clip; "1. Find the person" shows the mask alone first. | Person Remover LoRA and H3 Relay by Akatz Labs; SAM 3.1 by Meta | no: it needs SAM 3.1, a source clip fed in as a frame-aligned guide and windows that carry 18 frames of history, none of which the MLX engine has | yes |

**Turnaround.** The LoRA's own instruction, "the camera orbits the subject of `<Picture 1>` ninety degrees
clockwise", was read as a roll of the picture on some seeds, on both engines (the model without the LoRA always reads
it so). The Studio's default says the orbit is level and the subject stays upright, which held on every subject and
seed tried; the instruction box takes other wording. How far the subject turns varies: a toy robot went from front to
back, a person in a fighting stance turned about a quarter. Sizes 512 (what the LoRA was trained at), 1024 and 2048.

**Remove a person.** The graph and settings are those of the authors' example workflow (strength 1.0, windows of 22
frames with 18 frames of history, 12 passes, mask grown by 5 points), on the bfloat16 weights. Their workflow asks for
a first frame with the person already edited out. With "Build it for me" the Studio makes one: every masked point of
the first frame takes the median of the frames where it is not masked (when the camera is still), the rest is filled
in from its edges, and one window of the remover itself then draws that rough plate sharp. The clip is lengthened by
eight copies of its last frame and the result cut back, because the last frames of the last window kept patches of
green. The whole picture is drawn again, so details outside the person can change; the clip's own sound is put back,
with the removed person's voice still in it unless "No sound" is ticked.
After the windows, green that came back for a frame or two is painted over from the clean frames beside it (at most
four frames away), and so is a frame that is a different picture altogether; what is left is counted and said on the
job. The upscaler's and the remover's node packs each
replace the layout of H3's packed sequence when they first run and do not share a server: after one of the two
actions the other starts ComfyUI again, which costs the model load.

![A turnaround sheet in the Library](samples/ui_action_turnaround.png)

![Remove a person in the Library](samples/ui_action_remove.png)

What was measured (2026-10-12, one run each unless said; Mac Studio M5 Ultra 256 GB; DGX Spark GB10 with the models
already loaded unless said; the test pictures and the clip are generated ones):

| Action | Input | Mac | DGX Spark | Lowest free memory (Spark) | By eye |
|---|---|---|---|---|---|
| Turnaround, 512 | a toy robot, a person in a stance, an animated knight | 42 to 51 s (28 passes at 1.1 s) | 45 s; 77 to 81 s with the model load | 35 GB | upright in all, same subject in all five; robot front to back, person about a quarter turn |
| Turnaround, 1024 | the same | 150 to 162 s | 188 s | 36 GB | as at 512, sharper |
| Upscale to 2 megapixels | 5 s at 864x480 | not available | 303 s, to 1984x1088 | 32 GB | wood grain, rope and lettering sharper; framing, motion and sound unchanged |
| Upscale to 1 megapixel | the same | not available | 190 s through the app's queue | 31 GB | not looked at closely |
| Find the person | 5 s at 864x480, "man in a blue shirt" | not available | 40 s | 38 GB | the whole man in every frame, hands included |
| Remove a person, clean frame built | the same clip: the camera pushes in until the man fills a third of the frame | not available | 663 s in all: mask 40 s, clean frame 94 s, six windows 525 s | 28 GB | the man gone from every frame and the rooftop drawn again behind him, the push-in kept; green had flashed back on 22 of the 121 frames where his hand moved fast and was painted over from the frames beside them, which leaves a faint green tint on a few; the last three frames keep a dim green patch on the wall |
| The same through the app's queue | the same | not available | 775 s | 13 GB (another agent's render was loaded beside it; 28 to 30 GB alone) | as above; the clip's very last frame came out as another picture altogether, which the repair now replaces from the frame before (checked on the saved clip, not by a new render) |

Not tested: 2048 views, clips longer than 5 seconds, a clip with two people where one is removed, windows of 39 and
56 frames, and any of it on a machine other than these two.

### Clearing prompts and deleting clips

From 2.6. Nothing below erases a file except *Empty trash*.

| Where | Control | What it does |
|---|---|---|
| Create wizard | *Clear this step* | Empties the text fields of the step on screen. *Music* goes back to `N/A`; a line's language stays. |
| Create wizard | *Clear all* | Empties every prompt field of the clip: scene, action, camera, lines, soundscape, music, the notes on references and the composed prompt. Pictures, clips and settings stay. |
| Production board | *Clear* on a segment, *Clear every segment's text* | Empties scene, action, voice, line and soundscape. Titles, lengths, seeds and rendered clips stay. |
| Slate | *Clear* above a shot's prompt | Empties that shot's prompt. |
| Queue | 🗑 on a finished job | The job leaves the list; its clip or stills, its QA sheets and takes, and uploads no other job in the list uses go to the trash. |
| Queue | *Delete selected*, *Clear failed*, *Delete all finished* | The same for the ticked jobs, the failed ones, or every finished and cancelled one. A clip that is in a film (a project's timeline, a Slate take) stays. |
| Library | 🗑, or tick files and *Delete selected* | The files go to the trash. |
| Library | *Restore last deletion* | The files of the last deletion return to where they were, and its job cards to the queue. |
| Library | *Empty trash* | Shows the number of files and their size, asks, then erases them. This cannot be undone. |

A cleared form is put back by *Undo* in the message or by *Undo clear* beside the button, until the page is closed
or reloaded. A queued or running job cannot be deleted (cancel it first), and a file such a job reads or writes
stays. Deleting never touches a project file, a LoRA or a model: only pictures, clips and sound in `renders`,
`exports`, `scouts`, `uploads` and `qa`, and clips in `projects` and `slate`, can be moved. The trash is
`~/ImaginationStudio/trash`; a LoRA removed in the hub goes there too, so *Empty trash* erases that as well.
`tests/test_clear.py` holds the path checks. Driven in a headless Chromium on both machines' builds, against a
studio folder of test clips: each Clear and Undo, one job deleted and put back, two ticked jobs, the failed ones,
all finished, restore, two ticked Library files, and the trash emptied (25 checks). Not driven in a browser: the
*Clear* above a Slate shot's prompt (it is the same control as a segment's).

#### Wasserman Sharp Spec

A **Wasserman Sharp Spec** button sits in Create (step 1), on the Production board and in the Slate film settings. One
click sets Sam Wasserman's locked recipe, so every clip is made the same way: 1376x768 generated in one pass with no
upscale and no crop, FastH3 at 8 passes (sparse attention 0.8), seed 42, and 8 or 10 seconds (192 or 243 frames at
24 fps). The file is H.264 High yuv420p with 32 kHz stereo AAC, as for every clip here. A retake is the next seed. The
button shows a tick while every setting still matches; changing one of them turns it off.

Each clip made to the spec then goes through his **QA gate** (`app/qa.py`), about a quarter of a minute:

- **Speech.** The clip's sound is transcribed (faster-whisper if it is installed, else the `whisper` command) and has
  to hold the line the prompt asked for. A prompt with no spoken line passes.
- **Identity.** Three frames (1 s, the middle, 1 s before the end) go side by side on one contact sheet, and a vision
  model checks that face, hair and wardrobe are the same in all three with no smeared faces. The model is the film's
  brain, or Claude Code, Codex or a local vision model, under your own sign-in: the sheet is sent to that model.
- **Retake.** A clip that fails either gate is rendered once more at the next seed; the failed take and every contact
  sheet are kept in `~/ImaginationStudio/qa/`. The job card shows what each gate found, and the sharpness figure of
  the three frames as a diagnosis.

A gate whose tool is missing on the machine is shown as skipped and does not fail the clip.

#### Keys and Wasserman LoRA Hub

A library of LoRAs for MiniMax H3 that a clip can be rendered with: Sam Wasserman's 80s Horror LoRA and, from 2.6.3,
twenty-one community LoRAs in seven groups (six by kind, and Fun). It is in the video settings of Create (step 1), on the Production board and in
the Slate film settings. Each LoRA is its author's own work under its own licence; the page on its card is the
author's, for credit.

- **Browse.** "▸ Browse the hub" opens the library: a tab for each group, a search box, "Installed only", and a card
  for each LoRA with what it does, its author, what it works from, its licence, its size and its trigger word.
- **Install.** After the essential bundle (below) every usable LoRA is already installed. Otherwise "⤓ Install" on a
  card, or "Install all in this category", puts the file in `~/ImaginationStudio/loras/`, checked against its sha256.
- **Use.** "Use on this clip" adds the LoRA to the clip being made. Each picked LoRA has a strength (its author's range
  is in the tooltip; a slider LoRA also takes minus values) and, when its recipe has them, a look. The trigger word and
  look phrase are put at the start of the shot for you. Up to four can be stacked.
- **Add.** "+ Add your own LoRA" takes a recipe: a name, the address of the `.safetensors` file or an uploaded file,
  the trigger word, the strength, and optional looks. Presets are in `app/lora-presets.json`.

**Wasserman**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Wasserman-80's Horror LORA | Sam Wasserman (wassermanproductions.com) | The look of a 1970s-80s horror movie: VHS or 16mm film. | text or a first frame | H3 Community | not tested |

**01 Camera & Viewpoint**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Camera Motion | Jojocodex | Camera moves the base model tends to miss: push-in, pull-back, orbit, tracking, aerial, crane, pan, macro. | text or a first frame | Apache-2.0 | Mac (84 s), DGX Spark (150 s) |
| Head-mounted FPV | neph1 | A first-person view from a camera on the head, with the bob of walking or running. | text or a first frame | H3 Community | Mac (84 s), DGX Spark (150 s) |
| Equirectangular 360° | shamanic | A full-sphere 360° panorama video with sound, for VR players. | text or a first frame | H3 Community | Mac (108 s), DGX Spark (180 s) |
| 360° Orbit | pablodawson | One photo becomes a frozen-moment 360° orbit that ends on the frame it started from. | first and last frame | H3 Community | Mac (140 s), DGX Spark (230 s), 768x768 |

**02 Transformation & Morphing**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Transformation | Ashmotv | Morphs one picture into another, element by element. | first and last frame | H3 Community | Mac (108 s), DGX Spark (158 s on FastH3) |
| Turnaround | matlod | A five-view character sheet (front to back) from one reference picture. | the Library's *Turnaround* action | H3 Community | Mac (45 s), DGX Spark (45 to 80 s) at 512 |
| Keyframe Animation | alvdansen | Hand-drawn animation on held frames: the next key drawing, an inbetween, or a short held sequence from one or two drawings. | reference pictures | Custom, gated | Mac (260 s), DGX Spark (315 s), 20 passes |

**03 Character Editing**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Character Swap | Akatz Labs (akatz-ai) | Replaces a person in a source clip with the person in a reference picture, keeping the scene. | a source video | H3 Community | Mac (285 s), DGX Spark (215 s) |
| Person Remover | Akatz Labs (akatz-ai) | Removes a chosen person from a video and rebuilds the background. | the Library's *Remove a person* action | H3 Community | DGX Spark (about 11 min for 5 s) |
| Natural Face Speech | vpakarinen | More natural face muscles and clearer English speech for a person talking to the camera. | text or a first frame | Apache-2.0 | Mac (84 s), DGX Spark (150 s) |

**04 Motion & Action**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Motion Continuity Repair | JOKER141 | Keeps fast movement connected: fewer broken transitions, less unwanted slow motion, closer to the prompt. | text or a first frame | None stated | Mac (84 s), DGX Spark (150 s) |
| Combat Base V2 | JOKER141 | Faster, harder-hitting fight movement, and livelier body movement and sound in dialogue scenes. | text or a first frame | None stated | Mac (84 s), DGX Spark (144 s) |
| Weapon Combat | JOKER141 | Sword, staff and other weapon fights that hold together better. | text or a first frame | None stated | Mac (90 s), DGX Spark (150 s) |
| Spatial Physics | Jojocodex | Objects that collide, stack, fall and block each other more believably; also firmer contact in fights. | text or a first frame | Apache-2.0 | Mac (90 s), DGX Spark (150 s) |

**05 Art Style**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Anime Slider | adf99 | A slider between flat 2D anime (plus) and photorealism (minus). | text or a first frame | None stated | Mac (90 s), DGX Spark (150 s) |
| Lineart Anime | DiffSynth-Studio | Colours a line-art video into finished anime. | a source video | Apache-2.0 | Mac (280 s), DGX Spark (205 s) |
| 3D Animation Style | SOLRICKS | The look of a 3D animated feature. Start the prompt with `3dsrx`. | text or a first frame | H3 Community, gated | DGX Spark (Mac: rewritten for the engine, no clip rendered yet) |

**06 Support & Finishing**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Natural Face Speech | vpakarinen | More natural face muscles and clearer English speech for a person talking to the camera. | text or a first frame | Apache-2.0 | Mac (84 s), DGX Spark (150 s) |
| Motion Continuity Repair | JOKER141 | Keeps fast movement connected: fewer broken transitions, less unwanted slow motion, closer to the prompt. | text or a first frame | None stated | Mac (84 s), DGX Spark (150 s) |
| Video Upscaler LoRA | dntpi | Doubles the size of a finished clip: a latent upscale, then one guided re-draw in tiles. | the Library's *Upscale* action | MIT code, weights other | DGX Spark (5 min for 5 s to 2 MP) |

**07 Fun**

| LoRA | By | What it does | Works from | Licence as stated | Rendered with it on |
|---|---|---|---|---|---|
| Person Remover | Akatz Labs (akatz-ai) | Removes a chosen person from a video and rebuilds the background. | the Library's *Remove a person* action | H3 Community | DGX Spark (about 11 min for 5 s) |
| VH5 Tape (VHS) | KennethFal | A 1980s home-video look with a damage dial: light wear, visible tape damage, or heavy tracking errors. | text or a first frame | H3 Community | Mac (138 s), DGX Spark (150 s) |
| Wushu Action | Jojocodex | Kung fu and wushu moves with weight behind them: punches, kicks, spins, staff work. | text or a first frame | Apache-2.0 | Mac (84 s), DGX Spark (150 s) |
| Multiple Angles (image) | akhaliq | Shows the subject of a picture from another camera angle: 12 directions at 4 heights, each also as a close-up. Used in Create, *Edit a picture*. | a picture to edit | Apache-2.0 | Mac (10 s a picture), DGX Spark (15 s a picture) |

"Not usable yet" means the hub lists the LoRA and the bundle holds its file, but the LoRA needs something a clip does
not have in this version. Its card says which. Three LoRAs are not for a clip at all and have a job of their own,
which the Library runs as an action (see *Library actions*): Turnaround, Person Remover and Video Upscaler LoRA.
Multiple Angles is a LoRA for the image model: it is used in *Edit a picture*, not on a clip. 3D Animation Style is approved download by download by its author; its file is in kohya's own layout (`lora_unet_blocks_0_attn_qkv_proj`), which ComfyUI reads as it is and the Mac converter renames.

**What a picked LoRA asks for.** A LoRA that needs more than a prompt says so in its own row and again in the Render
step, each with a button to the step that takes it, and "Queue this clip" waits until nothing is missing: a last frame
for 360° Orbit and Transformation (see *A last frame: the clip ends on a picture*), a source video, a picture or 20
passes for the LoRAs made for a clip from references (below). **Camera Motion** has a *Camera move* choice with its
author's own phrases (push-in, pull-back, handheld follow, orbit, crane reveal, tilt, pan; the author's note on how
steady each is sits beside it), and **Head-mounted FPV** a *View* choice in the Studio's wording, since its author
gives none. A **slider LoRA** has a track centred on 0 with its two ends named (for Anime Slider: photographic at -5,
2D anime at +5), the number beside it, and *Off (0)*.

What was tested (2026-10-12, one seed each, 5 seconds at 864x480, FastH3 8 passes, text to video; 1120x480 for the 360°
LoRA): each usable LoRA was installed through the hub and rendered twice at the same seed, at 0.01 (the same engine
path with no visible effect) and at the strength in its recipe. "Change" is the mean absolute difference of the two
clips' frames on a 0 to 255 scale; the contact sheets were looked at.

| LoRA | Strength | Mac: time, change | DGX Spark: time, change |
|---|---|---|---|
| Camera Motion | 0.9 | 84 s, 52.1 | 150 s, 50.9 |
| Head-mounted FPV | 1 | 84 s, 62.6 | 150 s, 41.4 |
| Equirectangular 360° | 1 | 108 s, 63.8 | 180 s, 26.8 |
| Natural Face Speech | 0.7 | 84 s, 55.0 | 150 s, 29.9 |
| Motion Continuity Repair | 0.9 | 84 s, 41.0 | 150 s, 22.4 |
| Combat Base V2 | 1 | 84 s, 57.8 | 144 s, 46.6 |
| Weapon Combat | 0.6 | 90 s, 30.3 | 150 s, 14.6 |
| Spatial Physics | 0.4 | 90 s, 41.6 | 150 s, 25.8 |
| Anime Slider | 3 | 90 s, 69.3 | 150 s, 67.5 |
| VH5 Tape (VHS) | 1 | 138 s, 56.8 | 150 s, 45.4 |
| Wushu Action | 0.5 | 84 s, 34.9 | 150 s, 33.6 |

Seen on the sheets: the Anime Slider turns a 3D-leaning picture into flat 2D anime; Equirectangular 360° gives a
level panorama where the base model drew a "little planet"; Head-mounted FPV widens the view and puts it at head
height; Camera Motion pulls back from a close-up to a wide shot where the base clip only followed the fox; Combat Base
V2 gives larger, faster strikes; Natural Face Speech moves in on the speaker at the microphone; VH5 Tape turns a clean
studio shot into a worn 4:3 tape recording. The numbers say the clip changed, not that it improved: judge the others by eye for your own prompt.

How it renders: on a Mac the file is rewritten once (`scripts/convert-lora.py`) and merged at load by the MLX engine,
for MiniMax H3 at any quality and for FastH3 (which then runs on the MLX engine, not the native one). On a DGX Spark
(2.7.1) TensorFold's ComfyUI node merges it into FastH3's full-precision projections the way ComfyUI would (its LoRA
arithmetic, then its seeded rounding to bfloat16) and the engine quantizes them to int8, so the clip renders on the
engine at its usual speed and memory: 864x480, 124 frames took 114 s against 140 s on ComfyUI's own blocks, plus
12.5 s for the merge the first time a LoRA is used (a later clip without it puts the checkpoint's weights back the
same way). Same seed, The Wasserman 80s Horror at 1.0: the engine's clip and ComfyUI's bfloat16 one show the same shot
(pixel correlation 0.80, the sound 0.89), closer than the two engines are without a LoRA (0.55), and both far from
the clip without the LoRA (0.43 to 0.49). The LoRA's keys outside the blocks (the text refiner, the modulation, the
final layer) are merged by ComfyUI as before. A LoRA still has no time estimate.

**LoRAs on a clip from references.** The hub is also offered on a clip made from references (MiniMax H3 Ref2VA), and
the LoRA is merged into that transformer: by the MLX engine on a Mac, and on a DGX Spark by ComfyUI into the
full-precision (bfloat16) Ref2VA file, with or without the Turbo adapter. A LoRA merged into the Spark's 8-bit file
would corrupt the clip without an error (its weights are stored rotated), so a reference clip with a LoRA never runs
on it; it starts from a ComfyUI server with nothing loaded. Three presets were made for one job on the reference
model, and the row of a picked one says what the clip needs, whether it has it, and writes the Review prompt the way
its author trained it (`app/lora_flows.py`, each from the LoRA's own model card):

| LoRA | The clip holds | Prompt | Passes |
|---|---|---|---|
| **Character Swap** (Akatz Labs) | your clip as `<Video 1>` (4 to 5 s held best for the author), a picture of the new character as `<Picture 1>` | `Replace only <who> in <Video 1> with the character in <Picture 1>. ...`, with who is replaced taken from "Who or what it shows" of Video 1 | Turbo 4 or 20 |
| **Lineart Anime** (DiffSynth-Studio) | one video, line art (white lines on black) at the clip's own size, and no picture | Scene and Action as plain sentences, sent as written | Turbo 4 or 20 (the author: 20) |
| **Keyframe Animation** (Alvdansen Labs) | one drawing (the current key) or two (first drawing and end extreme) as pictures | `Create the next keyframe in the sequence. SCENE: ... MOVEMENT: ... STATIC: ...` | 20 (the author: not on a Turbo adapter) |
| **Anime Slider** (adf99) | anything: text, a first frame or references | yours | any |

*Make line art of Video 1* (in Lineart Anime's row) draws the line art of any clip with ffmpeg alone (`app/lineart.py`:
an edge-preserving blur, then each pixel's difference to its brightest neighbour; fine, medium or outlines only). No
model is involved. The author publishes no extractor; this one was matched by eye to the author's example clip.

**Layouts.** Most H3 LoRAs are saved in ComfyUI's layout. DiffSynth-Studio's are raw PEFT saves
(`blocks.N....lora_A.default.weight`, no prefix), which ComfyUI loads without matching a single key and without a
warning. `app/lora_layout.py` tells the layouts apart and rewrites such a file at install, on a Mac through
`scripts/convert-lora.py` and on a DGX Spark as `<id>.comfy.safetensors`. One kind of pair needs more than a new name
on a Spark. ComfyUI's `pruned` H3 checkpoints drop the time embedder: each adaln projection reads 8 numbers a
timestep from a table where MiniMax's own weights read 2,688. A LoRA pair made on the full adaln weights is carried
over with the affine map between the two (solved from the table and MiniMax's time embedder; fit error 1.4e-5 on the
real weights, and the update of a real pair agrees to 2.9e-5), `A' = A @ basis.T` with the rest in a bias term.
The basis is worked out once, from the model on the machine, a LoRA bundle that holds `ref2va-adaln-basis.npy`, or
the first 64 MB of MiniMax's own Ref2VA file (a ranged read of the public page; `LORA_OFFLINE=1` forbids it).

**Measured** (5 s at 864x480, seed 7, the same request without and with the LoRA; a generated clip of a man at a
wooden dummy as the source, a generated picture of a woman as the new character, two line drawings made from the
clip as the keys; one seed each, looked at on contact sheets):

| LoRA | Passes | Mac M5 Ultra: without / with | By eye | DGX Spark: without / with | By eye |
|---|---|---|---|---|---|
| Character Swap, 1.0 | Turbo 4 | 281 s / 287 s | the swap holds either way; with the LoRA her pose sits a little closer to the source's | 210 s / 516 s | the same: both swap her in on the same rooftop, small differences in hands |
| Lineart Anime, 1.0 | Turbo 4 | 271 s / 278 s | both colour the line art as anime on the lines; with the LoRA flatter cel shading | 541 s / 331 s | the same |
| Lineart Anime, 1.0 | 20 | - / 706 s | on the lines, but darker and closer to 3D than at Turbo 4 | - / 605 s | anime on the lines, as clean as at Turbo 4 |
| Keyframe Animation (sequence), 1.0 | 20 | 258 s / 302 s | without: a photographed man, the drawings ignored; with: the line drawing itself moves, held on white | 315 s / 306 s | a moving line drawing on white both times (without the adapter the Spark ran its 8-bit file); small differences in line weight |
| Anime Slider, +3 / -3 | Turbo 4 | 91 s / 92 s / 92 s | +3 smoother and flatter, -3 more photographic; mild next to the Spark | 235 s / 110 s / 110 s | +3 flat 2D anime with a drawn background, -3 photographic |

The Spark's times include starting ComfyUI and loading the 40 GB bfloat16 Ref2VA file, and a render that follows
another kind of render loads it again; its lowest free memory in these runs was 33 GB of 121. On this clip the base
model already swaps a person and already colours line art from the prompt, so Character Swap and Lineart Anime change
the result less than Keyframe Animation and the slider do. Why the slider's +3 is milder on the Mac than on the
Spark was not found (same file, same strength; the Mac runs these passes on its int8 kernels).

**Through the apps (2026-10-13, 2.6.3 on both machines, one seed).** Each clip below was set up in the create wizard
of the running app in a browser (headless Chromium), queued with "Queue this clip" and rendered by the app's own
queue, one at a time; every result was looked at on a contact sheet. 5 seconds at 864x480 unless noted; FastH3 8
passes for the clips from text or frames, the Ref2VA Turbo adapter (4 passes) for the clips from references.

| Clip | Mac (M5 Ultra) | DGX Spark | What came out |
|---|---|---|---|
| First and last frame (a teapot, a bonsai) | 100 s | 130 s | ends on the bonsai picture |
| 360° Orbit, first = last, 768x768, the author's prompt | 140 s | 230 s | a full orbit of the statue, back to the first view |
| Transformation, the author's prompt | 100 s | 165 s | the teapot breaks into painted ribbons that form the tree |
| Character Swap (a clip and a picture of a woman) | 285 s | 215 s | she is at the dummy in his place, the rooftop and the move kept |
| Lineart Anime, after *Make line art of Video 1* | 280 s | 205 s | the clip redrawn as anime on its own outlines |
| Keyframe Animation, two drawings, 20 passes | 260 s | 315 s | a moving line drawing |
| Anime Slider on a clip from text: -3 / 0 / +3 | 85 / 70 / 90 s | 150 / 125 / 150 s | photographic / the base look / flat 2D anime |
| A last frame made from a prompt (step 2) | 12 s | 23 s | the picture lands in the Last frame slot |

Lowest free memory on the DGX Spark across its ten jobs: 29 GB of 121.

**Known difference: the slider on a Mac clip from references.** On a clip from text the slider pulls the same on both
machines. On a clip made *from references* (Ref2VA), the same file at +3 gives full 2D anime on the DGX Spark and only
a painterly lean on the Mac; the Mac at +6 looks like the Spark at +3. It is not the int8 kernels (`INT8=0`, the same
weak pull), not the Turbo adapter (20 passes without it, the same) and not the file's scale (the text path agrees).
The cause is not found: on a Mac, use about twice the strength for a slider on a clip from references.

#### Essential LoRA bundle

```bash
bash oneshot-essential-bundle.sh          # both installers run it; --no-loras on an installer skips it
```

It fetches every preset of the hub once: fourteen in one essential package per platform (2.1 GB, from
`huggingface.co/drowzeys/keys-studio-lora-essential`, the LoRAs whose stated licences allow passing them on, each with its
author's card and licence; `LORA_PACKAGE=none` skips it) and the rest each from its author's own page (about 6.5 GB in
all and 4 minutes measured on a fast line;
the three Keyframe Animation adapters, 5.6 GB of it, need your Hugging Face account's acceptance of their licence),
into `~/ImaginationStudio/lora-bundle/`, laid out by the hub's groups (`01-camera-viewpoint` ... `06-support-finishing`, `07-fun`,
with `bundle.json` as the menu), checks each sha256, keeps the author's model card and licence beside each file, and
installs the set into the hub (on a Mac, rewritten for the MLX engine). After that the Studio needs none of those
links: copy the folder to another machine or drive and run `bash oneshot-essential-bundle.sh --source /path/to/lora-bundle`
(or an installer with `--lora-bundle=/path`) for an install with no network. A second run fetches nothing. A dead or
moved author link is reported and skipped, not fatal. `--categories camera,style`, `--only ID,ID`, `--dest DIR`,
`--dry-run`, `--verify`, `--json`, `--no-install`. `--package FILE` unpacks an essential package (one `.tar` per
platform with the LoRAs whose licences allow passing them on, checked against the `.sha256` beside it) before anything
else; `--make-package DIR` writes those archives from a bundle. No LoRA file is in this repository.

**For AI agents** (no prompts anywhere; run in this order on a new machine):

```bash
git clone https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3.git && cd keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3
bash oneshot-setup.sh --no-render --no-loras        # the Studio (oneshot-setup-spark.sh on a DGX Spark); model weights as listed under One-shot
bash oneshot-essential-bundle.sh --json > lora-bundle.json; echo "exit $?"   # 0 all present, 1 some unavailable (listed), 2 usage or disk
bash oneshot-essential-bundle.sh --verify --json    # fetches nothing: "present", "unavailable", "gated", and "menu" (group -> LoRAs -> state)
```

Expect 4.8 GB and 20 of 22 present without a Hugging Face token (`gated` lists the two an author gates), in 90 to 120 s
at 40 MB/s; measured 119 s on the DGX Spark and 86 s on the Mac, a second run 2 s, an offline run from a copied bundle
3 s. Set `HF_TOKEN` (or sign in with `hf auth login`) for the gated ones once their authors have approved the account.

#### Wasserman Slate AI Studio

At the top of **Production** there are two ways to plan a film: the board of segments above, or **Wasserman Slate AI
Studio**. The second is a port of the workflow of Slate, the prompt studio for AI filmmaking by Sam Wasserman
(wassermanproductions.com), wired to this Studio's render queue, so a shot goes from its prompt to a clip without
copying anything anywhere.

| Part | What it does |
|---|---|
| **First AD** | Say what you are making. It creates scenes and shots, writes their prompts, casts characters, scouts locations, sets spoken lines and sound, and lists what it did. It changes or replaces a character, place, prop, cue or voice that is already there (the sheet keeps its id, so every shot that casts it follows), sets the song, a shot's slot in bars and a shot's LoRA. |
| **Bible** | Logline, world and tone, defaults, the song (tempo, beats in a bar, first beat), how the film renders (resolution tier, quality), and which brain to use. |
| **Studios** | Casting, Art Department, Locations, Lookbook: sheets filled from one line by ✦ Fill. A still pinned to a character is its reference picture. |
| **Shots** | Specs (seconds, aspect, size, angle, lens, movement) and the six-section prompt; ✦ Structure, Tighten, Enrich, Distill, Shot, Angle, Beats, Variants; Director's notes; Slate's setups; picture-lock and mute; history. ✦ Coverage lays out a scene from one of Slate's twelve coverage plans. |
| **Roll camera** | What the shot opens on (text, the last frame of the shot before, an image, the cast's stills), who is in it and where, spoken lines, soundscape, cue. The shot compiles to MiniMax H3's prompt form with a preflight list; one button queues it. Clips come back as takes: circle the one that works. A LoRA from the hub can be put on one shot in place of the film's. *Import a take rendered on another machine* logs a clip another app made into the shot, with its seed and settings. |
| **Sound** | Voice sheets (the voice of a spoken line) and music cues (the score line of a shot). **Cast voice** speaks a sheet's sample line with Fish Audio Drama 3 in the voice it describes; ticked, the take goes into shots that open on cast stills as that speaker's reference audio. |
| **Deliver** | Render a whole scene in order, a continuity check, the shot list as Markdown or CSV, and the film's takes to the Editor's timeline. |

The brain is Slate's: your own Claude Code or Codex sign-in on the machine that runs the app, or a local
OpenAI-compatible server (Ollama, LM Studio, vLLM, llama.cpp). Everything without a ✦ works with no brain. A new film
starts from the brain chosen in Brains & tools; an install with nothing chosen there yet takes `STUDIO_DEFAULT_BRAIN`
(`local`, `claude`, `codex`, ...), `STUDIO_LOCAL_ENDPOINT` and `STUDIO_LOCAL_MODEL` from its environment. When a film's
brain is the local model, its frames are only ever shown to that model: the QA look is skipped rather than sent to a
cloud CLI when the local server is not set. Films are
Slate's own `project.json` under `~/ImaginationStudio/slate/`: a project made in Slate opens here, and one made here
downloads from the Bible and opens in Slate (what this port adds sits under `tf` keys). Shots render at any length
the model takes (their seconds, or their slot in bars) and the aspect ratios 16:9, 9:16, 1:1, 4:3, 3:4 and 21:9.

**What a shot sends to the model.** A shot's *intent* is a note for people and stays out of the prompt (it used to
stand in as the action when a shot had no beat sheet, which put a run's timecodes into its prompts): what happens is
the Subject section's, or the beat sheet's.

**A music video.** Give the song's tempo in the Bible (the Studio does not listen to the song: the tempo and the
first beat are yours, from the score or a beat tracker) and each shot a slot in bars. The shots are laid end to end
from bar 1, and a shot's clip is rendered at the shortest length the model takes that covers its slot, so the cut
can sit on the bar line: at 117.45 BPM a 2-bar slot is 98 frames and renders as 107, a 4-bar slot is 196 and renders
as 209 (with the four fixed lengths it needed a 10 s clip, 243). Roll camera shows the slot, where it starts in the
song and how many frames there are to trim. The cut itself is still made in the Editor or outside: nothing here lays
the song under the timeline.

**A LoRA on one shot.** *LoRA for this shot* in Roll camera puts one installed LoRA on that shot in place of the
film's. It goes through the hub (strength, look, what it needs) and, on a DGX Spark, through the memory guard when
the shot is queued; the shot's preflight says beforehand when it will be refused and what fits. On a Spark a clip
with a LoRA is taken up to 1376x768 at 243 frames (2.7.1; before that 1280x736 at 124, and the Sharp Spec tier took
none).

**Takes from other machines.** A film's project is on one machine and its clips are often rendered by others, each
by its own app. *Import a take* takes that app's address and one of its job ids (the clip, prompt, seed, settings and
what its checks said are read from the job), or a path in its folder, or an uploaded file, and logs the take in the
shot. `POST /api/slate/takes {"projectId", "shot", "from", "job"}` does the same for a script (`"log": true` writes
it into the project file). An app forgets its jobs when it restarts; the clip's path still works.

Not ported from Slate: sequence chunks, alt takes, punch-ups, the tone dial, pickups on a selected span, reference
and audio analysis, the stills library, compiles for other generators, and its MCP server. The app has no sign-in of
its own: anyone who can open it can use the brain, so keep it on a network you trust.

The app drives the same scripts as the command line, one job at a time on each machine, and keeps projects, uploads
and renders under `~/ImaginationStudio`. What it is not: the editor has one video track with trims and ordering only
(no transforms, text or transitions; its only audio step is a song laid under the export), and outside Slate's ✦
buttons there is no assisted prompt writing. The workflow is modelled
on rookiestar28's ComfyUI-MiniMaxH3-Studio, which does far more; no code is shared with it.

## Voice (Fish Audio Drama 3)

**Built with Fish Audio.** The **Voice** tab turns text into speech with Fish Audio Drama 3, run on this machine by
TensorFold's ports of it: on a Mac the MLX port (the 35B Qwen3.5 MoE backbone, its codebook tower and the Fish codec,
in MLX), on a DGX Spark the native CUDA port ([below](#voice-on-a-dgx-spark-cuda)). No Fish Audio cloud API is called
and the pack ships no Drama 3 weights.

- **Write what is said.** Text without a tag is speaker 0; `<|speaker:0|>` to `<|speaker:15|>` switch voices.
  Directions in brackets steer the delivery: `[whispering]`, `[sighs]`, `[laughs]`, or a description of the voice
  (`[a deep, gravelly man in his late fifties, slow and patient]`), which designs a voice without a recording and is
  not read out (measured: that one at 76 Hz, "a bright young woman" at 228 Hz, both transcribed without the bracket).
  Long text is split at sentence ends and keeps one voice.
- **Clone a voice (optional).** A clean clip of one speaker, about 10 to 30 seconds (2 to 60 are taken), and what it
  says; left empty, Whisper writes the transcript (on a Mac the engine's own, already loaded; on a Spark the app's). The consent box (*I have the speaker's consent to clone this
  voice*) must be ticked, and the API refuses a clip without `"consent": true`. Measured with a 3.9 s clip at 125 Hz:
  the takes came out at 130 and 133 Hz with every word right.
- **Settings.** Seed, and Fish's sampling defaults: temperature 1.0, top-p 0.9, top-k 30.
- **Each take** is a job in the queue, so it never runs beside a video render, and it holds the studio's GPU lock
  folder (`~/studio-gpu.lock.d`) while the model runs, waiting if a script outside the app holds it.
- **A turn a line.** Text with two or more speaker tags is sent with each turn after the first on a line of its own,
  on both machines (`voice.dialogue`): written on one line, Drama 3 dropped later turns far more often (TensorFold's
  measurement: 12 of 48 streamed dialogue takes short on one line, 1 of 48 a turn a line).
- **The word check.** On a Mac the engine checks itself (TensorFold `drama3-mlx` 4a8a982f or later): every part it
  speaks is heard by Whisper large-v3-turbo on MLX and aligned to the text; words after the end, or a long tail of
  sound, are cut; a part with words missing is drawn again with a new seed (twice at most, the best kept); and a
  take that passed a likely end and went on is stopped on the model's own end signal. The tab shows the check part by
  part (ok, trimmed, redrawn, words not heard) and its count as the take's score, with what was heard on hover. On a
  Spark the app runs the same check (`app/wordcheck.py`, the engine's rules) on what Whisper (the QA gate's own,
  `app/qa.py`, on the CPU) hears in the finished take, and makes a take with missing words again with another seed,
  not played, twice at most, keeping the best (2.7.8). With an older Mac engine Whisper only scores the take.
- **Plays while it is made**, on both machines: the tab plays the take from `/api/voice/takes/{id}/live` as the
  engine writes it. On a Mac a heard part cannot be drawn again, so a take whose check finds a part short while it
  plays is made once more without playing, and the take says so; untick *Play while it is made* to skip the played
  take (`"live": false` in the API).
- **A take as a MiniMax H3 voice reference.** *Use as Ref2VA voice reference* opens the create wizard on reference
  to video with the take as the next `<Audio N>` (2 to 15 s a clip, 15 s in all: a longer take is cut to its first 15
  seconds, asked first). Add a picture or a video of who speaks, and describe the same voice in the line's Voice box:
  what a reference audio clip does is in [Reference videos and reference audio](#reference-videos-and-reference-audio-ref2va).
  *Clone this voice* uses a take as the clone clip of the next, for the same speaker across lines.
- **Slate.** On the Sound tab a voice sheet has **Cast voice**: the sheet's sample line, with its description (age and
  gender, accent, timbre, pitch, pacing, energy, texture) as the direction. The take is kept on the sheet (`drama` in
  the project file); ticked, a shot that opens on cast stills sends it as the speaker's reference audio and names it
  in the prompt. A take over 15 s is cut on the sheet first; what does not fit MiniMax's limits is said in the
  preflight and left out.

| Measured, Mac Studio M5 Ultra (TensorFold `drama3-mlx` 92c09a8e, the persistent server, played while made), 2.7.7 | Audio | First sound on the page | Through the app, check included | Words heard |
|---|---|---|---|---|
| One speaker, 16 words, first take (loads the server, 5.2 s) | 8.0 s | 5.43 s (engine 0.21 s) | 8.2 s | 16 of 16 |
| Cloned voice from an MP3 clip (transcript left to Whisper on the CPU first) | 6.6 s | 10.7 s (engine 0.52 s) | 13.4 s | 16 of 16 |
| Two speakers, the tab's example, seeds 1, 2, 3 | 10.6, 9.4, 11.1 s | 0.30, 0.29, 0.23 s | 4.1, 3.6, 4.1 s | 20 of 20 each |
| Two speakers, seed 4: short while playing, made again without playing, redrawn once | 9.9 s | 0.28 s | 19.4 s | 20 of 20 |
| A 120-word read, three parts (two tails trimmed) | 46.4 s | 0.30 s | 16.8 s | 120 of 120 |
| Slate Cast voice | 3.2 s | 0.30 s | 1.5 s | 12 of 12 |
| One speaker, 9 words, loaded | 2.5 s | 0.24 s | 1.5 s | 9 of 9 |

First sound on the page is the first byte of `/api/voice/takes/{id}/live` after the take was asked for. Peak memory
48.5 to 49.9 GB (55.7 GB with a redraw); the server unloaded after 300 idle seconds and before a video job, and gave
the GPU lock back both times.

| Measured, Mac Studio M5 Ultra (TensorFold `drama3-mlx` 4a8a982f, 8-bit experts, played while made), 2.7.6 | Audio | Engine time | Through the app, check included | Words heard |
|---|---|---|---|---|
| One speaker, 16 words | 8.0 s | 2.7 s (71.9 frames/s, first sound 0.22 s; 5.3 s after the click) | 8.7 s | 16 of 16 |
| Cloned voice from an MP3 clip (3.9 s, transcript left to Whisper on the CPU first) | 6.6 s | 2.4 s | 18.4 s | 16 of 16 |
| Two speakers, three lines, seed 1: short while playing (7 of 20), made again and redrawn once | 12.5 s | 11.8 s the second run | 30.5 s | 20 of 20 |
| Two speakers, seed 2: short while playing, the last line still missing after three draws | 22.8 s | | 38.8 s | 16 of 20 |
| Slate Cast voice | 3.2 s | 1.3 s | 7.2 s | 12 of 12 |
| A 120-word read, three parts (two tails trimmed) | 46.4 s | 15.9 s (68.1 frames/s) | 21.9 s | 120 of 120 |

Peak memory 48.7 to 49.9 GB played while made, 55.7 GB with a redraw. Frames are drawn at about 70 a second while
playing (the codec decodes in windows) and 83 when not. The first sound comes 5.3 s after the click because each
take starts the engine's process and loads the model (3.7 s); the engine's own clock says 0.22 s.

| Measured, Mac Studio M5 Ultra (TensorFold `drama3-mlx` f623fb1a, 8-bit experts), 2.7.4 | Audio | Engine time | Through the app, word check included | Whisper |
|---|---|---|---|---|
| One speaker, 18 words | 8.3 s | 3.3 s (63.3 frames/s) | 16.7 s | 17 of 18 ("Phish") |
| Cloned voice, with `[sighs]` and `[laughs]` | 7.0 s | 2.8 s | 16.1 s | 12 of 12 |
| Cloned voice, transcript left to Whisper | 4.3 s | 1.8 s | 24.8 s | 15 of 15 |
| Two speakers, three lines | 11.3 s | 4.4 s | 17.7 s | 20 of 20 |
| Slate Cast voice (a sheet's description as direction) | 7.5 s | 3.0 s | 16.2 s | 15 of 15 |

Engine time is Drama 3's own (prefill, frames and codec); the process also loads the model (about 3 s) and the
word check loads Whisper on the CPU. Peak memory 48 to 51 GB.

**Mac requirements.** An Apple-silicon Mac with 64 GB of memory or more (the run peaks at about 52 GB; nothing else
of the studio runs beside it), about 40 GB of disk for the converted weights (and 39 GB for Fish's checkpoint while
converting), and TensorFold's Drama 3 port in an environment of its own. The port is on TensorFold's `drama3-mlx`
branch of [drowzeys/TensorFold](https://github.com/drowzeys/TensorFold). `bash oneshot-setup.sh --voice` (or
`bash scripts/setup-voice.sh` alone, `--verify` to check) does all of it: the clone at 8f3d4b33 in
`~/tensorfold-drama3-mlx`, its venv, Fish's checkpoint, the conversion and a test line. By hand:

```bash
git clone -b drama3-mlx https://github.com/drowzeys/TensorFold.git ~/tensorfold-drama3-mlx
python3.11 -m venv ~/drama3-venv && ~/drama3-venv/bin/pip install -e ~/tensorfold-drama3-mlx mlx mlx-lm mlx-whisper soundfile torch
hf download fishaudio/drama-3 --local-dir ~/models/drama-3        # Fish Audio Community License: read it first
~/drama3-venv/bin/python -m tensorfold.families.drama3.convert ~/models/drama-3 ~/models/drama-3-mlx   # once, about a minute
~/drama3-venv/bin/python -m tensorfold.drama3 --text "Hello there." --out hello.wav                  # check
```

From `drama3-mlx` 92c09a8e the app runs the engine as a persistent server (`python -m tensorfold.drama3 --serve`,
the same protocol as the Spark's), as described for the Spark [below](#voice-on-a-dgx-spark-cuda): loaded on the first
take, unloaded after five idle minutes (`DRAMA3_IDLE_S`) and before any other job, under the GPU lock and a memory
guard (vm_stat's free, inactive, speculative and purgeable pages; 8 GB, `DRAMA3_GUARD_GB`). An older engine runs once a
take. The app finds `~/drama3-venv` and `~/models/drama-3-mlx` itself (`DRAMA3_VENV` and `DRAMA3_MODEL` name others) and
says on the tab what is missing. `DRAMA3_BACKEND=mlx|cuda` picks the engine (`mlx` on a Mac, `cuda` on a DGX Spark by
default). The API: `GET /api/voice/state`, `POST /api/voice/takes {text, ref_audio?, ref_text?, consent?, seed?,
temperature?, top_p?, top_k?}` (a job of kind `voice`), `GET /api/voice/takes`, `GET /api/voice/takes/{id}/audio`,
`GET /api/voice/takes/{id}/live` (the take as it is made; `"live": false` in a take's settings: not played, Mac only), `POST /api/voice/sheet {sheet, project}` and
`POST /api/voice/cut {path, seconds}`. Takes are kept in `~/ImaginationStudio/voice/` (the WAV, its settings and
measurements, and the text).

### Voice on a DGX Spark (CUDA)

On a DGX Spark the tab runs **TensorFold's native CUDA port of Drama 3**: one binary, `drama3-generate` (Zig with
CUDA kernels built for the GB10's sm_121: the backbone, the codebook tower and the Fish codec, no Python or PyTorch at
run time), with `libtensorfold_drama3.so` beside it.

- **A persistent server.** The first take starts `drama3-generate --serve 1` (one JSON request a line on its stdin,
  one JSON answer a line on its stderr) and waits for it to load: about 18 s from the checkpoint, 38.6 GB of weights
  on the device, 41.6 GB at the peak. Later takes go straight to it.
- **It gives the GPU back.** While loaded it holds `~/studio-gpu.lock.d` (a script outside the app that takes the
  lock waits for it). It is unloaded, and its memory comes back, after **five minutes without a take**
  (`DRAMA3_IDLE_S`), and **at once when any other job of this machine's queue starts** (a video, a picture, a long
  video): Drama 3 and MiniMax H3 never stand side by side. Before it loads, the studio's ComfyUI server is stopped
  if it holds the memory Drama 3 needs (about 50 GB free is wanted); the next video loads its models again.
- **Its own memory guard**: available memory is read four times a second and the engine is killed by its PID under
  8 GB (`DRAMA3_GUARD_GB`); the take fails with the reason and the lock goes back. The engine also ends with the app.
- **Plays while it is made.** The engine writes the take's samples as it makes them and the tab plays them behind an
  open WAV header (`/api/voice/takes/{id}/live`): the first sound about 0.3 s after a take starts on a loaded engine
  (18.5 s on the first take, which loads it), and the take is made faster than it plays.
- **Long text** goes in parts of about 60 words cut at sentence ends (each part opens on the speaker who was
  speaking), joined with 0.15 s between them. A one-speaker take without a clip clones its first part's voice for the
  others (measured on a 56 s read: 271, 272 and 269 Hz in its three thirds); a clip given is used for every part.
- **A voice to clone** may be any sound file: it is converted to 44.1 kHz mono WAV with ffmpeg first (the engine
  reads WAV).

| Measured, DGX Spark GB10 (TensorFold `drama3-cuda` 81173e6c), through the app | Audio | Engine time | Through the app, word check included | Whisper |
|---|---|---|---|---|
| One speaker, 16 words, first take (loads the engine, 18.3 s) | 8.2 s | 5.9 s (30.9 frames/s) | 37.7 s | 16 of 16, 100 % |
| Cloned voice from an MP3 clip (3.9 s, transcript left to Whisper), with `[sighs]` | 6.1 s | 5.0 s (28.0 frames/s, first sound 0.47 s) | 29.7 s | 16 of 16, 100 % |
| Two speakers, three lines, `[whispering]` and `[sighs]` | 11.6 s | 8.4 s (30.2 frames/s) | 21.1 s | 20 of 20, 100 % |
| Slate Cast voice (a sheet's description as direction) | 4.1 s | 3.3 s (28.3 frames/s) | 14.2 s | 11 of 12, 92 % ("harbor") |
| A 120-word read, three parts | 55.7 s | 40.8 s (30.7 frames/s) | 56.9 s | 118 of 120, 98 % |

The cloned take came out at about 130 Hz against the clip's 125 Hz. Engine time is the requests' own (prefill,
frames, codec); the app's time adds Whisper on the CPU (and, for a clip without its words, Whisper on the clip first).

**Spark requirements.** A DGX Spark (GB10, 128 GB), the CUDA toolkit 12.9 or newer and zig 0.17 to build, 39 GB
of disk for Fish's checkpoint and 1.6 GB for the converted codec, and about 50 GB of free memory when a take starts
(nothing else of the studio runs beside it). The port is on the `drama3-cuda` branch of
[drowzeys/TensorFold](https://github.com/drowzeys/TensorFold) (81173e6c). The setup is by hand: the branch's build
script carries its author's paths and its zig build embeds the kernel fatbins of TensorFold's whole CUDA engine
(prebuilt, or every one built by nvcc), so there is no installer step yet; `bash oneshot-setup-spark.sh --voice` (or
`scripts/setup-voice.sh`) checks what the app looks for and prints these steps for what is missing.

```bash
git clone -b drama3-cuda https://github.com/drowzeys/TensorFold.git ~/tf-drama3 && git -C ~/tf-drama3 checkout 81173e6c
hf download fishaudio/drama-3 --local-dir ~/models/drama-3          # Fish Audio Community License: read it first
# build as the branch's tools/drama3/build_local.sh does (it has its author's paths): nvcc makes drama3.fatbin,
# drama3_fp8.fatbin and drama3_codec.fatbin for sm_121 beside the CUDA engine's other fatbins, then
#   zig build -Dfatbins=FATBIN_DIR -Dsm=121 -Doptimize=ReleaseFast --prefix OUT drama3-generate drama3-lib
mkdir -p ~/.local/opt/tensorfold-studio/drama3/{bin,lib}
cp OUT/bin/drama3-generate ~/.local/opt/tensorfold-studio/drama3/bin/
cp OUT/lib/libtensorfold_drama3.so ~/.local/opt/tensorfold-studio/drama3/lib/
# once, offline: Fish's codec.pth into the native codec's layout (PyTorch and Fish's reference code, ORACLE_DIR)
python3 ~/tf-drama3/tools/drama3/codec_convert.py ~/models/drama-3 ~/.local/opt/tensorfold-studio/drama3/codec-v1.safetensors
~/.local/opt/tensorfold-studio/drama3/bin/drama3-generate --model ~/models/drama-3 \
  --codec ~/.local/opt/tensorfold-studio/drama3/codec-v1.safetensors --text "Hello there." --wav hello.wav   # check
```

The app looks in `$PREFIX/drama3` (`~/.local/opt/tensorfold-studio/drama3`) and `~/models/drama-3`;
`DRAMA3_CUDA_BIN`, `DRAMA3_CUDA_LIB`, `DRAMA3_CUDA_MODEL` and `DRAMA3_CUDA_CODEC` name others, and the tab says what is
missing. The worker Sparks of the Sparks setting do not make voice takes: a take runs on the Spark whose app you use.

**Licence.** Drama 3's weights and codec are Fish Audio Materials under the **Fish Audio Community License v1.0**:
research, non-commercial and small-company commercial use; a hosted service that lets others generate speech or
clone voices needs a separate licence from Fish Audio; show "Built with Fish Audio" where it is used; never clone a
real person's voice without their consent, impersonate anyone, or train other speech models on the outputs.

## Music (MiniMax Music 3)

**MiniMax-Music3.** The **Music** tab makes a song from lyrics and a style with MiniMax Music 3, on this machine: sung
vocals, up to five minutes (the app offers 10 to 300 s; the model takes up to 360), 44.1 kHz stereo WAV. No cloud is
called and the pack ships no weights.

- **Lyrics.** Section tags on lines of their own, above the lines they hold: `[Intro]`, `[Verse]`, `[Pre-Chorus]`,
  `[Chorus]`, `[Post-Chorus]`, `[Bridge]`, `[Hook]`, `[Instrumental]`, `[Solo]`, `[Break]`, `[Build]`, `[Outro]`.
  `[Instrumental]` is a passage without singing; lines in (parentheses) are backing vocals. Empty lyrics make an
  instrumental. The tab warns about lyrics without tags and tags it does not know (the model may sing them).
- **Style.** One caption with the genre, tempo, key, mood, the voice that sings and the instruments, e.g. "Upbeat
  modern pop, 118 BPM, bright synths, punchy four-on-the-floor kick ... Warm female lead vocal with layered
  harmonies." The *Style helper* writes one from six boxes; you can still edit it.
- **Length** is the longest the song may be: the model may end it sooner (measured: asked for 120 s, the short
  hip-hop lyrics ended at 55.6 s on the Spark and 60.9 s on the Mac). The take says when the model ended it.
- **Settings** are MiniMax's: 30 DiT steps at CFG 1.7, the autoregressive model at CFG 1.5 and top-k 50
  (`steps`, `dit_cfg`, `ar_cfg`, `top_k` in the API).
- **Each song** is a job in the queue, so it never runs beside a video render or a voice take. The model stays loaded
  for five minutes after a song (`MUSIC3_IDLE_S`) and is unloaded at once when any other job starts; Drama 3 is
  unloaded before a song in the same way. While loaded it holds the studio's GPU lock (`~/studio-gpu.lock.d`).
- **The lyrics check.** With faster-whisper installed, Whisper listens to the take and counts the lyrics' words it
  heard, in order, up to the last one it heard (`asr.score`), and how far into the lyrics the song got
  (`asr.reached`). Sung words are harder to hear than spoken ones: treat it as a rough count.
- **Play while it's made** (a Mac on the MLX port). With the box ticked (the default when the engine streams), the song plays in the
  tab while the engine makes it. The engine's stream appends raw 16-bit stereo PCM to a file as it decodes each piece
  (`pcm` in the request, as Drama 3's server does), and `GET /api/music/takes/{id}/live` serves it as one WAV, two
  channels at 44.1 kHz, its length left open. A stream is slightly slower than real time and gets slower as the song
  grows (60 s hip-hop: a 4 s piece every 4.2 s at the start, every 4.7 s near the end). So the app holds the sound
  back until playing will not run dry. It waits for the engine's measured no-stall start (10.3 s from the request for
  60 s, 52.7 s for 300 s, a straight line between and beyond) plus 3 s. If the engine is running slower than that
  measurement, it waits for a larger lead as well. The kept take is the engine's finished WAV, the same samples that
  were played (checked byte for byte on two songs), and the lyrics check and *Use as soundtrack* work on it as before.
  Untick the box to hear the song only when it is done.
  Measured through the app on the Mac Studio M5 Ultra (int8, the hip-hop prompt; first sound is the engine's
  `first_audio_ms`, the start is when the live route let the sound go, both counted from the request; the least
  buffer is the smallest amount of song a player started at that moment had in hand, from the bytes as they
  arrived):

  | Song | First sound | Playing from | Least buffer | Made in |
  |---|---|---|---|---|
  | 60 s (seed 7, engine loaded for it, 1.0 s) | 7.6 s | 13.4 s | 4.0 s, at 49 s | 63.4 s |
  | 60 s, ended by the model at 57.9 s (seed 11) | 6.4 s | 13.4 s | 4.0 s, at 45 s | 61.0 s |
  | 30 s (seed 3) | 6.4 s | 8.1 s | 1.6 s, at 19 s | 30.0 s |

  Before that margin was added, a start at the first piece (10.7 s) ran dry for 0.18 s at 53 s of a 60 s song, which
  is why the margin is there. Limits: it works on a Mac only, because neither route on a DGX Spark streams (the CUDA
  engine and ComfyUI's nodes alike) and the box is not shown there. Only 30 s and 60 s songs were measured through the app; the 300 s figure is the
  engine's own (no-stall 52.7 s). Starting about 13 s in is the price of a stream that runs slower than real time,
  and the wait grows with the length (about 56 s for 300 s). The engine found no seams by measure, but nobody has
  listened for them. A browser's own buffering comes on top.
- **Use as soundtrack.** Under a song, *Use as soundtrack* lays it under a video: a long video (its card in Create has
  *Lay a song under it*), the Editor's export or any clip of the Library. In the Editor, *Song under the export* does
  it for the whole timeline. ffmpeg mixes the song at -10 dB (your choice, -30 to 0) under the clip's own sound; a
  compressor keyed by the clip's speech band (200 Hz to 3.5 kHz) lowers it by up to 12 dB while someone speaks
  (measured in `tests/test_music.py`: 6 to 13.5 dB on a test tone); a limiter at four times the rate keeps every peak
  under -1 dBFS, checked after the AAC encode (under the 48.6 s promo: -1.46 dBFS on the Mac, -2.0 on the Spark). The picture is copied;
  the result is a new file in `exports/`.

**On a Mac with an M5 or later and TensorFold's Metal engine** (since 2.9.2, the default where it is built) the song
runs on `music3-generate --serve 1` built for Metal: Zig and Metal, no Python, MLX or PyTorch (fork branch
`upstream/music3-metal`, commit 8c9559da, the code of TensorFold pull request #987; `$PREFIX/music3/bin`,
`MUSIC3_METAL_BIN` names another). It has the CUDA engine's protocol and lifecycle, int8 weights by default
(`MUSIC3_WEIGHTS=bf16` for the checkpoint's own) and the kill-guard. `bash oneshot-setup.sh --music` builds it
(`scripts/build-music3-metal.sh`: Zig 0.17 and Xcode's Metal compiler, a few minutes) beside the MLX port. Without
it, or on an M1 to M4 (its DiT, prompt rows and decoder need the M5's tensor units), the Mac runs the MLX port below,
and `MUSIC3_BACKEND=mlx` chooses that port anyway. **It does not stream yet**, so *Play while it's made* is not shown;
the MLX port keeps it. Measured on a Mac Studio M5 Ultra against the MLX port int8 (same prompt and seed, 60 s
hip-hop, runs alternated, three each, TensorFold's command line): the autoregressive model 69 frames/s against 65,
the whole song 59.5 to 63.0 s against 62.3 to 63.9 s, peak footprint 17.8 GB against 28.1 GB, byte-identical repeats.
Its DiT and decoder match ComfyUI's reference more closely than MLX's (38.97 dB and 103.9 dB against 34.9 and 50.5).
Through the app: a song the model ended at 33.4 s took 35.2 s, the load (2.9 s) included, with a 16.1 GB peak.

**On an older Mac, or with `MUSIC3_BACKEND=mlx`,** the song runs on TensorFold's MLX port of MiniMax Music 3 (fork branch `music3-mlx`, commit 6844b413: the
autoregressive model with its depth decoder, the DiT and the DAV decoder, in MLX), as a persistent process in its own
environment (`python -m tensorfold.music3 --serve`, Drama 3's protocol), under a memory guard (8 GB of vm_stat's free
pages, `MUSIC3_GUARD_GB`). `bash oneshot-setup.sh --music` (`scripts/setup-music.sh`) clones the branch at that commit
into `$PREFIX/music3-src`, makes `$PREFIX/music3-venv`, downloads the weights to `~/music3-models/MiniMax-Music3-Comfy`
(`MUSIC3_MODEL` names another folder; `MUSIC3_VENV` another environment) and makes a 10 s test song. 64 GB of memory
or more. Measured on a Mac Studio M5 Ultra 256 GB: the installer's test song, 10 s in 12.1 s, 28.1 GB at the peak;
through the app, a 60 s song in 75.8 s (the autoregressive model 37.8 s, the DiT 34.6 s, the decoder 2.0 s), MLX's
peak 27.6 GB. TensorFold's own run of the command line (the same hip-hop lyrics as the Spark's, seed 7): 30 s in
61.2 s, 60 s in 82.5 s, both at 27.6 GB.

**int8 weights by default.** The Studio starts the Mac engine with `--quant int8`: the autoregressive model,
its code-0 head and the depth decoder at 8 bits. On the M5 Ultra the autoregressive model went from 40 to 65 frames a
second and the peak from 27.6 to 20.5 GB, so a 60 s song takes about 1.1 times its length. Against ComfyUI's
reference the code-0 logits keep cosine 0.99994 (bf16 0.99997) with 98.0 % greedy agreement, and Whisper's lyric
scores over six same-seed songs stayed within their noise. Measured through the server as the app starts it: 10 s in
10.1 s, 20.9 GB. `MUSIC3_QUANT=none` runs the bf16 weights.

**On a DGX Spark with TensorFold's CUDA engine** (since 2.9.1, the default where it is built) the song runs on
`music3-generate --serve 1` (fork branch `music3-cuda`, commit 6b714531; `$PREFIX/music3/bin`, `MUSIC3_CUDA_BIN` names
another), the Mac engine's protocol and lifecycle: loaded on the first song (about 37 s; int8 weights are made on the
GPU as it loads, `MUSIC3_WEIGHTS=bf16` for the checkpoint's own), holding the GPU lock while loaded, unloaded after
`MUSIC3_IDLE_S` and before any other job, under the kill-guard (`MUSIC3_GUARD_GB`, 8). A studio ComfyUI that holds
models is stopped before it loads. `bash oneshot-setup-spark.sh --music` downloads the weights and installs the engine
(`spark/build-music3.sh`: from the carrier image, or built with nvcc 12.9+ and Zig 0.17). Measured on one GB10, int8,
the same hip-hop lyrics, seed 7 (one process a song, memory above the idle machine):

| Asked for | Made | Whole run (load 36.6 to 37.4 s included) | Autoregressive model | DiT, 30 steps | Decoder | Peak memory taken |
|---|---|---|---|---|---|---|
| 30 s | 30.0 s | 60.3 s | 36.4 s (20.9 frames/s) | 22.0 s | 1.9 s | 16.6 GB |
| 60 s | 60.0 s | 123.9 s | 73.9 s (20.4 frames/s) | 46.4 s | 3.6 s | 17.9 GB |
| 120 s (hip-hop lyrics) | 63.3 s (the model ended it) | 128.2 s | 78.0 s | 46.3 s | 3.9 s | 18.9 GB |
| 120 s (long lyrics, seed 5) | 120.0 s | 252.0 s | 151.7 s (19.9 frames/s) | 93.0 s | 7.3 s | 19.3 GB |
| 60 s, bf16 weights | 60.0 s | 194.3 s | 144.4 s (10.4 frames/s) | 46.3 s | 3.6 s | 25.5 GB |

Through the app on a worker Spark: a 60 s song in 149.2 s (load 37.3 s, then 111.9 s; the model ended it at 55.2 s),
18.7 GB at the peak, Whisper 97 %; the engine unloaded after its idle time and gave the lock back. Not streamed;
GB10 only. Without the binary or the weights the tab uses ComfyUI's nodes, below; `MUSIC3_BACKEND=comfy` forces them.

**On a DGX Spark without it** the song runs in the studio's ComfyUI (`spark_generate.py music`) on ComfyUI's own MiniMax Music 3
nodes, laid out as ComfyUI's blueprint *Text to Music (MiniMax Music 3)*: the text encoder (pruned, bf16, 16.7 GB) runs
the autoregressive model, the DiT (fp16, 4.9 GB) the 30 steps, the DAV decoder works in tiles. A song starts from a
ComfyUI with nothing else loaded (the server is restarted when it held the video or picture models); afterwards its
models are freed with ComfyUI's `/free`, after the idle time or before the next job. The memory guard beside the
server is the one every render has, and the song is checked before it is queued (`memory_guard.music_check`).
`bash oneshot-setup-spark.sh --music` downloads the three files (22 GB) to `~/spark-studio-models/MiniMax-Music3-Comfy`.
Measured on one DGX Spark (GB10, ComfyUI 5a665890, one run each, the models in the page cache; peak memory is
MemTotal - MemAvailable sampled once a second, less the 5.7 GB the machine held before):

| Asked for | Made | Whole run | Autoregressive model | DiT, 30 steps | Decoder | Peak memory taken |
|---|---|---|---|---|---|---|
| 30 s (hip-hop lyrics) | 30.0 s | 131.0 s (ComfyUI started for it); 127.5 s loaded | 84.2 s | 44.2 s | 2.1 s | 28.1 GB |
| 60 s (hip-hop lyrics) | 60.0 s | 260.6 s | 163.8 s | 92.5 s | 4.0 s | 28.7 GB |
| 120 s (hip-hop lyrics) | 55.6 s (the model ended it) | 239.1 s | 152.4 s | 82.8 s | 3.7 s | 28.6 GB |
| 120 s (long pop lyrics) | 120.0 s | 527.6 s | 331.4 s | 188.1 s | 7.7 s | 29.1 GB |
| 300 s (long pop lyrics) | 189.8 s (the model ended it) | 844.7 s | 530.1 s | 301.9 s | 12.3 s | 29.0 GB |

On the Spark the autoregressive stage is ComfyUI's Python loop, about 9 frames a second (25 make a second of song), and
it is the larger part of every song; the Mac's MLX port runs it about four times faster. Through the app, from an
empty page cache with ComfyUI stopped, a 60 s song took 289.0 s and 29.3 GB (the table in [New in 2.9.0](#new-in-290)).
Whisper heard 96 % of the words of the 60 s hip-hop song above (162 of 168).

The API: `GET /api/music/state`, `POST /api/music/takes {caption, lyrics, seconds, seed?, steps?, dit_cfg?, ar_cfg?,
top_k?, title?, asr?, live?}` (a job of kind `music`; `live: false` does not stream), `GET /api/music/takes`, `GET
/api/music/takes/{id}/audio`, `GET /api/music/takes/{id}/live` (the song being made, a stereo WAV), `POST
/api/music/soundtrack {take, video, music_db?}` (a job of kind `soundtrack`), and an export job's `params.soundtrack
{take, music_db?}`. Songs are kept in `~/ImaginationStudio/music/` (the WAV and its settings and measurements).

**Licence.** MiniMax-Music3 is under the **MiniMax-Music3 Community License**: commercial use is allowed; a commercial
product or service must display "MiniMax-Music3" in its user interface; a licensee above US$20 million of yearly
revenue needs MiniMax's prior written authorisation; a hosted service must keep safeguards against misuse. Do not
redistribute the weights.

## Sound effects and foley (Stable Audio Open, MMAudio)

Sound → **Sound effects** makes two kinds of take, each a job of the queue, on a Mac (`bash scripts/setup-sound.sh`,
or `oneshot-setup.sh --sound`; about 13 GB of weights in `$PREFIX/sound-models` and Hugging Face's cache):

- **Foley for a clip** (MMAudio large_44k_v2): pick a Library clip; MMAudio watches its frames and makes its sound,
  steps on the footfalls. Words are optional. The result is a WAV and the clip with it, laid under the clip's own
  sound at -6 dB (its speech stays on top; peaks under -1 dBFS) or in place of it. Up to 15 s, the studio's longest clip.
- **A sound effect** (Stable Audio Open 1.0): words to 1 to 47 s of 44.1 kHz stereo.

Both land in `~/ImaginationStudio/sound/` with a take file each, and in the Library under **Sound effects** (foley
clips under Clips). `POST /api/jobs {kind: "sfx", params: {prompt, seconds, negative?, seed?, steps?, cfg?}}` or
`{kind: "foley", params: {video, prompt?, negative?, keep_sound?, foley_db?, seed?}}`; `GET /api/sound/state`,
`GET /api/sound/takes`.

**On a DGX Spark** (`bash oneshot-setup-spark.sh --sound`, about 11 GB in `$SPARK_MODELS/Sound-Comfy`) both run through
the studio's ComfyUI server (`spark/comfy.sh`, with its memory guard beside it) as `spark/spark_generate.py sfx` and
`foley`, never as standalone model code:

- **A sound effect** runs on ComfyUI's own Stable Audio nodes (the checkpoint, its T5-base encoder, the seconds as
  conditioning, KSampler with dpmpp_3m_sde_gpu on the exponential schedule), the files from Stability's gated repo
  with your own Hugging Face sign-in.
- **Foley** runs on kijai's ComfyUI-MMAudio (MIT, pinned at `8eaeb72e`) with the Studio's own sampler node
  (`spark/studio_mmaudio`, put beside it by `comfy.sh`): kijai's sampler reads one stream of frames as both CLIP's
  8 a second and Synchformer's 25 a second, so a 24 fps clip would land a little early; the Studio's node takes the
  clip's own rate and picks the frame on screen at each of MMAudio's times. The clip is brought to 384 rows before
  ComfyUI decodes it (a 15 s clip at 1376x768 would be 4.6 GB of frames in memory, at 384 rows 1.1 GB). The weights
  are Kijai/MMAudio_safetensors (fp16, run in bf16) and NVIDIA's BigVGAN v2, fetched by the install so nothing is
  downloaded at the first take.

Each starts from a ComfyUI server with nothing else loaded, as a song does. A kind renders on a Spark only once
`spark/memory_guard.py` has its measured estimate (`SOUND_ESTIMATE`, `python3 spark/memory_guard.py sound`): it is
asked before the job is queued and again before anything loads, and a kind with no measurement is refused with that
reason. The WAV is levelled the way a song is (full level, peaks under -1 dBFS) and foley is laid under the clip by
the same ffmpeg step as on a Mac.

**Licences.** Stable Audio Open 1.0: Stability AI Community License (free under US$1 million of yearly revenue); its
Hugging Face page is gated, so accept the licence there first (the studio's Install guide signs you in to Hugging
Face). MMAudio's weights are **CC-BY-NC 4.0: non-commercial**; its code is MIT. Do not redistribute either's weights.

## Motion Previs: a shot into control layers and a camera move

**New to it? Start with the walkthrough:** [TUTORIAL.md, section 9](TUTORIAL.md#9-motion-previs-make-a-clip-that-moves-like-another)
(two worked examples, what each choice does, and what to do when a clip goes wrong), or **Tutorial → Motion Previs** in
the app.

The **Motion Previs** tab takes a clip (one you upload, or an earlier render or upload), a range of 2 to 15 seconds
and one of four reference modes (camera only, actor motion, object motion, full scene), and writes a production pack
under `~/ImaginationStudio/motion/<id>/` with a ZIP beside it: the trimmed reference clip, a pose track
(`pose_landmarks.json`), an OpenPose BODY_25 skeleton video and `openpose_keypoints.json`, a subject-masked camera
solve (`camera_motion.json`), depth (Depth-Anything-small, and an ffmpeg proxy), edges, line art, a motion mask, a
contact sheet, a shot bible and a prompt pack. The tab shows the reference, skeleton, depth and edges side by side
and says the camera move in a sentence ("The camera pushes in while it slowly tilts up ..."). Stages report their own
progress and Cancel stops the job and keeps nothing. A pack ZIP exported by Motion Previs Studio can be uploaded in
place of an analysis.

**The analysis is a Python port, modified, of [Motion Previs Studio](https://github.com/wassermanproductions/motion-previs-studio)
by Sam Wasserman** (Apache-2.0, Copyright 2026 Sam Wasserman). Packs use his file names and manifest layout, so they
are interchangeable with his app's. His NOTICE is at `LICENSES/Motion-Previs-Studio-NOTICE`; what was changed and the
notices for MediaPipe, Depth Anything, OpenCV and ONNX Runtime are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

```bash
bash oneshot-setup.sh --motion      # or: bash scripts/setup-motion.sh   (0.5 GB, an environment of its own)
```

`--motion` is an option, not part of the default install: MediaPipe brings its own OpenCV build, which must not replace
the Studio's, so the analysis has its own environment (`$PREFIX/motion-venv`, 433 MB, and `$PREFIX/motion-models`,
70 MB). Without it the tab still trims the shot and makes the ffmpeg layers, and on a Mac the camera solve; pose and
AI depth say that they are not available on this machine. Measured on a Mac Studio M5 Ultra, a 5 second 864x480 shot
with every layer at 12 samples a second: 6.6 s in all (prepare 0.2, pose 1.3, camera 0.1, depth 3.4, encode 0.9,
pack 0.1), 0.5 GB of memory at its peak, on the processor. On a DGX Spark (`bash oneshot-setup-spark.sh --motion`;
MediaPipe 1.1.0, OpenCV 5.0.0 and ONNX Runtime 1.31.0 all installed from their Linux aarch64 wheels) the same kind of
shot with every layer took 12.6 to 17.6 s (prepare 0.4, pose 3.3, camera 0.2, depth 5.8, encode 1.9, pack 0.4 in the
quicker run), 0.4 to 0.5 GB at its peak, with the same camera sentence the Mac gave for that clip. On the Spark the
tab was driven through its API and a screenshot of it from a headless Firefox was looked at; it has not been used by
hand in a browser on either machine. `?motionref=1` in the app's address opens the create wizard on the stored motion
reference.

**Use as motion reference** makes a hand-off (one layer of the pack resampled to the length and size of the clip to
be made, the camera move in words, and the reference mode: `motion_reference()` in `app/motion.py`) and opens the
create wizard on its references screen with that layer as `<Video 1>` of a MiniMax H3 Ref2VA clip.

### Motion as a reference in the create wizard

*The clip starts from → References*, add a reference video, then **Analyse motion** under it (offered when the
motion environment is installed; without it the video is a plain reference). The analysis runs on the processor in
well under a minute and gives two choices:

- **What to carry over:** *Full scene* (the same place and motion with a new person: its person is replaced by
  `<Picture 1>`), *Actor motion* (the body motion and the camera, in a new place), *Object motion* (an object's path
  and the camera) or *Camera only*.
- **What goes to the model as `<Video 1>`:** the **source clip** (the default for *Full scene*), the **depth** layer
  as a control video (the default for the other three), or the **skeleton** (offered, never the default) or edges.
- **People in `<Video 1>`:** *One* or *Two or more*. With two or more the prompt speaks of its people and figures,
  one for one. You say it: the pose pass, its skeleton layer and the distance figures below follow **one person
  only** (asked for up to four people on a two-person duel at arm's length, it returned one in every frame).

**Fast motion, on the Mac** (through the app, 5 s at 864x480, seed 7; two cuts of a fictional martial-arts short: one
man drilling a wooden dummy while the camera pushes in to a close-up, and two fighters trading hands while the camera
cranes up to overhead; pose distance as in the table below, median / 90th percentile):

| shot | `<Video 1>` | passes | time | distance | what came out |
|---|---|---|---|---|---|
| dummy drill | source clip + a picture of a woman | Turbo 4, as first shipped | 236 s | 26.4 / 36.6 | the place kept; she drifts out of frame, strikes lost |
| dummy drill | the same request | Turbo 4, without the int8 kernels | 231 s | 3.7 / 11.2 | held |
| dummy drill | the same request | 20 | 741 s | 4.2 / 11.7 | held: she does the drill and the push-in on the same rooftop |
| dummy drill | depth + a picture of her in a bamboo clearing | Turbo 4 | 235 s | 3.5 / 11.7 | held, in the new place |
| dummy drill | depth + a picture of her on the same rooftop | Turbo 4 | 247 s | 16.0 / 25.3 | the drill and the push-in, with the layout shifted |
| duel | depth + a picture of the pair in a temple courtyard | Turbo 4 | 234 s | not valid | held by eye: both new fighters in time, the crane-up followed |
| duel | source clip + a picture of a new pair, *One* person | Turbo 4, without the int8 kernels | 294 s | 3.2 / 8.3 | motion and crane-up followed; only one fighter replaced |
| duel | the same with *Two or more* people | Turbo 4, without the int8 kernels | 284 s | 3.1 / 8.3 | both fighters replaced, motion and crane-up followed |

The first row was a defect of the Mac build, not of the source-clip route (the DGX Spark held the same request at
2.9 / 7.7): the Ref2VA Turbo adapter, a reference video and the three int8 kernels together lose the motion. Any one
kernel alone held (3.4 to 3.5), any two did not (14 to 23), and 20 passes without the adapter held with all three.
`scripts/video.sh` now runs that one case in bfloat16, about 13 s more a pass at 864x480 (`REF_INT8=1` puts the
kernels back). The depth duel's figures are not valid because the checker follows one person and picked different fighters in
source and result; the source-clip duel's are for the one fighter it followed. The adapter at 3 passes held the
dummy drill as well (3.2 / 10.5, 257 s). One seed each.

The camera move in words goes into the prompt in every case (a switch; text of your own in the Camera field wins).
With the source clip and a picture, step 4 writes "The target video is `<Video 1>` with its person replaced by
`<Subject 1>`"; calling the picture a first-frame anchor there made the picture's layout override the video. For a
control layer it writes the control-video form: `<Video 1>` is declared the structural template (layout, silhouettes,
positions, movement and camera path frame by frame), `<Picture 1>` the first-frame anchor, with a legend of the layer
(a skeleton's coloured lines are joints and limbs on black; in a depth map brighter is nearer), "These colours are
labels only, not the look of the target video", and the closing "no skeleton lines, no flat-coloured, depth-map or
segmentation-map look". Give a picture of the opening frame as `<Picture 1>` with a control layer: the look and the
setting come from it.

Where the defaults come from: one shot measured on the Mac (a martial-arts sequence with a kick and a punch toward
the camera, fictional people, 5 s at 864x480; the distance of the body's keypoints from the source's, in percent of
the frame width, lower is closer):

| `<Video 1>` | Distance | What it kept |
|---|---|---|
| Source clip, with a picture of another person | 0.6 to 2.2 | the shot move for move, in its own place, with the new person |
| Depth, with a first-frame picture | 1.3 to 2.9 | the kick and the punch toward the camera; the setting is the picture's |
| Depth, with a picture of another setting | 2.7 to 5.5 | the new place with no leak of the source's; the punch, not the kick |
| Skeleton, with a first-frame picture | 2.0 to 4.5 | the scale and the camera; the kick was lost |
| The picture alone | 5.0 to 9.8 | |

No control layer left a trace of its look in a clip. One shot: read the table as an order, not as figures to promise.
On the DGX Spark the same routes were rendered through the wizard's own API calls with the Turbo adapter and looked
at, not measured: see [the Spark's table](#reference-videos-audio-and-motion-on-a-dgx-spark).

### Sound and movement of a motion reference: Impacts or Flow

A clip's sound and its movement have to agree. The first duel renders above did not: the reference is two people
doing slow sticky hands (forearms in contact, circling), the soundscape asked for "quick slaps of forearms and hands",
and the model did both: it followed the slow hands and laid 1.7 to 1.9 slaps a second over them, on the Mac and on
the DGX Spark alike. Three things were measured before anything was changed:

- **The slaps came from the Soundscape field**, not from the source clip (its own sound is at -54 dBFS and was not
  sent).
- **The slow hands came from the reference.** The source clip's quickest movement is 14 percent of the frame width a
  second; the renders made from it are at 14 to 17. A reference video is followed whatever the prompt says about
  speed: four wordings were tried on the Spark (fast words in the action, a template that says the slow hands are
  not followed, *Camera only*, and the clip played at twice its speed and back), and the arms stayed at 15 to 21.
- **The music of the Mac's fourth test** was the model's own: nothing in that prompt asked for music.

**Sound and movement**, under *Analyse motion* on the references screen, is the choice that follows from this:

| | What the prompt says | What is sent | Sound |
|---|---|---|---|
| **Impacts** | both fight at full speed: sharp strikes and hard blocks, attacking and blocking, every strike heard as it lands | a reference that moves fast goes in as before. One that moves slowly is **left out** (a switch, on by default, when there is a picture of who is in the clip): the picture and the prompt carry the fight, and the camera move of the video stays, in words | the hits of the soundscape |
| **Flow** | the movement is slow, smooth and continuous, and no strike lands | the reference, as before | the hits are taken out of the soundscape and a video's own hits are not reused. Without music the clip is **delivered silent** (its track is muted after the render); with music named in step 3, under that music |
| **Automatic** (the default) | as you wrote it, except that a soundscape that names hits over a reference that moves slowly becomes Flow | the reference | as Flow in that one case |

The analysis measures the reference for this (`app/pace.py`, with ffmpeg and NumPy alone): **movement** is how fast
the quickest parts of the picture move against the rest of it, in percent of the frame width a second (frames lined
up on the camera's shift, each pixel's change over its edge strength, the three quickest blocks less the median
block, 90th percentile over the clip), read also against the size of the action in the frame, so that a fight seen
from across a roof is not called slow for being small; **hits** are sharp onsets a second that fill the band above
600 Hz and die away within 50 ms, which a plucked note and a spoken consonant do not. The same two figures are taken
of every finished reference clip and shown under it ("Sound and movement: agree", or "impact sound over slow motion"
in red): a flag beside the clip, like the QA gate's lines, never a failed job. `pace`, `silent` and
`motion.measured` / `motion.free` are the job's settings for scripts that drive `/api/jobs`.

Measured on the duel, through the wizard's own API calls, Turbo 4 passes, 5 s at 864x480, seed 7 (movement in percent
of the frame width a second, then the same against the size of the action; fast is 24 or 44):

| machine | Sound and movement | time | movement | hits a second | checker | by eye |
|---|---|---|---|---|---|---|
| Mac | before: source clip, slaps in the soundscape | 284 s | 14.2 / 34.8 slow | 1.7 | impact sound over slow motion | slow sticky hands under slaps |
| Mac | Impacts (video left out) | 89 s | 25.0 / 62.4 fast | 2.5 | agree | both throw straight punches and block, in time with the slaps; a new rooftop from the Scene text, the push-in and tilt followed |
| Mac | Flow, no music | 262 s | 14.5 / 35.5 slow | 0 | agree | the slow hands of the reference, silent |
| Mac | Flow, with music | 272 s | 15.6 / 40.4 slow | 0 | agree | the slow hands under a slow plucked melody |
| DGX Spark | before: source clip, slaps in the soundscape | 216 s | 16.8 / 40.6 slow | 1.9 | impact sound over slow motion | slow sticky hands under slaps |
| DGX Spark | Impacts (video left out) | 101 s | 17.0 / 59.0 fast | 2.5 | agree | both strike and block at speed, seen from further off than on the Mac; a new rooftop, the push-in followed |
| DGX Spark | Flow, no music | 206 s | 14.9 / 37.8 slow | 0 | agree | the slow hands of the reference, silent |
| DGX Spark | Flow, with music | 207 s | 15.3 / 37.6 slow | 0 | agree | the slow hands under a slow plucked melody |

What this does not do. **Impacts cannot make a slow reference fast**: with the video left out the clip keeps who is
in it (from the picture) and the camera move (in words), and loses the reference's own place and blocking, which
then come from the Scene text. A picture of the reference's first frame as a second picture was tried for the
place and brought the reference's people back with it after the first second, so it is not used. Before the track
is muted, a Flow clip asked for near silence was near silent on the Spark (-45 dBFS) and under music of the model's
own on the Mac in two of three renders, which is why the track is muted and not left to the prompt. The thresholds
were set on thirteen clips of two shots; they sort the clear cases and call the rest "moderate", which is not
flagged. One seed each.

**The mouth of a reference video.** A picture with a source clip of someone talking, in a clip where nobody speaks:
the new face mouths the reference's words (a woman asked only to smile opens and closes her mouth through the last
second, on both machines). The model copies the lip movement whatever is asked: the same clip came out with a
prompt that says her lips stay together and that the mouth of `<Video 1>` is not followed, and at 20 passes without
the Turbo adapter (761 s on the Spark). What removes it is taking the mouth out of the reference: **Keep the mouth
of `<Video 1>` out of the clip** (on by default when the source clip itself goes in, the clip has no spoken lines and
does not reuse the video's sound) smooths an ellipse over the mouth and jaw of the pose track's person before the
video goes to the model (`soft_mouth()` in `app/motion.py`, ffmpeg and NumPy; `mouth: "soft"` on
`/api/motion/reference`). With it she holds the smile to the end, on the Spark (202 s) and on the Mac (275 s); body,
head and camera follow the reference as before. It needs *Analyse motion* (the pose track) and follows one person.

**The woman who left the frame** (the first dummy-drill row above) was checked again by eye on the Mac through the
app's own job path, the way the wizard runs it, at this release's code: 259 s, she stays in frame for the whole clip
and strikes the dummy as the reference does (movement 28.8 fast, 2.3 hits a second, "agree"). The app goes through
`scripts/video.sh`, so it picks up the fix (a reference video with the Turbo adapter runs without the int8
kernels) with nothing to set.

## FastH3 (new in 1.8)

**Credit first: FastVideo / Hao AI Lab** made FastH3 (the distilled weights and the sparse-attention routing they
were trained with), on **MiniMax's** H3. What this pack adds is the attention kernel that makes it fast on a Mac.

```bash
bash scripts/fast.sh "a prompt" out.mp4                      # 480p (864x480), 8 passes
STEPS=4 bash scripts/fast.sh "a prompt" out.mp4              # 4, 8 or 20 passes
RES=720p bash scripts/fast.sh "a prompt" out.mp4             # 1280x720
UPSCALE=1 bash scripts/fast.sh "a prompt" out.mp4            # 480p generated, 2x decoder: 1728x960
RES=720p UPSCALE=1 bash scripts/fast.sh "a prompt" out.mp4   # 720p generated, 2x decoder: 2560x1440 (2K)
# Qwen-Image-2.1 makes the first frame, FastH3 animates it (all the studio.sh presets work: X2=1, TWOK=1, QHD=1)
ENGINE=fasth3 STEPS=8 WIDTH=864 HEIGHT=480 bash scripts/studio.sh "the picture" "what happens" out.mp4
```

In the web app FastH3 is the first card under **Video engine**, with Passes, Resolution and 2x upscale beside it.

Measured on a Mac Studio M5 Ultra (64-core GPU, 256 GB), 5 second clips of 124 frames with sound, one run each,
whole command including model load and decode:

| Output | Generated at | 4 passes | 8 passes | 20 passes |
|---|---|---:|---:|---:|
| 480p, 864x480 | 864x480 | 51 s | 78 s | 174 s |
| 480p + 2x decoder, 1728x960 | 864x480 | | 88 s | |
| 720p, 1280x720 | 1280x736, cropped | 112 s | 192 s | 434 s |
| 720p + 2x decoder, 2560x1440 | 1280x736, cropped | | 203 s | |

A pass takes 7.1 s at 480p and 19.7 s at 720p; the 2x decoder adds about 10 s. A 10 second 1312x736 clip (243
frames) takes 478 s at 8 passes, against 2,183 s with FastVideo's own Metal kernel in the same engine.

- **8 passes** is what the checkpoint was trained for. **4** and **20** spread that many rungs evenly over the same
  schedule: by eye on one prompt, 4 is a little softer and 20 has more texture. Neither is a setting FastVideo
  trained or tested. FastVideo's dedicated 4-step preview checkpoint was tried too and looked no better than V2 at
  4 passes for the same time, so the pack uses one checkpoint for all three.
- **2x of 480p is 1728x960**, which is above 720p, not exactly 720p. For a 1344x768 clip generated at half size,
  the Turbo path's `X2=1` draft preset does that.
- **Why it is fast.** FastH3 lets each 64-row tile of the video attend to the text and audio rows and to the best
  fifth of the video tiles. The kernel gives one threadgroup one query tile of one head and walks only the chosen
  key tiles, read in place, with int8 scores and values. At 69K rows a block takes 0.52 s against 1.93 s for dense
  attention. The int8 scores pick a few different tiles than FastVideo's float reference would, so details differ
  from its output (cosine 0.9999 on random inputs; not a bit-exact port).
- **Image to video works, though FastVideo trained FastH3 on text to video only.** One test (the baker prompts,
  864x480, 8 passes): the clip opens on the Qwen image, keeps the face and follows the prompt; image 9 s, video
  94 s. The first-frame rows join the rows every video tile always attends to. Not tested widely.
- The sound is FastH3's own; the base-model audio step is not applied here.
- FastH3 weights are a derivative of MiniMax H3 under the MiniMax H3 Community License.

## The native engine (new in 2.0)

**Credit first: Ash Hart and the TensorFold contributors** wrote the TensorFold 1.0 runtime this uses (Zig driving
Metal directly, Apache-2.0), and **FastVideo / Hao AI Lab** made FastH3. What this pack adds is an H3 family for that
runtime: FastH3's block stack, its tile-routed attention and its int8 kernels for the M5 tensor units.

| What | Where it runs |
|---|---|
| FastH3, from text or from a first frame (a Qwen scout, your own image, every part of a long chain): all transformer passes (rows in, 50 blocks, routing, attention, both output heads) | `tf-h3-dit`, the native Zig + Metal program |
| Prompt encoding, the first frame's encoding, the checkpoint's small projections and timestep tables | Python (MLX), once per clip |
| Video decoder, audio decoder, MP4 | Python (MLX), as before |
| Qwen-Image-2.1, the Turbo adapter path, the 20-step path, the base-model audio step | MLX engine, unchanged |

`scripts/video.sh` picks the native engine for a FastH3 clip when it is installed and the chip has tensor units (M5
or later); it prints `[tensorfold] engine: zig` or `engine: mlx (why)`, and the web app
shows the same on the job. `FASTH3_ENGINE=mlx` or `zig` forces one.

Measured on a Mac Studio M5 Ultra (64-core GPU, 256 GB), FastH3 with sound, text to video unless a row says from
an image, one run each, whole command including export and decode unless a row says per pass:

| Clip | Native engine | MLX engine |
|---|---:|---:|
| 864x480, 124 frames, per pass | 5.33 s | 7.12 s |
| 864x480, 5 s, 4 passes | 43 s | 51 s |
| 864x480, 5 s, 8 passes | 66 s | 78 s |
| 864x480, 5 s, 20 passes | 129 s | 174 s |
| 864x480, 124 frames, from a Qwen-Image first frame, per pass | 6.14 s | 8.08 s |
| 864x480, 5 s, 8 passes, from a first frame (image already made) | 72 s | 90 s |
| 1728x960 through the 2x decoder, 5 s, 8 passes, from a first frame | 77 s | not run |
| 18 s chain of two clips, 864x480, 4 passes | 156 s | 183 s |
| 1280x720, 5 s, 8 passes | 199 s | 259 s |
| 1280x720, 10 s (243 frames), 8 passes | 388 s | about 480 s (53 s a pass; not re-run) |
| 1280x720, 10 s (243 frames), 20 passes | about 950 s (45 s a pass plus export and decode) | not run |

The two 1280x720, 5 s figures come from one sitting with another job using the machine on and off, so both are
slower than a quiet run would be (the MLX figure was 192 s when first measured); read them as a ratio.

- **Quality.** The native engine and the MLX engine both use int8 arithmetic and agree with a float reference to the
  same degree on the first pass (video cosine 0.961 and 0.966). The same seed gives the same shot with different
  details, not the same pixels. The clips were judged by eye by the pack's owner; the native engine's attention
  weights are 8-bit.
- **20 passes** is the setting to reach for when a prompt has several distinct actions: in two tests it brought in
  action the 8-pass clip had skipped and removed an object glitch. It is not a setting FastVideo trained.
- **Sizes.** The routing cuts the video into tiles of 4x4x4 tokens (128 pixels a side, 17 frames deep). A size that
  is not whole tiles is padded inside the attention. Rounding the size up instead is not faster at the same length
  (896x512 took 5.75 s a pass against 5.33 s for 864x480 at 124 frames), so the studio generates the size you ask
  for. Lengths of 107, 175, 243 and 311 frames are whole tiles in time; 243 is the 10 second choice.
- **From an image.** A first frame's rows sit between the text and the audio, enter on every pass at the keyframe
  noise level and are never stepped, as in the MLX engine. On the baker test the clip opens on the given image
  (frame 0 against the image: 33.7 dB on both engines) and the first pass agrees with the float reference as closely
  as the MLX engine's does (video cosine 0.995 and 0.997). One image and prompt at 864x480, plus the app tests.
- **Not in the native engine yet:** the prompt encoder, the first-frame encoder, the decoders, Qwen-Image. A Qwen-Image
  transformer for the same runtime exists in the fork and is level with the MLX one (0.483 s against 0.477 s a pass
  at 1344x768), so the studio keeps the MLX one.
- **Install.** `oneshot-setup.sh` copies the engine from the carrier image's payload (a 0.7 MB program, its Metal
  kernels inside it, compiled when it starts). Without Docker it builds from source if `zig` 0.17 and Xcode's Metal
  toolchain are present (`scripts/build-zig-engine.sh`); with neither, FastH3 stays on the MLX engine and everything
  still works.

## How it works

1. **You write two prompts**: what the picture shows, and what happens in the clip (action, spoken or sung words,
   sound).
2. **Qwen-Image-2.1-Turbo makes the first frame** from the first prompt, in about 9 seconds (8 steps). Or it
   makes several scouts for you to choose from.
3. **MiniMax H3 animates that frame** from the second prompt, with sound, in one of two modes:
   - **Standard (Turbo 5).** The Turbo adapter denoises picture and sound together in 5 passes. The adapter is then
     dropped and the base model denoises the sound again from scratch, 20 steps, with the finished picture held
     fixed. Each of those steps recomputes only the audio rows (about 4% of the sequence) against the picture's
     stored attention keys and values, so 20 steps cost less than one full pass.
   - **High quality (`QUALITY=high`).** The base model denoises picture and sound together for 20 steps, no adapter.
     About three times slower; calmer motion, softer picture, and the sound the standard mode's audio step is
     borrowed from.
4. **The decoders finish it.** The video decoder turns latents into frames, optionally at twice the size (the 2x
   decoder, for 2K). The audio decoder's output is kept clear of clipping, and a take that comes out thin gets its
   bass lifted.

Both transformers, the samplers, the adapter merges, the audio step, the int8 kernels and the image and video
decoders run in TensorFold on MLX.

**Scout, pick, animate, finish in 2K.** Six scout images from one prompt in 27 seconds; pick one; MiniMax H3 animates
it with sound and a 2x decoder finishes it in 2K: an 8 second clip at 2048x1152 in five minutes, or at 2560x1440 in
under eleven. A 1344x768 draft of the same 8 seconds takes 108 seconds.

![Six scouts from one prompt](samples/scout_sheet.jpg)

![Frames of the 2560x1440 clip, and a full-resolution crop](samples/qhd_frames.jpg)

The newest sample, made with the standard setting: [`samples/singer_standard_8s.mp4`](samples/singer_standard_8s.mp4)
(prompts `singer-image.txt` and `singer-video-8s.txt`). The baker clips below predate 1.5.

Prompts: [`prompts/baker-image.txt`](prompts/baker-image.txt) for the picture,
[`prompts/baker-video.txt`](prompts/baker-video.txt) for what happens. Clips: [`samples/`](samples/).

## The base-model audio step

*This section and the Turbo rows in "How it works" and "Measured" describe MiniMax H3 Turbo, which since 2.1 is
installed only with `oneshot-setup.sh --turbo`. FastH3, the default, makes its picture and sound together.*

The Turbo adapter is what makes the picture fast, and it is also what spoils the sound. So the sound is made twice:
once with the picture, by the adapter, and thrown away; then again by the base model, which is the one that sounds
right.

1. After the Turbo passes, the adapter-merged model is unloaded and the released weights are loaded (4 s).
2. The finished picture, the first frame and the prompt go through the model once, held still, and every block's
   attention keys and values over them are stored.
3. The audio starts again from noise and is denoised in 20 steps. Each step runs only the audio rows (1,206 of 29,009
   on a 15 second clip) against the stored keys and values: about 1.3 s a step instead of 24 s.

It is on by default whenever the adapter is used. `REVOICE=0` keeps the adapter's own sound; `REVOICE=<steps>` sets the
number of audio steps (20 is the only value tried). It was offered upstream (ashhart/TensorFold#405) and closed with the H3 family; it lives in the fork.

## The workflow

```bash
# 1. Scout: several first frames from one prompt, one model load (27 s for six), with a contact sheet
bash scripts/scout.sh "A lighthouse keeper in a yellow raincoat on a cliff at dusk, storm clouds behind him" 6 scouts/keeper
open scouts/keeper/sheet.jpg

# 2. Draft the motion from the one you like: 8 s at 1344x768 in under two minutes
IMAGE_FILE=scouts/keeper/scout_s3.png X2=1 FRAMES=192 bash scripts/studio.sh "" \
    "He raises a lantern, wind pulls at his coat, waves crash below. He says: Storm's coming." draft.mp4

# 3. Finish in 2K: same image, same prompt. TWOK=1 is 2048x1152 (5 min), QHD=1 is 2560x1440 (11 min)
IMAGE_FILE=scouts/keeper/scout_s3.png QHD=1 FRAMES=192 bash scripts/studio.sh "" \
    "He raises a lantern, wind pulls at his coat, waves crash below. He says: Storm's coming." final.mp4
```

Or all at once, with no picking: `TWOK=1 bash scripts/studio.sh "the picture" "what happens" out.mp4`.

- **Scouts are made at 1280x736**, which is exactly the frame the video model starts from for a 2560x1440 clip, so
  the scout you pick is the frame the clip opens on, with no resize in between.
- **Why 736 and not 720.** The video model needs sides in multiples of 32, and 720 is not one. The 2K preset
  generates at 1280x736, decodes at 2x to 2560x1472 and centre-crops 16 rows top and bottom to 2560x1440.
- **The draft and the final are different renders.** The draft generates at 672x384 and the final at 1280x736, so the
  motion is similar in kind, not frame for frame.

## Measured (2026-10-04, Mac Studio M5 Ultra 256 GB, macOS 27.0.1, one run each)


**Notes and Regenerate.** Under every take, in Shots and in Deliver, there is a box for notes or feedback and a
"↻ Regenerate" button. With notes, the brain revises the shot's prompt to answer them (the old prompt goes to History)
and a new take is queued; with the box empty, the same prompt is rendered at the next seed. Earlier takes are kept.

### Text to image to video (`scripts/studio.sh`), 8 second clips unless noted

| Output | Video generated at | Standard: Turbo 5 + base-model sound | Earlier: Turbo 3, adapter sound |
|---|---|---:|---:|
| Six scout images, 1280x736 (`scripts/scout.sh`) | | 27 s | 27 s |
| 1344x768, 2x decoder (`X2=1`) | 672x384 | **108 s** | 67 s |
| **2048x1152, 2x decoder (`TWOK=1`)** | 1024x576 | **299 s** | 151 s |
| **2560x1440, 2x decoder (`QHD=1`)** | 1280x736 | **635 s** | 352 s |
| 1344x768, native decode | 1344x768 | 709 s | 364 s |
| 864x480, native decode | 864x480 | 174 s | 98 s |
| 864x480, 5 s (124 frames), native decode | 864x480 | 103 s | 74 s |

Totals include the image (9-12 s). Where the standard time goes, 2048x1152: five passes 185 s (37 s each), the sound
made again 60 s, decode 31 s, loading and text 12 s. At 2560x1440: passes 436 s, sound 119 s, decode 55 s. The audio
step costs about one and a third passes at every size: one full pass to store the picture's keys and values, then 20
light steps.

`QUALITY=high` (20 steps, no adapter) took 606 s for a 15 second clip generated at 672x384, against 219 s at the
standard setting; it has not been timed at the sizes above.

The earlier column was measured with `POINTS=4 REVOICE=0`, which still works.

**Resolution costs far more than length.** The video model works on one row per 32x32 pixels of every latent frame,
and attention compares every row with every other, so its cost grows with the square of the row count. 5 s at 864x480
is 16,500 rows (9.5 s per pass); 8 s at 864x480 is 24,826 rows (18.3 s per pass); 8 s at 1344x768 is 60,403 rows
(104.6 s per pass). Going from 864x480 to 1344x768 is 2.5 times the pixels and 4 times the time for the same length.

**What the 2x decoder is.** A replacement head for H3's own video decoder (speach1sdef178's MiniMax-H3-X2-Detail-VAE)
that turns the same latents into frames twice as large along each side, in the normal decode time. It is an upscale: a
1344x768 clip decoded from a 672x384 generation is softer than one generated at 1344x768, with stair-steps on
high-contrast edges ([comparison](samples/x2_compare.jpg): native on top, 2x below). It earns its place twice: as a
6 times faster draft, and as the last step to 2K from a full-size generation, where the 2560x1440 clip costs no more
than the 1344x768 one. With `QHD=1` or `TWOK=1` the first frame is made at the size the video model starts from
(1280x736 or 1024x576), since a larger picture would only be shrunk again.

Wall time from the command to the finished files, both models loaded from disk each time, int8 kernels on. In every clip the baker lifts the loaf, speaks the scripted line
(checked by speech recognition) and smiles. The 864x480 run was the first after install, so its 14 s includes a cold
read of the text encoder.

### Qwen-Image-2.1 alone, 1344x768, same prompt and seed

| Path | Steps | Per step | Denoise | Whole run |
|---|---:|---:|---:|---:|
| mflux `add5164`, bfloat16 (the reference this port is checked against) | 40 | 0.73 s | 29 s | 40.7 s |
| TensorFold, bfloat16 | 40 | 0.78 s | 31.0 s | 34.3 s |
| TensorFold, int8 kernels | 40 | 0.48 s | 19.2 s | 24.3 s |
| TensorFold, int8 kernels + Viggle turbo adapter (the default up to 2.2) | 6 | 0.50 s | 3.0 s | 9.2 s |
| **TensorFold, int8 kernels, Qwen-Image-2.1-Turbo (2.3, measured 2026-10-09; peak 15 GiB against 28)** | **8** | 0.49 s | **3.9 s** | **8.9 s** |

![mflux bf16 40 steps / TensorFold bf16 40 steps / TensorFold int8 40 steps / TensorFold int8 turbo 6 steps](samples/qwen_image_compare.jpg)

Top: mflux, TensorFold bfloat16. Bottom: TensorFold int8, TensorFold int8 turbo. Against the mflux image the
TensorFold bfloat16 image is 32.7 dB and the int8 image 30.4 dB: the same picture with different fine detail. Turbo
gives the same scene with a different rendering.

- **Per step in bfloat16, mflux is slightly faster than this port** (0.73 s against 0.78 s). The gain comes from the
  int8 kernels (1.6x per step) and the 6-step adapter.
- **MiniMax H3 numbers**, the shootout against h3.c, H3MLX and minimax-h3-mlx, and what makes H3 fast are in the
  video-only pack: [keys-Mac-TensorFold-MiniMax-H3-MLX](https://github.com/drowzeys/keys-Mac-TensorFold-MiniMax-H3-MLX).
  At 20 steps the C engines are faster and sharper than TensorFold; TensorFold leads only with the Turbo adapter.

## One-shot

```bash
git clone https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3.git
cd keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3
brew install python@3.11 uv ffmpeg
bash oneshot-setup.sh               # both models and the 2x decoder: 182 GB of weights
bash oneshot-setup.sh --image-only  # or just Qwen-Image-2.1: 33 GB
```

`oneshot-setup.sh` does the following:

1. Gets TensorFold 0.6.5 with the H3 and Qwen-Image families (`drowzeys/TensorFold` at `a2068c03`), from the **GHCR
   prebuilt carrier** when Docker is available (checksums verified), otherwise from git at the same commit.
   The native FastH3 engine (`tf-h3-dit`) comes from the same carrier, or without Docker as a checksummed download
   from the [v2.0 release](https://github.com/drowzeys/keys-Wasserman-Slate-AI-Studio-powered-by-TensorFold-built-with-integrated-Fish-Audio-Drama3/releases/tag/v2.0); no Zig toolchain is needed.
2. Installs it with mflux at `add5164e` and the dependency lock ([`requirements.lock`](requirements.lock): mlx 0.32.3,
   mlx-lm 0.32.0, mlx-vlm 0.7.4, …) into its own venv at `~/.local/opt/tensorfold-studio`.
3. Downloads `Qwen/Qwen-Image-2.1-Turbo` (33 GB) to `~/qwen-models/Qwen-Image-2.1-Turbo`. Where an earlier install's
   `Qwen-Image-2.1` sits beside it, only the transformer (14 GB) is fetched and the rest is linked.
4. Unless `--image-only`: clones minimax-h3-mlx at `79190205`, downloads the FastH3 transformer (65 GB), the MiniMax
   H3 `FL2VA` partition without its transformer (72 GB: the text encoder and the two decoders) to
   `~/h3-models/MiniMax-H3` and the 2x video decoder (5.2 GB, checksum verified). With `--turbo`, the whole
   partition (134 GB) and the video Turbo adapter (1.96 GB, checksum verified).
5. Renders a test: a 5 second clip from a generated image (`outputs/test.png`, `outputs/test.mp4`), or a test image
   with `--image-only`.

Override `PREFIX`, `QWEN_MODEL_DIR` or `H3_MODEL_DIR` through the environment. `--verify` checks an existing install;
`--no-render` skips the test.

`studio.sh` and `video.sh` use MiniMax H3 Turbo when it is installed (`--turbo`) and FastH3 otherwise; `ENGINE=fasth3`
or `ENGINE=h3` picks one. `QUALITY=high` and `QUALITY=full` need `--turbo`. For FastH3 the sizes measured here are 864x480,
1280x736, 1344x768, and 672x384 and 1024x576 with the 2x decoder (the app's Draft and 2K cards: 44 s and 93 s for 5 seconds).

### Text to image to video

```bash
bash scripts/studio.sh "A lighthouse keeper in a yellow raincoat on a cliff at dusk, storm clouds behind him" \
                       "He raises a lantern, wind pulls at his coat, waves crash below. He says: Storm's coming." \
                       out.mp4
```

The image is written beside the clip (`out.png`). Defaults: 1344x768, 124 frames (5 s), native decode. `QHD=1` makes
a 2560x1440 clip; `X2=1` generates the video at half of `WIDTH` x `HEIGHT` and decodes at 2x (multiples of 64, up to
2688x1536); `IMAGE_FILE=picture.png` animates an image you already have. Set `FRAMES=192` for 8 seconds, `WIDTH`/`HEIGHT` for another canvas (multiples of 32, at most 768x1344 pixels in total, the video model's
released limit), `SEED`, or `IMAGE_SEED` and `VIDEO_SEED` separately. The script opens the video prompt with the line
MiniMax's own image-to-video prompts use; `RAW_PROMPT=1` passes yours untouched.

### Image only

```bash
bash scripts/image.sh "A neon shop sign that reads OPEN LATE, rainy night, reflections on wet pavement" out.png
TURBO=0 bash scripts/image.sh "..." out.png                 # 40 steps, base model
WIDTH=2048 HEIGHT=2048 bash scripts/image.sh "..." out.png  # sizes in multiples of 16
IMAGE_MODEL=flux3k-fast bash scripts/image.sh "..." out.png # FLUX3-K Fast, 16 steps (oneshot-setup.sh --flux3)
IMAGE_MODEL=flux3k-full bash scripts/image.sh "..." out.png # FLUX3-K Full, 50 steps at CFG 3.5
IMAGE_MODEL=flux3k-fast UPSAMPLE=0 bash scripts/image.sh "a full scene caption ..." out.png   # no prompt upsampler
```

### Edit a picture

```bash
bash scripts/edit.sh photo.png "The same statue at night under a full moon" night.png
LORA=~/ImaginationStudio/loras/qwen-multiple-angles.safetensors:0.9 \
  bash scripts/edit.sh photo.png "<mva> right side view, high-angle shot" side.png
SET_FILE=edits.json bash scripts/edit.sh photo.png          # [["prompt", "out.png"], ...] from one load
BASE=1 bash scripts/edit.sh photo.png "..." out.png         # 40 steps, base model (--image-edit)
IMAGES="person.png" bash scripts/edit.sh room.png "The person of image 2 sits on the sofa of image 1" out.png   # up to nine more, one a line
SIZE=model RESOLUTION=1024 bash scripts/edit.sh photo.png "..." out.png   # the model's own size; the default is the picture's own
IMAGE_MODEL=flux3k-fast IMAGES="person.png" bash scripts/edit.sh room.png "..." out.png   # FLUX3-K Fast (flux3k-full: Full), --flux3
```

The result has the size of the first picture (`WIDTH` and `HEIGHT` ask for another). The noise is drawn for `SEED`
and the pictures together, so the seed a picture was made with is as good as any other for editing it.

### Video only, or from your own image

```bash
bash scripts/video.sh "A hummingbird hovering over red flowers, soft wing hum" out.mp4
FIRST_FRAME=photo.jpg WIDTH=1344 HEIGHT=768 bash scripts/video.sh "She turns to the camera and smiles" out.mp4
QUALITY=high bash scripts/video.sh "..." out.mp4            # 20 steps, no adapter
```

`FRAMES` must be `17n + 5` (56, 73, 90, 124, 192, 243, 362). `FIRST_FRAME` is stretched onto the canvas, so match its
aspect ratio.

### A last frame: the clip ends on a picture

MiniMax H3's first-and-last-frame mode holds a picture at the end of the clip the way it holds one at the start: the
end keyframe is encoded as a condition latent at the clip's last latent frame and kept at every pass. `LAST_FRAME`
works with `FIRST_FRAME`, or alone (the clip then arrives at the picture). Beside a first frame the last one is fitted
by a centre crop, so give both the clip's aspect ratio. A keyframe is read by the model, not written into the clip:
in one production run 63 of 64 were held and one first frame was not, and FastH3 cut to a close-up inside 9 of 32
such clips. The app checks both after the render (see [the shot check](#what-the-app-tells-you-about-a-prompt-and-a-clip)).

```bash
FIRST_FRAME=teapot.png LAST_FRAME=bonsai.png bash scripts/video.sh "The teapot opens and a bonsai grows out of it" out.mp4
LAST_FRAME=bonsai.png bash scripts/video.sh "A potter's table; a bonsai is set down" out.mp4      # arrives at the picture
FIRST_FRAME=statue.png LAST_FRAME=statue.png WIDTH=768 HEIGHT=768 FRAMES=73 LORA="orbit.safetensors:1.0" \
  bash scripts/video.sh "One frozen instant. Only the camera moves. In a continuous 360 orbit. ..." out.mp4
```

In the app, step 2 of **Create** has a **Last frame** slot under the first frame. Ways to fill it: upload a picture;
*Pick from the Library*; *Same as the first frame* (an orbit or a loop); *From a clip in the Library* (its last
frame, its first frame, or the frame at a time you type: `POST /api/frame` saves the still to the Library at the
clip's own resolution); or *make one from a prompt* with Qwen-Image-2.1. Beside *Upload an image*, *Or continue from a
clip in the Library* puts a clip's last frame in as the new first frame; in the **Library**, *continue* on a video
and *last frame* on a picture do the same in one press. Step 4 opens the prompt with a line that places `<Picture 1>`
at 0.00 seconds and `<Picture 2>` at the clip's end (MiniMax documents that line for a first frame only; the two-picture
form here follows its wording and is our reading, and the keyframes are held whatever the line says). A LoRA from the
hub that needs both ends asks for the last frame in step 2, and *Queue this clip* waits for it. A job takes
`last_image` (a picture in the studio folder), `last_same` (the first frame again) or `last_frame_from` (a clip: its
first frame). Not for reference clips (a reference is not a frame of the clip) and not for videos longer than 15 s.

![The Last frame slot in step 2](samples/ui_last_frame.png)

Measured 2026-10-12, one seed each, 5 s at 864x480 unless noted; SSIM is the clip's final frame against the picture
it was given (0.3 to 0.5 is what an unrelated frame of the same scene scores):

| Machine | Engine | Request | Time | Final frame vs the picture | By eye |
|---|---|---|---|---|---|
| Mac | MiniMax H3, Turbo 5 passes | first + last | 105 s | 0.86 | the teapot becomes the bonsai and the clip ends on the picture |
| Mac | the same | first only (same seed) | 101 s | 0.46 | a tree grows, in another pot |
| Mac | the same | last only | 105 s | 0.86 | another teapot, arriving at the picture |
| Mac | FastH3 8 passes (MLX engine) | first + last | 103 s | 0.86 | as the first row |
| DGX Spark | FastH3 8 passes, TensorFold int8 blocks | first + last | 130 s | 0.90 | ends on the picture |
| DGX Spark | the same | first only (same seed) | 86 s | 0.32 | a tree grows, in the teapot |
| DGX Spark | the same | last only | 85 s | 0.90 | arrives at the picture |
| DGX Spark | the same, queued through the app's job API (`image` + `last_image`, prompt from `/api/compose`) | first + last | 126 s | 0.90 | ends on the picture |

![First and last frame, first only, last only (DGX Spark), last only and FastH3 first and last (Mac)](samples/last_frame_plain.jpg)

Two LoRAs from the hub that need both ends, same seed with and without, the authors' prompts and strength 1.0:

| LoRA | Machine | Engine | Time | With the LoRA | Without |
|---|---|---|---|---|---|
| 360° Orbit (first = last, 768x768, 73 frames) | Mac | MiniMax H3, Turbo 5 passes | 86 s without; with it the wall time was not clean (the run queued behind other renders) | a full orbit of the statue, back to the start | frozen |
| the same | DGX Spark | MiniMax H3 bf16, 25 passes (`SPARK_BASE=h3`) | 324 s | a full orbit | frozen |
| the same | DGX Spark | FastH3 bf16, 8 passes | 147 s | a full orbit | not run |
| Transformation (teapot to bonsai) | Mac | MiniMax H3, Turbo 5 passes | 108 s | the teapot dissolves into painted ribbons that form the tree | a cross-fade through a plant pot |
| the same | DGX Spark | MiniMax H3 bf16, 25 passes | 362 s | the same ribbons | a cut through another object |
| the same | DGX Spark | FastH3 bf16, 8 passes | 158 s | the same ribbons | not run |

![360° Orbit: Spark MiniMax H3 with and without the LoRA, Spark FastH3 with it, Mac with and without](samples/last_frame_orbit.jpg)
![Transformation: the same five rows](samples/last_frame_transformation.jpg)

On a Mac the zig engine does not hold an end keyframe, so a FastH3 clip with a last frame runs on the MLX engine
(the script says so). On a DGX Spark the last frame works on both block engines. Both LoRAs were trained on MiniMax
H3's own first-and-last-frame transformer, which a Spark install does not have by default (it renders on FastH3, where
both worked here): `bash oneshot-setup-spark.sh --h3-base` fetches it (40 GB) and `SPARK_BASE=h3` renders a clip on
it, 25 passes by default, on ComfyUI's own blocks with dense attention. Not tested: lengths other than 3 and 5 s,
sizes above 864x480, a last frame with the Qwen scout route in one job, and *make one from a prompt* in a browser.

### Another text encoder, your own size and passes

From the scripts, `TEXT_ENCODER` may be the path of another MiniMax H3 text encoder file (on a DGX Spark, the name
of a file in ComfyUI's `text_encoders` folder); unset, it is MiniMax's own. On the Mac FastH3 then runs on the MLX
engine, not the native one.
In the app, *Text encoder* is *Regular* (MiniMax H3's own), any file found in the `text_encoders` folder beside the
model (by its file name), or *Your own*: the path of a MiniMax H3 text encoder file on your machine (on a DGX Spark,
the name of a file in ComfyUI's `text_encoders` folder). The Studio ships MiniMax H3's own text encoder and no other;
it does not provide, check or recommend the file you choose. That file, its license and what you make with it are
your own choice and responsibility.

In the app, *Resolution → Custom* takes a width and a height (multiples of 32, each side 256 to 1536, at most
1344x768 pixels in all). A number of passes of your own, from 1 to 50, is the *Custom* card beside *20 passes* in each
passes choice (FastH3's *Passes*, the reference choice, and MiniMax H3's *Quality*, where it follows the two
20-pass recipes and keeps the one that was chosen); a number box opens under the cards. No time is estimated for either. From
the scripts these are `WIDTH`, `HEIGHT` and `STEPS` (FastH3, and everything on a Spark) or `POINTS` (MiniMax H3 on
the Mac: one more than the passes).

### Reference pictures to video (Ref2VA)

A first frame is the clip's opening picture. A reference picture is not in the clip at all: it fixes how a person or
a thing looks, and the prompt puts them in a new scene. This is MiniMax H3's second transformer, Ref2VA, installed
with `bash oneshot-setup.sh --ref2va` (62 GB; the text encoder and the two decoders are the ones already installed,
linked into `Ref2VA/`).

```bash
REF_IMAGES="face.jpg" bash scripts/video.sh \
  "Use <Picture 1> as the person. The person walks through a rainy neon street, turns to the camera and says hello." out.mp4
REF_IMAGES="face.jpg bike.png" bash scripts/video.sh "Use <Picture 1> as the person and <Picture 2> as the bicycle. ..." out.mp4
REF_TURBO=1 REF_IMAGES="face.jpg" bash scripts/video.sh "Use <Picture 1> as the person. ..." out.mp4   # 4 passes
```

- Up to nine pictures, named `<Picture 1>`, `<Picture 2>`, ... in the order given. Each is used at the clip's pixel
  area with its own aspect ratio (a 768x1024 portrait becomes 544x736 for an 864x480 clip), so nothing is stretched.
- 20 plain passes with the model's own sound by default: 3 min 43 s for 5 s at 864x480 on an M5 Ultra (9.7 s a pass
  over 16.2K rows, 64 GiB peak). `REF_TURBO=1` uses LightX2V's 4-step Ref2VA adapter and then makes the sound again
  without it: 1 min 28 s, 103 GiB peak while the adapter is merged. `QUALITY=high` runs the 20 passes with the fast
  recipe: 1 min 58 s. Two pictures (a person and a bicycle) took 4 min 5 s at 20 passes and 1 min 29 s with the adapter.
- Not with `FIRST_FRAME`, and not with FastH3, which has no reference-trained weights.
- **Your own number of passes:** `STEPS=12` (or `POINTS=13`, one more, which wins when both are set), and the
  *Custom* card of the passes choice in the app. It applies to every kind of reference, and with `REF_TURBO=1` too: the adapter then runs the
  number asked for instead of the 4 it was trained for, and the render says so. A reference render starts by naming
  the passes it will run, and the app's job card shows them. Before this, `STEPS` was read for FastH3 only and a
  reference render from the scripts ignored it; the app already converted its field.
- In the app: **The clip starts from → Reference pictures**. Step 4 writes the prompt in the model's own reference
  form (`subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, and from 2.6 the sound under
  its own `overall_soundscape` heading); edit it freely.
- **On a DGX Spark** the same `REF_IMAGES` and `REF_TURBO=1` work after `bash oneshot-setup-spark.sh --ref2va` (39 GB:
  the Ref2VA transformer for ComfyUI in bf16 and LightX2V's adapter). Measured on one Spark, 5 s at 864x480, one
  portrait: 20 steps 288 s, `REF_TURBO=1` (4 passes, the adapter's own sound) 107 s; the app's *Reference pictures*
  start works there too (101 s). The passes run on ComfyUI's own blocks, not on TensorFold's CUDA engine. Free memory
  fell to 7 GB of 128 during these renders, with a browser and the app open: close what you can first. One picture
  was tested there, not several.
- Checked on one fictional portrait: the same face, hair and glasses in the clip, against a different person from the
  same prompt and seed without the reference, and the same identity as h3.c's `--ref-image` gives. The token layout
  matches h3.c's to the last float32 bit. Not checked: sound by ear, lengths over 5 s. Reference videos and
  reference audio are the next section.
- **What the text encoder sees of a picture (corrected after 2.5).** A first frame or a reference picture also goes
  to the text encoder's vision tower. Up to 2.5 it was normalised there with the mean and deviation of the earlier
  Qwen-VL models, which minimax-h3-mlx builds its image processor with; MiniMax H3's own processor
  (`processor/preprocessor_config.json` in the checkpoint) uses 0.5 and 0.5, pixels to -1..1. The renderer now reads
  that file (0.5 and 0.5 when it is absent). For one 864x480 picture the values handed to the tower were up to 1.12
  away from MiniMax's processor before (0.39 on average, range -1.79..2.09) and are equal to it now, and to h3.c's
  `x * 2 - 1`. The video VAE's view of the picture, which carries most of the likeness, was never affected. Same
  prompt and seed, before and after, one first-frame clip (Turbo, 5 passes) and one reference-picture clip (20
  passes), 5 s at 864x480: a different take each time, as any change to the conditioning gives, with the same
  person, colours and sharpness; we could not see a gain or a loss. `EXTRA="--vision-stats mlx"` renders with the
  old values. This covers MiniMax H3 and FastH3 on the Mac, both engines; the DGX Spark build encodes text in
  ComfyUI, which already used 0.5 and 0.5. The sample clips and timings in this README from first frames and
  reference pictures were made with the old values: the timings stand, the clips would come out as other takes.

### Reference videos and reference audio (Ref2VA)

The same Ref2VA transformer takes two more kinds of reference, as MiniMax's model card describes: videos, which
carry a subject and how it moves, and audio clips, which carry a voice or music. **On a Mac** the passes need TensorFold's H3 family to pack these references and to hold reference audio rows, which is on
the engine branch `ref-video-audio` of the fork this pack installs from (pinned to `a2b592f` since 2.6.1). An install
made with 2.6 or earlier has the older engine, with which the renderer stops with a message saying so: run
`oneshot-setup.sh` again and it replaces the engine. Reference pictures alone are unaffected. **On a DGX Spark** they run now,
through ComfyUI's node: see [below](#reference-videos-audio-and-motion-on-a-dgx-spark).

```bash
REF_VIDEOS="baker.mp4" bash scripts/video.sh \
  "<Subject 1> is the man in the green apron in <Video 1>. ... the bakery is replaced by a market stall ..." out.mp4
REF_IMAGES="singer.png" REF_VIDEOS="baker.mp4" bash scripts/video.sh \
  "<Subject 1> is the woman shown in <Picture 1>. <Video 1> is the motion reference. ..." out.mp4
REF_IMAGES="singer.png" REF_AUDIOS="voice.wav" bash scripts/video.sh \
  "<Subject 1> is the woman shown in <Picture 1>. <Audio 1> is the voice timbre reference for her voice. ..." out.mp4
REF_VIDEOS="baker.mp4" REF_VIDEO_SOUND=1 bash scripts/video.sh "..." out.mp4   # the video's own sound is <Audio 1>
```

- **Limits (MiniMax's):** up to 9 pictures, 3 videos and 3 audio clips, 12 files in all; each video and each audio
  clip 2 to 15 seconds, and 15 seconds in all per kind; audio never alone (it needs a picture or a video beside it).
  The scripts and the app refuse a request outside these and say which file or count is wrong.
- **Names in the prompt:** `<Picture N>`, `<Video N>`, `<Audio N>`, each numbered in the order given. The model is
  always handed pictures first, then videos, then audio clips. With `REF_VIDEO_SOUND=1` every video's own soundtrack
  is a reference as well and those sounds are numbered first: with one video, its sound is `<Audio 1>` and the first
  `REF_AUDIOS` clip is `<Audio 2>`. Without it a video's sound is ignored. The render prints the name of every file.
- **What is read:** a video at 24 fps up to the clip's own length, cut down to `17 n + 5` frames, at its own aspect
  ratio and brought down to the clip's pixel area (`REF_VIDEO_SIZE=full` keeps the model's 768-pixel canvas; not
  tested). Audio at 32 kHz stereo, cut to the clip's length. `ffmpeg` and `ffprobe` do the reading.
- **Cost:** every reference latent rides through every pass. Measured on an M5 Ultra, 5 s at 864x480, 20 passes:
  one 5 s reference video 12 min 32 s (34.9 s a pass over 33.2K rows), a picture and a 5 s video 13 min 36 s, a
  picture and a 5 s audio clip 4 min 10 s (11.3 s a pass over 17.0K rows), against 4 min 6 s with that picture alone;
  64 GiB peak in each. `REF_TURBO=1` with a picture and an audio clip: 1 min 35 s, 103 GiB peak. A shorter reference
  video costs less; none was timed.
- **In the app:** *The clip starts from → References*; step 2 takes pictures, videos and audio, shows each one's name,
  and step 4 writes the prompt in the model's own form (`<Video 1> is the reference video for ...`,
  `<Audio 1> is the voice timbre reference for ...`). No time is estimated when a video or an audio clip is used.
- **On a DGX Spark** the same variables, limits and messages: see the next section.
- **How it was checked.** The token layout (row order, positions, which rows are held) is equal to the last float32
  bit to MiniMax's own `build_ref2va_packed_sequence` and to h3.c's `h3_layout_build`, on six mixed requests. The
  prompt's token sequence equals MiniMax's `build_ref2va_presentation`; the frame sampling for the text encoder,
  the frames kept for the video VAE and the frame size rule equal MiniMax's functions. Reference rows decode back to
  what went in (video 38.7 dB PSNR, audio loudness envelope correlation 0.999). Two differences from MiniMax's
  pipeline remain, both shared with the picture path: the video latent is the middle of the encoder's distribution,
  not a draw from it, and a reference is not enlarged to the 768-pixel canvas.
- **What the renders showed** (fictional people from earlier renders of this pack; 5 s, 864x480, 20 passes, one seed):
  - *A reference video* (a baker lifting a loaf in a bakery), with a prompt that moves him to a market stall: the
    same man, clothes and motion, second for second, in front of a market. The same prompt and seed without the
    video gave another man holding the loaf up throughout.
  - *A picture and a video* (a woman's still, the baker clip as motion reference): the woman, in the baker's
    bakery, doing his motion second for second. Her mouth also moves as his does, though the prompt said nobody
    speaks.
  - *A video with its own sound* (`REF_VIDEO_SOUND=1`): the clip's sound follows the reference closely (pitch 120 Hz
    against 122, spectrogram correlation 0.86 moment by moment, against 0.43 for the run without the sound). With
    the app's prompt and a picture beside the video: 0.99 at 20 passes. **With `REF_TURBO=1` this was broken until
    the engine's re-voice was corrected** (branch `ref-video-audio`, `a2b592f`): the sound made again after the
    adapter's passes came out near silent, correlation 0.22, where the adapter's own sound had 0.98. The audio-only
    re-voice kept the held rows beside the clip's finished audio; with reference audio in the sequence they are now
    kept beside the starting noise: 0.98, same time. A voice clip as `<Audio 1>` through Turbo was not affected
    (139 Hz and distance 6.7 after, 140 Hz and 6.6 before; 193 Hz and 13.8 without the clip).
  - *A picture and an audio clip of the same kind of voice* (a woman's still, 6 s of a woman singing, the prompt
    asks her to sing): median pitch 281 Hz, the reference's own 281, against 314 without the clip; closer in
    timbre statistics as well (4.9 against 6.7).
  - *A picture and a voice that does not fit it* (the woman's still, a man's voice as `<Audio 1>`): **it carries
    when the prompt describes the voice, and not when the prompt is silent about it.** Measured against a clean
    5.8 s male voice at 117 Hz (the macOS speech voice "Daniel"; distance = MFCC statistics to the reference, lower
    is closer; 20 passes, one seed):

    | prompt | with the clip | same prompt without it |
    |---|---|---|
    | says only "the voice timbre references `<Audio 1>`" | 225 Hz, distance 14.1 (no carry-over) | 182 Hz, 11.7 |
    | also calls it a deep male voice (written by hand) | 137 Hz, 5.0 | 169 Hz, 11.6 |
    | the app's prompt with *Voice* = "a deep male voice" | 130 to 134 Hz, 4.1 to 4.8 | 141 Hz, 10.1 |
    | one plain sentence: "speaks with the voice in `<Audio 1>`, a deep male voice" | 132 Hz, 3.9 | not run |

    The other way round (a man's still, a woman's voice at 180 Hz, the prompt silent about the voice) it carried
    as well: 174 Hz and distance 4.3 with the clip, 143 Hz and 10.6 without. The first test of this pack found no
    carry-over because its prompt did not describe the voice and its reference was a noisy clip with a voice in
    a fifth of it; with the voice described, that same clip carries too (117 Hz against its own 122, distance 6.0
    against 15.9 without). So: **describe the voice in the line's *Voice* field as it sounds in the clip, and use a
    clean recording.** The pitch and the tone come close to the reference; none of this was judged by ear, and it
    is not an exact copy of a voice.
- **Not checked:** sound by ear (the figures above are measurements: pitch by autocorrelation, MFCC statistics,
  spectrogram correlation), more than one reference video or audio clip in a request, two seeds, lengths over
  5 s, references longer than the clip, `REF_VIDEO_SIZE=full`, `QUALITY=high` with a video, the app's screens by eye
  in a browser (its checks and the prompt it writes were exercised through its API, two clips were rendered
  through its queue, and the Create page's choosers and reference step were driven in a scripted DOM, not looked
  at).

### Reference videos, audio and motion on a DGX Spark

`REF_VIDEOS`, `REF_AUDIOS`, `REF_VIDEO_SOUND=1` and `STEPS` work on the Spark as on the Mac, in the
scripts and in the app, after `bash oneshot-setup-spark.sh --ref2va`: the same limits, checked before any model loads,
with the same messages. `spark/spark_generate.py` reads each reference with `ffmpeg` (a video at 24 fps, up to the
clip's own length, LANCZOS-scaled to its own aspect ratio and brought down to the clip's pixel area, stored without
loss; sound at 32 kHz stereo, cut to the clip's length) and hands it to ComfyUI's `MiniMaxH3ReferenceToVideo` node
(`ref_videos`, `ref_video_audios`, `ref_audios`), which numbers pictures, videos and audio clips as the Mac build
does. The passes run on ComfyUI's own blocks in bf16. Scaling the video before the node sees it matters: left alone,
the node takes a reference at up to 768 pixels on the short side whatever the clip being made, several times the
rows; for that reason `REF_VIDEO_SIZE=full` is refused on a Spark. The reference files are taken out of ComfyUI's
input folder again after the render. Nothing here needs the TensorFold engine change the Mac build is waiting for.

Measured on one DGX Spark (GB10, 121 GB usable), 5 s at 864x480, seed 7, fictional people from earlier renders, a
browser and the app open, each render under a watcher on free memory; "lowest free" is the lowest `MemAvailable`
seen during the render:

| Render | Passes | Time | Lowest free |
|---|---|---|---|
| One picture (as in 2.5, to see nothing changed) | Turbo 4 | 100 s | 39.8 GB |
| A picture and a 2.3 s reference video | Turbo 4 | 147 s | 39.1 GB |
| A picture and a 5 s reference video | Turbo 4 | 202 s | 38.3 GB |
| A picture and a 5 s audio clip | Turbo 4 | 98 s | 39.1 GB |
| Wizard through the API: a picture and the source clip, full scene | Turbo 4 | 208 s | 37.8 GB |
| Wizard through the API: a picture and the skeleton layer | Turbo 4 | 187 s | 37.8 GB |
| Wizard through the API: a picture and the depth layer, actor motion | Turbo 4 | 193 s | 15.4 GB (see below) |
| A 5 s reference video alone (the 8-bit transformer) | 20 | 677 s | 57.1 GB |

A 5 second reference video about doubles the time of a Turbo clip and costs under 2 GB more memory at this size; at
20 passes a pass over the clip's and the video's rows took 30.7 s (613 s of passes), the reference's encoding 46 s.
What the clips showed, besides the table: the 20-pass clip has the baker of the video, his clothes and his motion in
front of a market stall, as on the Mac. With the Turbo adapter: the woman of the picture doing the
baker's motion second for second in his bakery; the martial-arts shot repeated move for move with the woman of the
picture in it; the skeleton layer steering the scale and the camera with no skeleton look in the clip. The audio
clip (a man's spoken line beside a woman's picture) gave **no carry-over we could measure**, as on the Mac: her voice
at 192 Hz with the clip and 195 Hz without, the reference near 110 Hz.

The depth layer with a picture of the new person kept the stances and the punch toward the camera in a rooftop
drawn afresh from the prompt, with no depth-map look.

**Memory after a FastH3 clip.** The depth row, and a repeat of the source-clip row (210 s, 14.0 GB), ran right after
a FastH3 clip: the server then still holds what ComfyUI's unloading does not give back (TensorFold's engine keeps its
int8 weights), and the reference model loads on top. That is the 7 GB figure of 2.5 as well. A reference render
that follows a FastH3 clip now restarts ComfyUI first ("a reference render starts from a server with nothing
loaded"): the same picture and 5 s video then ran in 213 s, restart included, with 37.9 GB lowest free.

**What a sound reference does (measured on the Spark, 2.6).** Same picture, prompt and seed with and without the
reference, 5 s at 864x480, Turbo 4 passes unless said; pitch by autocorrelation, likeness as the correlation of the
two spectrograms moment by moment (two unrelated spoken clips measure about 0.3), words by a speech-to-text pass:

| Request | With the reference | Without it |
|---|---|---|
| A woman's picture, a recording of a woman's voice, the prompt's line is what the recording says | the recording itself: likeness 0.98, pitch 229 Hz (the recording's 229) | 188 Hz, likeness 0.28 |
| The same picture, a man's recording, the prompt's line is what he says | the recording itself: likeness 0.98, pitch 113 Hz (his 122) | not run |
| The same picture, the man's recording, another line | nothing carried: 188 Hz, likeness 0.11; the prompt's words | 188 Hz |
| The same picture, the man's recording, another line, and the voice described too ("in the deep male voice of `<Audio 1>`") | pitch 122 Hz (his 122), timbre distance 8.5; the prompt's words | 156 Hz, distance 13.4 (the description alone) |
| The same picture, the woman's recording, another line | pitch 229 Hz (the recording's), but the words came out garbled | 183 Hz, the words right |
| A man's picture, his own recording, another line | pitch 130 Hz (his 122); the prompt's words | 147 Hz |
| A video with its own sound (`REF_VIDEO_SOUND=1`) | likeness 0.81, loudness envelope 0.90, pitch 115 Hz (the video's 122); the prompt's words | likeness 0.34, envelope 0.48, 131 Hz |
| A picture, a video with its own sound, and a voice recording (`<Audio 2>`) | her voice at 216 Hz (the recording's 229), the prompt's words; neither sound reproduced | not run |

So a reference audio clip is a **performance to repeat** more than a voice to borrow: when the spoken line is what the
recording says, the clip plays the recording back with the mouth moving in time with it (mouth movement against
speech, from face landmarks: r 0.55 to 0.59, as for clips with the model's own voice), whoever is in the picture.
With other words, the voice comes across when the prompt describes it as well as naming the clip (the pitch went to
the recording's); named without a description, a voice unlike the pictured person changed nothing, and with the
Turbo adapter a voice of the same kind blurred the words. It is not voice cloning, and the app says so where the clip is added. A video's own sound carries
as on the Mac. *The videos' own sound* stays **off** by default in the app: the box swaps the sound written in step 3
for the video's own, which has to be a choice.

**A voice nobody asked for.** Up to this change the app wrote the soundscape on the end of the shot description of
a reference prompt. With some pictures the model then read it out: a clip of a woman drinking coffee, whose prompt
says that nobody speaks, had a voice talking nonsense in 41 % of its frames (63 % on a second seed, 49 % at 20
passes), not in time with her mouth. With the same words under an `overall_soundscape:` heading, as text-to-video
prompts have always had, the voice is gone (0 % on both seeds, the room tone at -40 dBFS), and a spoken line is still
spoken as written. The app now writes the heading. Guidance was tried as well (ComfyUI's CFG at 5 with an empty
negative): it also removes the voice but burns the picture with the 4-step adapter and doubles the time, and at 2 it
does nothing; the reference model is run without guidance, as MiniMax ships it.

**The sound of every clip on a Spark** is now made from a lossless copy of the decoder's output (it used to be
ComfyUI's own AAC track at about 130 kb/s): 256 kb/s, cut to the picture's length, and limited to -1 dBFS when the
decoded peak is higher (it usually is: speech comes out of the decoder touching full scale, and the lossy track
then overshot it, 31 samples over full scale in one test clip). The render says what it found
(`[tensorfold] sound: peak -0.5 dBFS as decoded, limited to -1 dBFS`); `AUDIO_EQ=off` keeps ComfyUI's track. Every
clip of this section was checked for sample rate (32 kHz stereo), length against the picture (within one frame),
start offset (0), silence, channel balance (within 0.6 dB), clicks at the ends, and full-scale samples (none after
the change).

More of the 2.6 surface, on the same machine (Turbo 4 passes, 864x480):

| Render | Time | Lowest free |
|---|---|---|
| Two 5 s reference videos (a man from one, a street and its camera from the other) | 373 s | 33.8 GB |
| An 8 s clip from a picture and an 8 s source shot, full scene, through the app | 345 s | 30.3 GB |
| A picture, a 5 s video with its own sound and an audio clip | 229 s | 33.2 GB |
| The 8 s clip again with the shot's own sound as a reference: the clip's sound is the shot's, likeness 0.96 (0.02 without) | 344 s | 33.4 GB |
| Object motion: the depth layer of a ball rolling over a table, no picture, a prompt for an apple on marble | 208 s | 38.1 GB |
| Camera only: the depth layer of a street shot, no picture, a prompt for an empty canyon | 198 s | 36.9 GB |

The object-motion clip has the apple on the ball's path over a table of the same shape in a new kitchen; the
camera-only clip has a canyon with the street's layout and no person, with a boulder where the figure stood (a depth
layer shows the figure, so "camera only" keeps its place filled). The same request with the 4-step adapter at 4, 3
and 2 passes (*Custom*; a picture and a 5 s reference video) took 219, 191 and 156 s; the three clips are the same
shot to the eye, equally sharp (median Laplacian variance 70, 73, 84) and equally close to the reference's motion
(pose distance 1.1 % of the frame width in each), so 2 or 3 passes are worth trying for drafts; one request, one seed.

**The shared motion test** (two fast martial-arts shots, each as *Full scene* with the source clip and as *Actor
motion* with the depth layer and a new setting; Turbo 4 passes, seed 7, the wizard's own prompts, the same on both
builds): on the Spark 202 to 216 s a clip and 29.9 GB lowest free. Pose distance from the source, as a share of the
frame width: 2.9 % median (7.7 % at the 90th percentile), 3.2 % (8.1 %) and 3.2 % (7.6 %) for the first three; the
fourth, two fighters in a new courtyard, follows by eye, and the checker, which tracks one person, picked the other
fighter (its 28 % is not a measure of that clip).

Not tested on the Spark: larger sizes than 864x480 with a reference video, clips over 8 s with one, three reference
videos at once, and sound by ear (the figures above are measurements).

### GHCR prebuilt carrier

```bash
docker pull ghcr.io/drowzeys/keys-tensorfold-studio:2.9.1
docker run --rm -v "$PWD":/out ghcr.io/drowzeys/keys-tensorfold-studio:2.9.1 cp -a /payload/. /out/payload/
```

One image for both builds. For a Mac it holds the TensorFold wheel, `requirements.lock`, the render scripts, the
prebuilt native FastH3 engine (`zig-engine/tf-h3-dit`, built for Apple Silicon at the pinned commit) and `SHA256SUMS`.
For a DGX Spark it holds the prebuilt CUDA engine (`spark-engine/libtf_h3.so`, GB10, aarch64; since 2.8.0 the
`h3-cuda-lora` build at `1f7ecc75`, with LoRA support, so a Spark install no longer builds it from source), which
`spark/build-engine.sh` takes when Docker is there and otherwise builds from source; since 2.9.1 also MiniMax Music 3's
`spark-music3/music3-generate` (`music3-cuda` at 6b714531), which `spark/build-music3.sh` takes the same way. The Mac's
Drama 3 and Music engines are not in it: their installers clone TensorFold at the pinned commits. **It is not a runtime**: neither
Metal nor the Spark's models run in a container, so the setup installs natively. Rebuild it with
`PUSH=1 bash scripts/build-carrier.sh` (on a Spark, after `spark/build-engine.sh` and `spark/build-music3.sh`, to include its engines). Up to 2.2
the image was `ghcr.io/drowzeys/keys-mac-tensorfold-studio`; the Mac setup still falls back to it.

## Stack

| Piece | Value |
|---|---|
| Host | Mac Studio M5 Ultra, 256 GB, macOS 27.0.1 |
| Engine | TensorFold 0.6.5 (`609ca419`) + twelve commits, `drowzeys/TensorFold` branch `studio` @ `a2068c031e08109a0ec14c26b1ca655cf50ac34c` (Apache-2.0) |
| Native engine (FastH3) | TensorFold 1.0.4's Zig runtime + an H3 family for Metal and one for CUDA, `drowzeys/TensorFold` branch `h3-1.0.4` @ `b7e309e042e48b72e75e9fb87342df4e9bf22f37` (the DGX Spark's CUDA
library: branch `h3-cuda-lora` @ `1f7ecc75`, which adds `tf_h3_set_weight` for LoRAs); built with Zig 0.17. Same output bit for bit as the 1.0.2-based engine of 2.0 to 2.2 |
| Image model | `Qwen/Qwen-Image-2.1-Turbo`: 7B transformer (32 blocks, bfloat16), 64-channel VAE, Qwen3-VL text encoder; 8 steps on the schedule saved with the checkpoint |
| Image adapter (installs from before 2.3) | `Viggle/Qwen-Image-2.1-viggle-turbo`, v0.3, rank 256, 6 steps on its trained nodes, on `Qwen/Qwen-Image-2.1` |
| Video model | `MiniMaxAI/MiniMax-H3`, `FL2VA` partition: 33B transformer, Qwen3-VL text encoder, video and audio VAEs |
| Video adapter | lightx2v MiniMax H3 Turbo v1.0, runner layout as published by Phosphene |
| 2x video decoder | `speach1sdef178/MiniMax-H3-X2-Detail-VAE`, `MiniMax-H3-X2-Detail-v1.safetensors` (decoder only; its reference-detail branch is not used) |
| Borrowed at run time | mflux @ `add5164e`: Qwen-Image prompt encoder. minimax-h3-mlx @ `79190205`: H3 text encoder, first-frame encoder, audio decoder, MP4 writer |
| MLX | 0.32.3 |
| Peak memory | image 28 GiB at 1344x768, 47 GiB at 2560x1472; FastH3 28 to 32 GiB; MiniMax H3 Turbo 103 GiB during its adapter merge ([table](#minimum-requirements)) |

## Notes

- **Licences decide what you may do with this.** Qwen-Image-2.1-Turbo (and Qwen-Image-2.1 with the Viggle adapter) are under the **Qwen Research
  License Agreement: non-commercial research and evaluation only**; commercial use needs a licence from the Qwen
  team. MiniMax H3 and the 2x decoder derived from it are under the MiniMax H3 Community License, which excludes some
  territories. This pack ships no
  weights. Read both licences before downloading.
- **This is a development render path, not a product.** There is no `tensorfold generate` command and no server; each
  script loads its model from disk and exits. The two models run one after the other, never together.
- **What TensorFold runs and what it borrows.** Both transformers, both samplers, the adapter merges, the int8
  kernels, the image decoder and the video decoder are TensorFold's. The Qwen-Image prompt encoder is mflux's, and
  the H3 text encoder, first-frame encoder, audio decoder and MP4 writer are minimax-h3-mlx's.
- **Qwen-Image here is text to image only.** Editing, reference images, transparent output and guidance with a
  negative prompt are not ported; mflux has them.
- **Sound (1.5).** With the Turbo adapter the sound was judged poor by ear: harsh on speech, worse on singing, with an
  echo-like quality. Measurements agree on one cause: the model makes two audio channels, which agree at 20 steps
  and disagree under the adapter. More passes, a mono fold-down, level changes and a different audio schedule did
  not fix it. Making the sound again with the base model did, and is now standard (`REVOICE=0` keeps the adapter's
  sound). The fast audio step is not identical to recomputing the whole sequence each step: on one clip the two
  gave the same loudness contour and tone but not the same waveform. Lip-sync was judged by eye, not measured.
- **Levels.** The audio decoder's hard clip at full scale is lifted and an overshooting take is turned down as a
  whole. A take whose 120-300 Hz band is weak gets a low shelf of up to 6 dB (`AUDIO_EQ=off` skips it); re-voiced
  takes have needed 2.5 to 5 dB.
- **Passes.** On a 15 second (362 frame) clip, 3 passes lost a detail (headphones) that 4, 5 and 6 kept, and 5 was
  preferred by eye. On an 8 second (192 frame) clip 3 passes kept it. `POINTS` sets passes plus one.
- **Quality is not graded.** Checked: stills of every clip, the scripted line by speech recognition, one image prompt
  against the mflux reference. Not checked: motion and audio by eye and ear, lip-sync, text rendering in general
  (the chalkboard reads "FRESH TODAY" at 1344x768 and comes out garbled at 864x480), other prompts and seeds.
- **Turbo adapters are distilled models.** They trade some fidelity for speed; `TURBO=0` (image) and `QUALITY=high`
  (video) run the base models.
- **M5 only for these numbers.** The int8 kernels need Metal 4 tensor operations; elsewhere both families run
  bfloat16 and slower.
- **Memory.** `--image-only` asks for 48 GB; the default install 64 GB; `--turbo` and `--ref2va` 128 GB. Measured on 256 GB only; see [Minimum requirements](#minimum-requirements).
- **Not part of upstream TensorFold.** The H3 family, the Qwen-Image family and the audio step were offered to
  ashhart/TensorFold as draft pull requests (#384, #393, #405) and closed on 2026-10-07: the engine is built around
  token lanes with exact output, it does not trade precision as the int8 kernels do, its Python engine is frozen and
  its new Zig engine drives Metal without MLX. This code lives in the `drowzeys/TensorFold` fork, on 0.6.5.

## Credits

Imagination Studio is by **Keys (drowzeys)** and **Sam Wasserman and Wasserman Productions** (Wasserman's Filmmaker
Suite, and the studio's design with Keys); see [License & credits](#license--credits). Cite the original authors first: the Qwen team (Qwen-Image-2.1), Black Forest Labs (FLUX 3 Image Klein, the FLUX3-K
picture models) and felix-ab (flux3-mlx, their Mac runtime), OpenMOSS (MOSS-Image), the MiniMax team (MiniMax H3, MiniMax-Music3), Ash Hart and the
TensorFold contributors, antirez (h3.c), RobZombAI (H3MLX), mrbizarro (minimax-h3-mlx, Phosphene), Filip Strand and
the mflux contributors, Viggle, speach1sdef178 (the 2x decoder), LightX2V, NVIDIA Research, FastVideo, and Apple MLX and its contributors. Full list:
[CREDITS.md](CREDITS.md). Pack: drowzeys / keys, with Wasserman Productions.
