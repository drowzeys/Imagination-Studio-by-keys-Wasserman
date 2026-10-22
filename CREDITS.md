# Credits

**Imagination Studio is by Keys (drowzeys) and Sam Wasserman / Wasserman Productions.** Attribution required: per the
NOTICE file (Apache 2.0 §4(d)), any use, fork or redistribution keeps the NOTICE and credits Keys (github.com/drowzeys)
and Sam Wasserman / Wasserman Productions (wassermanproductions.com) in its documentation and about/credits surface.

**Thank you.** Almost everything here is other people's work. Keys / drowzeys ported pieces into two TensorFold
families, wrote the int8 tensor-unit kernels with their designs as the guide, pinned it and measured it on a Mac
Studio M5 Ultra. Cite the authors first.

## The studio itself: Keys (drowzeys)

**Keys (drowzeys)** built this studio: the app and its scripts, the Mac and the DGX Spark builds and the carrier
image, the TensorFold families for Qwen-Image and MiniMax H3 with their int8 tensor-unit kernels, the native and CUDA
engines the studio runs (FastH3 on Metal, H3, Drama 3 and MiniMax Music 3 on CUDA, and Music 3 on Metal), and the
measurements every setting here is held to. Imagination Studio is **by keys+Wasserman**.

## The studio's design and Wasserman's Filmmaker Suite: Sam Wasserman and Wasserman Productions

**Sam Wasserman and Wasserman Productions** helped design and build Imagination Studio in collaboration with Keys, and
made **Wasserman's Filmmaker Suite**, the apps the Studio's suite vault
brings work in from and sends work out to, each under its own licence (as its repository states):

