# LoRAs and training

[Back to the README](../README.md)

Adapters, the LoRA Hub and training recipes. Model licences and supported backends still apply.

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
Multiple Angles is a LoRA for the image model: it is used in *Edit a picture*, not on a clip.

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
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git && cd Imagination-Studio-by-keys-Wasserman
bash oneshot-setup.sh --no-render --no-loras        # the Studio (oneshot-setup-spark.sh on a DGX Spark); model weights as listed under One-shot
bash oneshot-essential-bundle.sh --json > lora-bundle.json; echo "exit $?"   # 0 all present, 1 some unavailable (listed), 2 usage or disk
bash oneshot-essential-bundle.sh --verify --json    # fetches nothing: "present", "unavailable", "gated", and "menu" (group -> LoRAs -> state)
```

Expect 4.8 GB and 20 of 22 present without a Hugging Face token (`gated` lists the two an author gates), in 90 to 120 s
at 40 MB/s; measured 119 s on the DGX Spark and 86 s on the Mac, a second run 2 s, an offline run from a copied bundle
3 s. Set `HF_TOKEN` (or sign in with `hf auth login`) for the gated ones once their authors have approved the account.
## Train your own LoRA

**Looks → Train a LoRA** turns your own clips or stills into a LoRA for the video model: a **look** (a colour, a grain,
a kind of light) or a **character** (one person or creature, the same in every shot). It trains on a DGX Spark, and
the result appears in Looks under **My LoRAs**, ready to use on a clip like any other look.

1. Pick at least 8 clips or stills from the Library (each clip 5 seconds or longer; clips and stills can be mixed).
2. Give it a name and a **trigger word**, one word your prompts will use (`wk_noir`). Choose a look or a character.
3. Choose where the captions come from: **Automatic** uses the prompt each clip was made from, else the Library's
   caption of it, else the trigger alone; **Prompts only**; or **Trigger only**. Every caption starts with the trigger;
   a character's also says "a video of" or "a photo of" it.
4. Set the length (300 to 3000 steps, 1000 by default) and press **Start training**. The page says how long it takes:
   about 110 seconds a step at the standard size (928x512, 124 frames), so 1000 steps is about 30 hours.

While it trains the Spark does nothing else: ComfyUI is stopped, the brain engine, Drama 3 and Music 3 are unloaded,
and the run holds the GPU lock and about 55 GB of memory, with the memory guard beside it. The run is a job of the
queue with its step count; a checkpoint is saved every 100 steps, so a run you cancel (or one the memory guard stops)
**resumes** from its last checkpoint. When it ends, the LoRA is written to `~/ImaginationStudio/loras/` in the layout
ComfyUI reads, and its card in Looks has **Try it**, which queues a short clip with it on.

**Needs a DGX Spark.** Training is not offered on a Mac. Install it with `bash oneshot-setup-spark.sh --train`; it also
runs with `--all`.

Agents can do the same: `train_lora {name, trigger, kind, items, steps}` and `training_status {id}` in the Studio's
MCP tools. Over HTTP: `POST /api/train/datasets {name, trigger, kind, items, captions}`, then
`POST /api/train/start {dataset, steps, rank, lr, size, save_every}` (or `{resume: <lora id>}`); `GET /api/train`.

**What it needs, and the disk** (`bash oneshot-setup-spark.sh --train`, or `bash spark/setup-train.sh`; `--verify`
checks; an 8 GB environment, two files of about 6 GB and, when `--h3-base` has not installed it, its 40 GB transformer):
kohya-ss's [musubi-tuner](https://github.com/kohya-ss/musubi-tuner) at `f8a1b03` (Apache License 2.0) in its own
environment under `$PREFIX/train`; MiniMax H3's FL2VA transformer (what `--h3-base` installs, fetched if missing);
MiniMax H3's video encoder in fp16 from Comfy-Org/MiniMax-H3 and ostris's de-distillation adapter v2
(ostris/minimax_h3_training_adapter), the two files, in `$SPARK_MODELS/Train-H3`. The adapter is merged under the LoRA
while it trains and is not used when it renders. MiniMax H3's files are under the MiniMax H3 Community License; the
adapter is made from MiniMax H3. The LoRA you train is yours; what it may be used for follows that licence.