- **ScriptBreak** (screenplay breakdown and prompt packs) — Apache-2.0
- **Cork Board** (index-card story structure) — Apache-2.0
- **Master Canvas** (the pre-production canvas), and its MCP server — MIT
- **Storyboard Reference Studio** (reference frames into storyboard stills) — Apache-2.0
- **Blockout** (grey-box 3D previs), and its MCP server — Apache-2.0
- **Motion Previs Studio** (motion, depth, pose and camera from a clip), and its MCP server — Apache-2.0
- **Slate** (the prompt studio for AI filmmaking) — Apache-2.0
- **Circle Take** (AI dailies) — Apache-2.0
- **Stem Studio** (dialogue, music and effects stems) — Apache-2.0
- **Call Sheet** (the suite's launcher and dashboard) — Apache-2.0
- **DaVinci MCP** (an agent drives DaVinci Resolve) — Apache-2.0

The Studio reads and writes their files and ships none of their code, except where a section below says so (the
Slate workflow and its files in `app/static/slate/`, and the Motion Previs analysis port, each with its NOTICE). Sam
Wasserman's own art covers the engines, the start, action and vault cards and the look swatches; the Motion Previs
action's card was rendered with the studio's own FLUX3-K and Qwen 2.1 models, and the home key art was generated with the
image tool of Sam Wasserman's ChatGPT desktop app (OpenAI). Every picture's source is in `app/static/art/ART-SOURCES.md`.

He also wrote **More local agents** (#114): every OpenAI-compatible model server you run, on this computer, the LAN
or the tailnet, is an agent of its own beside Bolt and the subscriptions, with Find servers on this computer, a card
in the Agent room and a place in Slate's brain picker. And the **memory guard's cold-load fix** (#117): a cold start's
model load, which stalls on the page cache with half the machine free, is no longer stopped (a 2-minute clip was
stopped so with 62 GB available).

## The film workflow in Production: Sam Wasserman's Slate

**Sam Wasserman (wassermanproductions.com)** created **Slate**, the prompt studio for AI filmmaking: projects, scenes
and shots, the six-section shot prompt, the Bible, Casting, the Art Department, Locations and the Lookbook, coverage
plans, setups, the brain's transforms, Director's Notes and the First AD. The *Wasserman Slate AI Studio* inside the
Production tab is a port of that workflow. The project file is Slate's own `project.json`; the brain prompts and the
First AD's action contract are Slate's; `app/static/slate/` holds Slate's coverage plans, setups and model profiles
unchanged, with its NOTICE. What this pack adds is the render half Slate leaves to a generator: a compile to MiniMax
H3's prompt form, the queue, and takes with their clips. Slate does much more than this port: sequence chunks, alt
takes, punch-ups, the tone dial, reference and audio analysis, the stills library, per-model compiles for a dozen
generators, and its MCP server. (Apache-2.0.)

The **Wasserman Sharp Spec** button and the QA gate behind it are his too: the locked recipe (1376x768, FastH3 at 8
passes, seed 42, one pass with no upscale) and the two gates, a transcript of the spoken line and a three-frame
contact sheet checked by a vision model, with one retake at the next seed. `app/qa.py` is this pack's writing of them.

The first preset of the *Keys and Wasserman LoRA Hub* is his **80s Horror LoRA** (Wasserman 80s Horror LoRA, a style
LoRA for MiniMax H3). The pack holds only its recipe; the weights are downloaded from his release when a user installs
them, as a Model Derivative of MiniMax H3 under the MiniMax H3 Community License Agreement. 80s Horror LoRA by Sam
Wasserman (wassermanproductions.com). Powered by MiniMax H3.

## The LoRA hub's community presets: their authors

From 2.6.3 the hub lists twenty-one community LoRAs in seven groups, twenty for MiniMax H3 and one for Qwen-Image-2.1. Each is its author's own work, under
that file's own licence. The pack holds only their recipes. The essential bundle first takes the LoRAs whose stated licences allow passing them
on (14 of the 22) from one hosted copy, `huggingface.co/drowzeys/keys-studio-lora-essential`, and every other file from
its author's page; each keeps its author's model card and licence beside it.

- **Camera & Viewpoint:** Camera Motion by Jojocodex; Head-mounted FPV by neph1; Equirectangular 360° by shamanic;
  360° Orbit by pablodawson.
- **Transformation & Morphing:** Transformation by Ashmotv; Turnaround by matlod; Keyframe Animation by alvdansen.
- **Character Editing:** Character Swap and Person Remover by Akatz Labs (akatz-ai); Natural Face Speech by vpakarinen.
- **Motion & Action:** Motion Continuity Repair, Combat Base V2 and Weapon Combat by JOKER141; Spatial Physics by
  Jojocodex.
- **Art Style:** Anime Slider by adf99; Lineart Anime by DiffSynth-Studio; 3D Animation Style by SOLRICKS.
- **Support & Finishing:** Video Upscaler LoRA by dntpi (with the latent upscaler by LBH-123-AI it builds on).
- **Fun:** VH5 Tape (VHS) by KennethFal; Wushu Action by Jojocodex; Multiple Angles, for Qwen-Image-2.1, by akhaliq.

The MiniMax H3 ones are Model Derivatives of MiniMax H3. Powered by MiniMax H3.

## The Library's actions: the people whose workflows they run

Three of those LoRAs are not for making a clip. Each has a workflow of its own, built by its author, and the Library
runs that workflow as an action. On a DGX Spark it is the author's own ComfyUI nodes, cloned from the author's
repository at a pinned commit when the Studio is installed; none of their code is in this pack.

- **Turnaround sheet:** the Turnaround LoRA by matlod and the contact-sheet sampler and ComfyUI nodes by MATLOWAI
  (H3 Contact Sheet, GPL-3.0). The idea is theirs: five still pictures packed where a five-step video would go, so
  the model's consistency along time becomes consistency between views. On a Mac the MLX engine packs and decodes
  the five views the same way (`h3_generate.py --sheet`), written from their description of it.
- **Upscale:** the Video Upscaler LoRA and the H3 Video Upsampler node by dntpi (Sandpies), MIT; the latent upscaler
  network it enlarges with by LBH-123-AI (weights Apache-2.0); the upscale recipe it follows by bbaudio-2025; the
  guide layout and the training set of the LoRA by Alissonerdx.
- **Remove a person:** the Person Remover LoRA and H3 Relay (the windowed removal with its green mask and its relay
  of history frames, GPL-3.0) by Akatz Labs (akatz-ai); the person is found and followed by SAM 3.1 from Meta, in
  the build Comfy-Org repackaged for ComfyUI, under the SAM License.

## The Motion Previs tab: Sam Wasserman's Motion Previs Studio

**Sam Wasserman (wassermanproductions.com, wasserman.ai)** created **Motion Previs Studio**
(https://github.com/wassermanproductions/motion-previs-studio, Apache-2.0, Copyright 2026 Sam Wasserman): the desktop
app that turns a reference shot into control layers and a production pack. The analysis behind this Studio's *Motion
Previs* tab is a **Python port of it, modified**: the pose track with its gap filling and smoothing, the OpenPose
BODY_25 skeleton and keypoints, the subject-masked camera solve and its `camera_motion.json`, the depth, edge, line art
and motion mask passes, the quality score, the four reference modes, and the production pack with its file names,
manifest, shot bible and prompt pack are all his design. `app/motion_worker.py` is the port; the Blender import
scripts in `app/motion_templates/` are his, copied with a header; his NOTICE is kept at
`LICENSES/Motion-Previs-Studio-NOTICE`. What was changed, and the notices for MediaPipe, Depth Anything, OpenCV and
ONNX Runtime, are in `THIRD_PARTY_NOTICES.md`. His app does much more than this port: web video import, a 3D
stick-figure preview, Send to Blockout, an MCP control server, session restore. What this pack adds is the camera
move in words and the hand-off of one layer as a motion reference for MiniMax H3.

## The image model: the Qwen team

The **Qwen team at Alibaba** released **Qwen-Image-2.1**: the 7B single-stream transformer with its causal text
prefix, the 64-channel VAE and the Qwen3-VL text encoder, with code in diffusers. The image half of this pack is
their model. The weights are under the Qwen Research License Agreement (non-commercial); this pack ships none of
them. Built with Qwen.

## The FLUX3-K picture models: Black Forest Labs

**Black Forest Labs** released **FLUX 3 Image Klein**: the 5.6B image transformer in a distilled checkpoint (FLUX3-K
Fast, 16 steps) and a base one (FLUX3-K Full, 50 steps), the Qwen3-VL-4B text encoder setup, the image autoencoder and
the prompt upsampler with its system prompts, with their reference code and their files for ComfyUI. The weights are
under the FLUX Kommunity License (non-commercial use; commercial use of the outputs for Qualifying Users under US$5M
revenue); this pack ships none of them. On a DGX Spark they run on **ComfyUI**'s own FLUX 3 nodes (comfyanonymous,
Comfy Org and the ComfyUI contributors, GPL-3.0, run as a separate program). On a Mac they run on **flux3-mlx** by
**felix-ab**, a port of BFL's reference code to MLX (Apache-2.0), with the 8-bit MLX conversion of the prompt
upsampler by **VisualInference**.

## The MOSS-Image picture model: OpenMOSS

**OpenMOSS** released **MOSS-Image**: a 5B single-stream image transformer in a base and a supervised fine-tuned
(SFT) checkpoint, with a Qwen3.5 9B text encoder and the FLUX.2 small decoder, and its JSON layout prompts. The weights
are under the Apache License 2.0; this pack ships none of them. On a DGX Spark the SFT checkpoint runs on **ComfyUI**'s
own MOSS-Image support (comfyanonymous, Comfy Org and the ComfyUI contributors, GPL-3.0, run as a separate program), from
**Comfy Org**'s repackaged files.

## The MLX port the image family is adapted from: Filip Strand and the mflux contributors

**mflux** has the MLX implementation of Qwen-Image-2.1 that TensorFold's family is adapted from: the transformer
module tree, running the text prefix once and reusing its keys and values, the rotary layout, the shifted schedule
and the VAE. It is also the reference every number here was checked against, and this pack still runs its prompt
encoder directly. mflux does much more than this port: editing, reference images, transparent output. On a Mac,
Edit a picture runs on mflux's own Qwen-Image-2.1 edit pipeline as it is. (MIT.)

## The image model since 2.3: Qwen-Image-2.1-Turbo

The **Qwen team** released **Qwen-Image-2.1-Turbo**, their own 8-step checkpoint with its sampling schedule saved
in it; the Studio's images come from it since 2.3. The DGX Spark build loads ComfyUI's one-file repack of it
(`Comfy-Org/Qwen-Image-2.1`).

## The image turbo adapter up to 2.2: Viggle

**Viggle** distilled **Qwen-Image-2.1-viggle-turbo**, the 6-step adapter behind the fast image numbers here, and
documented its nodes and schedule precisely enough to reproduce. (Qwen Research License.)

## The video model: MiniMax

**MiniMax** released **MiniMax H3** (Hailuo) as open weights with code: the 33B joint video and audio diffusion
transformer, the Qwen3-VL text encoder setup, and the video and audio VAEs. Nothing in this pack exists without
that release. The weights are under the MiniMax H3 Community License; this pack ships none of them.

## The voice model: Fish Audio

**Fish Audio** made **Drama 3**, the text-to-speech model this studio is integrating: a 35B mixture-of-experts model
(3B active per token) on a Qwen3.5 backbone with a codebook-MTP audio decoder and the Fish S2 codec, with up to 16
speakers in one text, voice cloning from a short clip, and direction tags. The local ports (TensorFold CUDA on the DGX
Spark, TensorFold/MLX on the Mac) follow Fish Audio's and the SGLang-Omni team's reference implementation
(Apache-2.0). Fish Audio Community License v1.0. **Built with Fish Audio.**

## The music model: MiniMax (MiniMax-Music3)

The **MiniMax team** made **MiniMax-Music3** (MiniMax Music 3), the model behind the Music tab: lyrics with section
tags and a style caption to a whole song with sung vocals, up to five minutes, 44.1 kHz stereo. It has an
autoregressive model that writes the acoustic tokens, a DiT that renders them, and the DAV decoder. The weights are
Comfy-Org's repackaging of MiniMax's release (Comfy-Org/MiniMax-Music-3), under the **MiniMax-Music3 Community
License**: commercial use is allowed, a commercial product shows "MiniMax-Music3" in its interface, a company above
US$20 million of yearly revenue needs MiniMax's written authorisation, and a hosted service keeps safeguards against
misuse. On a DGX Spark it runs on **ComfyUI**'s own MiniMax Music 3 nodes and blueprint (Comfy Org, GPL-3.0); on a Mac
on TensorFold's native Metal port (Zig and Metal, on an M5 or later) or its MLX port (`music3-mlx` in the
drowzeys/TensorFold fork), both following ComfyUI's reference code. The
pack ships no weights. **MiniMax-Music3.**

## Sound effects and foley: Stability AI, and Ho Kei Cheng and the MMAudio authors

**Stability AI** made **Stable Audio Open 1.0**, the model behind a sound effect from words (44.1 kHz stereo, up to
47 seconds), run through diffusers' StableAudioPipeline (Hugging Face). Its weights are under the **Stability AI
Community License**: free for research and for a business under US$1 million of yearly revenue; above that,
Stability's own licence. Its page on Hugging Face is gated.

**Ho Kei Cheng, Masato Ishii, Akio Hayakawa, Takashi Shibuya, Alexander Schwing and Yuki Mitsufuji** (University of
Illinois Urbana-Champaign and Sony AI) made **MMAudio**, the model behind foley for a clip: it watches the frames and
reads the words, so the sound lands on what is seen. Its code is MIT (Sony Research Inc.) and its weights are
**CC-BY-NC 4.0, non-commercial**. It loads Apple's DFN5B CLIP, Synchformer (Vladimir Iashin et al.) and NVIDIA's
BigVGAN v2 vocoder (Sang-gil Lee et al.). The studio installs it from its repository at a pinned commit, in an
environment of its own; the pack ships none of its code or weights.

**kijai** (Jukka Seppänen) wrote **ComfyUI-MMAudio** (MIT), the ComfyUI nodes foley runs on on a DGX Spark, and its
safetensors copies of MMAudio's weights. The studio clones it at a pinned commit; its own sampler node beside it
(`spark/studio_mmaudio`) calls kijai's loaders' models and generate. On a Spark a sound effect runs on **ComfyUI**'s
own Stable Audio nodes.

## The first native Mac engine: Salvatore Sanfilippo (antirez)

**antirez** wrote **h3.c**, MiniMax H3 in C and Metal. It is the reference for what is possible on Apple
Silicon and the cleanest picture we measured. The int8 kernels here follow h3.c's quantization scheme: per-row
activation scales, per-output-channel weight scales, one scale per 1,024 channels on wide inputs, 128x128x128
int8 tiles on the M5 tensor units, and the q/k norm and rotation inside the QKV kernel. No h3.c source is
copied; the design is his. (MIT.) Its `--ref-image` was the working reference-to-video on a Mac that this pack's
Ref2VA layout was checked against, row for row; the layout itself follows **ComfyUI**'s MiniMax H3 nodes
(Comfy-Org, GPL-3.0: read for the arithmetic, no source copied). Reference videos and reference audio follow
**MiniMax**'s own diffusers pipeline for H3 (`packing_ref2va.py`, Apache-2.0, as vendored by minimax-h3-mlx), and
h3.c's `--ref-video` and `--ref-audio` were the second implementation the layout was compared with.

## H3MLX: RobZombAI

**RobZombAI** built **H3MLX** on top of h3.c: a second-order solver, int8 paths, 4K mastering and a large preset
library. Working through it on an M5 Ultra is what started this pack, and its Sol-style switches and solver
gave us measured points to compare against.

## The MLX port this family is adapted from: mrbizarro

**mrbizarro** wrote **minimax-h3-mlx**, the MLX port of H3 that runs inside **Phosphene**. TensorFold's H3
family is adapted from it: the transformer module tree, the packed sequence and rotary positions, the two sigma
schedules, the video decoder with its tiling and clip chunking, and the adapter layout handling. Its notes on
bfloat16 adapter merging, QKV row order and SwiGLU half order saved days. This pack still runs its text
encoder, audio decoder and MP4 writer directly, and downloads the runner-layout Turbo adapter asset that
Phosphene publishes. (Apache-2.0.)

## The engine: Ash Hart

**Ash Hart** (ashhart) and the TensorFold contributors — **TensorFold**: the family and kernel structure this
lives in, and the practice of reaching the M5 tensor units from `mx.fast.metal_kernel` that the int8 kernels
are built on (`kernels/qwen/dense/v1/lane_qmm.py`). This pack pins TensorFold 0.6.5 with three commits on top.
(Apache-2.0.)

## Sol-Engine, Sol-Attn and Sol-H3: NVIDIA Research

**NVIDIA Research, Efficient AI Team and Singapore Lab** (Jincheng Yu, Junsong Chen, Yitong Li, Haopeng Li,
Haocheng Xi, Song Han, Enze Xie and co-authors of the Sol-Engine and Sol-Attn papers). Sol-H3 showed which
optimizations matter for H3: few-step adapters, fused norm / RoPE / SwiGLU kernels, precomputed AdaLN, int8 QKV,
and dense attention on a single device. The cached AdaLN tables and the fused QKV kernel here follow that list.
Their runtime is CUDA; no Sol code runs here.

## The 2x video decoder: speach1sdef178

**speach1sdef178** trained **MiniMax-H3-X2-Detail-VAE**: H3's own ViT decoder with a head that packs 12 channels, so
the same latents decode to frames twice as large. It is what makes the 2K finish and the fast drafts here possible,
and its write-up is unusually honest about what the 2x output does and does not contain. This pack uses the decoder
only; the reference-detail branch in the same file is not ported. (MiniMax H3 Community License.)

## Few-step adapters

- **LightX2V** (ModelTC) — the **MiniMax H3 Turbo** 4-step adapter (`lightx2v/Minimax-h3-Turbo`, Apache-2.0),
  the adapter behind every fast video number in this pack.
- **LightX2V** (ModelTC) — the **Ref2VA Turbo** 4-step adapter from the same repository (Apache-2.0), behind
  `REF_TURBO=1` for reference pictures.
- **larryvrh** — `MiniMax-H3-Turbo-Lora`, the adapter lineage minimax-h3-mlx's loader was written for.
- **Ash Hart** and the TensorFold contributors — the **TensorFold 1.0 native runtime** (Zig driving Metal, Apache-2.0)
  that the FastH3 text-to-video passes run on since 2.0: its Metal bindings, checkpoint readers and build. The H3
  family and kernels added on top are this pack's; its LICENSE, NOTICE and THIRD_PARTY_NOTICES ship beside the
  program in `zig-engine/`.
- **FastVideo / Hao AI Lab @ UCSD** — **FastH3**: the distilled FastH3 8-Step V2 transformer that `scripts/fast.sh`
  runs, the VSA-H3 sparse-attention routing it was trained with (their MLX routing code is vendored in the engine,
  Apache-2.0), and the 4-step adapters (`fastvideo-lora-v2`) this family also reads.
- **TaoLiveAIGC** — the TaoMate-H3 3-step ladder, documented in Phosphene and supported by the sigma subset
  option.

## The workflow the web app follows: rookiestar28

**rookiestar28** built **ComfyUI-MiniMaxH3-Studio**: a prompt workbench, a production board for longer videos and a
clip editor for MiniMax H3 inside ComfyUI. The web app in this pack follows that create, produce, edit flow on top
of this pack's scripts. No code is shared; theirs is the far more complete tool. (Apache-2.0.)

## The many-step fast recipe: ddalcu

**ddalcu**'s **mlx-serve** showed how to make 30 steps cost nine: a velocity cache and attention reuse with careful
gates. The engine pinned here carries an adaptation of it; it is not yet enabled in this pack's scripts.

## The brain the studio runs itself: NVIDIA (Nemotron 3.5 Bolt) and Mia (MiaAI-Lab)

**NVIDIA** made **NVIDIA Nemotron 3.5 Bolt** (nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB, 74B parameters, 9B active)
and its **LiLiCorr** drafter, the model the studio loads as its own brain (`app/bolt.py`): the official 24 GB
checkpoint at a326822a and the drafter at 4c6a5613, downloaded by `--bolt`, under the **OpenMDW License Agreement 1.1**
(use, modification and redistribution free, commercial use included; a copy passed on keeps the agreement and NVIDIA's
notices). No weights are in this pack.

**Mia (MiaAI-Lab)** made **Nemotron-3.5-Bolt-TensorFold-DGX-Spark** (MIT), the recipe that serves Bolt on one DGX Spark
with TensorFold's native engine: the Bolt lane backend (`libbolt.so`, the packed-weight kernels, the LiLiCorr drafter
run inside it) and the patches to TensorFold 46a08c18. The studio's CUDA engine is that build, copied out of Mia's
image or the carrier by `spark/build-bolt.sh`; the trimmed settings it runs with (4 streams, 65,536 tokens, 2 GiB of
prompt cache) are the studio's. TensorFold is Ash Hart's and the contributors'; **llama.cpp** (Georgi Gerganov and the
ggml contributors) runs Bolt on a Mac until TensorFold's Metal port of the engine lands.

## The agents and brains you connect: their makers

The studio drives the coding agents and model servers you already use, each with your own sign-in or on your own
machine; none of their code ships in this pack.

- **Anthropic** — **Claude Code**.
- **OpenAI** — **Codex** (the CLI, and the copy the ChatGPT desktop app carries).
- **Google** — **Gemini CLI**.
- **xAI** — **Grok CLI**.
- **Nous Research** — **Hermes Agent**.
- **Ollama**, **LM Studio**, **vLLM**, **SGLang** (the SGLang team and LMSYS) and **llama.cpp** (Georgi Gerganov and
  the ggml contributors) — the local model servers a brain or a local agent may be.
- **Siddharth Ahuja** — **blender-mcp**, the Blender bridge an agent works through; the **Blender Foundation** —
  **Blender**.
- The **LobeHub** icon set — the agents' marks on the page (unofficial, MIT; `app/static/brand/THIRD-PARTY-MARKS.md`).

## The RTX Pro 6000 build, run on the card: August Sturm

**August Sturm (@auguststurm)** ran the Studio's core install on an RTX Pro 6000 Blackwell, the first run on the card
itself, and made the CUDA install pick the card by its `nvidia-smi` index (`STUDIO_GPU`): one install shared by the
DGX Spark and RTX Pro 6000 entries (`oneshot-setup-cuda.sh`), a compute card used beside a display card, and a second
studio on a second card. He also made **quitting the app stop that install's ComfyUI** (#119), so Ctrl-C frees the
card on a DGX Spark and an RTX Pro 6000 alike, and the quit no longer ends in a traceback.

## Bug reports from a film made in Slate: BTankut

**BTankut (@BTankut)** made a 2-minute, continuity-first film entirely in Slate and reported what got in the way, each
report with its cause and a fix (#51 to #60). Four are fixed from those reports (#121): the export keeps a clip's In
point when Out is 0, the export size survives a redraw, the shot check's "+ timeline, trimmed to it" button shows, and
Regenerate no longer sends the app's own "Regenerated at seed N" label to the brain as feedback.

## Platform and tools

- **Apple / ml-explore** — **MLX**, and the Metal 4 tensor operations on M5.
- **mlx-vlm** contributors — the Qwen3-VL language model the text encoder runs on.
- **Qwen team (Alibaba)** — Qwen3-VL.
- **Kijai**, **DeepBeepMeep**, **ddalcu** — the ComfyUI conversions, pruned checkpoints and MLX quants that the
  wider H3 ecosystem (and Phosphene's install) relies on.
- **FFmpeg**.
- **OpenAI** — **Whisper**, and **SYSTRAN** — **faster-whisper**: the QA gate's and Voice's check of the spoken words.
- **yt-dlp** contributors — Motion Previs reading a shot from a link.
- **PyTorch**; **Hugging Face** — **diffusers**, **transformers** and **huggingface_hub**; **Patrick Kidger** and
  **Xuechen Li** — **torchsde**.
- **Comfy Org** — **ComfyUI**, which every render on a DGX Spark runs through.
- **Sebastián Ramírez** — **FastAPI**; **Encode** — **Starlette** and **Uvicorn**: the web app.
- **The Geist Project Authors** — **Geist** and **Geist Mono**; **The Instrument Serif Project Authors** — **Instrument
  Serif** (SIL Open Font License, `app/static/fonts/`).

## Libraries, conversions and build tools

What the studio's installers fetch, its engines bundle and its code imports, beyond the models above. Each is its
authors' work under its own licence; none of it is this pack's.

- **Mobius Labs** — **faster-whisper-large-v3-turbo**, the CTranslate2 conversion the QA gate hears speech with (MIT).
- **Lihe Yang et al.** (HKU, TikTok) — **Depth Anything** (Apache-2.0), and **Xenova** (Joshua Lochner) — its ONNX
  conversion the Motion Previs tab's depth pass runs; **Google** — **MediaPipe** and its Pose Landmarker models
  (Apache-2.0); **OpenCV**; **Microsoft** — **ONNX Runtime** (MIT). Their notices are in `THIRD_PARTY_NOTICES.md`.
- **Google** — **T5-base**, the text encoder inside Stable Audio Open (Apache-2.0); **SentencePiece**.
- **Descript** — **descript-audio-codec** (MIT), and the **sgl-project** SGLang-Omni team — the `fishaudio_s2_pro`
  codec files (Apache-2.0): the installer fetches both by hash to build Fish Audio's codec on a DGX Spark.
- Inside the Bolt engine (`spark/build-bolt.sh`): **MLC-AI** — **xgrammar** (Apache-2.0) with **DMLC**'s dlpack and
  **Kazuho Oku**'s picojson; **libjpeg-turbo** and the **Independent JPEG Group**; **FFmpeg**'s decoders (LGPL-2.1+);
  **ggml**'s quantization tables (MIT).
- **BusyBox** (GPL-2.0) — the base layer of the GHCR carrier image.
- **Zig Software Foundation** — **Zig** 0.17, which builds every native engine (MIT); **NVIDIA** — the **CUDA toolkit**.
- **Simple Icons** contributors — the source of the Qwen, Claude, Gemini and Fish Audio marks (CC0-1.0,
  `app/static/brand/THIRD-PARTY-MARKS.md`).
- **NumPy**, **Pillow**, **PyAV**, **SciPy**, **soundfile**, **einops**, **safetensors**, **tokenizers** and
  **accelerate** (Hugging Face), **mlx-lm**, **mlx-whisper** and **mlx-audio** (ml-explore, Prince Canuma), **aiohttp**,
  **Pydantic**, **python-multipart**, **llguidance**, **sounddevice**, **miniaudio**, **Matplotlib**; and for
  ComfyUI-MMAudio, **open_clip**, **timm**, **librosa**, **torchdiffeq** and **OmegaConf**.
- **Astral** — **uv**; **Homebrew**; **Node.js** and **npm**; **Docker**.
- **kohya-ss** — the LoRA file layout `scripts/convert-lora.py` reads; **OpenTimelineIO** (Academy Software
  Foundation), Apple's **FCPXML** and the CMX **EDL** — the timeline formats the Suite writes with its own code.
- **Anthropic** — **Claude**, which wrote much of this code with Keys.

## Test prompts

"BLACK MIRROR: FINAL REFLECTION" (`prompts/black-mirror-scene.txt`) is by **@itxabdullaa** on X (onlyprompts.ai X
collection).

Pack: drowzeys / keys.
