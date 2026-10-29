# Third-party notices: the Motion Previs tab

The Motion Previs tab (`app/motion.py`, `app/motion_worker.py`, `app/static/motion.js`, `app/motion_templates/`,
`scripts/setup-motion.sh`) builds on the work below. None of it is bundled in this repository except where said:
packages and models are downloaded by `scripts/setup-motion.sh` (`oneshot-setup.sh --motion`) into
`$PREFIX/motion-venv` and `$PREFIX/motion-models`. The notices for the rest of the pack are in [NOTICE](NOTICE) and
[CREDITS.md](CREDITS.md).

## Motion Previs Studio, by Sam Wasserman

- Original: **Motion Previs Studio 4.1.0**, <https://github.com/wassermanproductions/motion-previs-studio>, read at
  commit `95e7d0ff1d4cc546f7eb09a74ccbd084988a19bc`.
- Copyright 2026 Sam Wasserman. Developed and created by Sam Wasserman for Wasserman Productions and Wasserman.ai.
- License: Apache License 2.0 (the text is this repository's [LICENSE](LICENSE)). Its NOTICE file is kept unchanged at
  [LICENSES/Motion-Previs-Studio-NOTICE](LICENSES/Motion-Previs-Studio-NOTICE).
- Windows support in that repository was contributed by Gumbii Digital; none of the Windows packaging is used here.

**What is taken, and that it was modified.** The analysis in this tab is a Python port of his: `app/motion_worker.py`
is a rewrite in Python of `src/lib/pose.ts`, `poseVideo.ts`, `openpose.ts`, `cameraMotion.ts`, `aiDepth.ts` and
`quality.ts`, and of the ffmpeg control passes and the production pack writer in `electron/main.cjs`. The pack's
file names, the layout of `bundle_manifest.json`, the schemas of `camera_motion.json`, `pose_landmarks.json` and
`openpose_keypoints.json`, the four reference modes, the wording of the prompt pack, shot bible and manifests, and the
three Blender import scripts in `app/motion_templates/` (copied from `electron/main.cjs` with a header added) are his.
The interface in `app/static/motion.js` follows his app's flow and shares no code with it.

**What differs from the original**

| Original | This port | Why |
|---|---|---|
| Electron + React/TypeScript desktop app | A tab of this web app; the analysis is one Python process per job | It runs beside the Studio's render queue, on a Mac or a DGX Spark, with no second app |
| MediaPipe Tasks Vision (WASM) in the renderer | The `mediapipe` Python package, same Pose Landmarker models and hashes | Same models, native speed |
| Pose model defaults to Lite | Defaults to Heavy (Lite and Full are offered) | Heavy tracks a 5 second shot in about a second here, and on the test clips Lite placed the shoulders well inside a coat |
| Camera solve written out by hand on a 192 pixel canvas: Shi-Tomasi corners, pyramidal Lucas-Kanade, seeded two-point RANSAC | OpenCV's `goodFeaturesToTrack`, `calcOpticalFlowPyrLK` (with a forward-backward check) and `estimateAffinePartial2D` (RANSAC, fixed seed) on a 384 pixel canvas | The same method on the library's tested routines; the same subject mask from the pose track, the same clamps and confidence |
| The shift of each step is read at the top-left corner of the frame | It is read at the centre of the frame | A push-in on the middle of the picture then has no pan or tilt in it |
| The roll of each step is the rotation of the similarity fit | Roll is kept only where both picture axes turn the same way | Travelling past near and far things shears the picture, and the fit read that as several degrees of roll that were not there |
| With no pose track, corners in the middle of the frame score half | Corners are taken from outside the middle first, then topped up | `goodFeaturesToTrack` takes a mask, not a weight |
| Depth-Anything-small through transformers.js (WebGPU or WASM), short side 518, at most 8 frames a second, levels set per frame | The same ONNX file (`Xenova/depth-anything-small-hf` at the pinned revision, 8-bit) through ONNX Runtime on the processor, long side 518, at the chosen sample rate, levels that follow the clip slowly | A quarter of the work for a control layer, no flicker between frames, and a layer smooth enough to hand on as a reference clip |
| A smaller source is enlarged to 1280 pixels | A smaller source keeps its size | The Studio's own clips are often 864 x 480 |
| Imports web video through yt-dlp; sends clips to Blockout; MCP control server; session restore; 3D preview | None of these | Not asked for here. No URL download exists in this tab |
| `pose_high_contrast.webm`, `openpose_pose.webm`, `ai_depth.webm` | Not written (their manifest entries are null) | They are the original's pre-v4 recorder output; the mp4 files are the same pictures |
| Any shot length | A shot is 2 to 15 seconds | The length of a reference clip MiniMax H3 Ref2VA accepts |
| - | `bundle_manifest.json` has one more key, `port`: what this is a port of, the camera move in words, per-stage timings, notes | The original's keys are unchanged and in its order |
| - | A "MiniMax H3" section at the end of `prompt_pack.md` with the camera move in words; `motion_reference()` | The hand-off this tab exists for |

## MediaPipe (Google)

- The `mediapipe` Python package 1.1.0, <https://github.com/google-ai-edge/mediapipe>. Apache License 2.0.
- Pose Landmarker models `pose_landmarker_lite.task`, `pose_landmarker_full.task`, `pose_landmarker_heavy.task`
  (float16, version 1), from `storage.googleapis.com/mediapipe-models`, with the SHA-256 hashes pinned in Motion Previs
  Studio's `ASSET_MANIFEST.json`. Apache License 2.0.

## Depth Anything

- **Depth Anything** (Lihe Yang, Bingyi Kang, Zilong Huang, Xiaogang Xu, Jiashi Feng, Hengshuang Zhao; CVPR 2024),
  <https://github.com/LiheYoung/Depth-Anything>. Code: Apache License 2.0. The small model
  `LiheYoung/depth-anything-small-hf`: Apache License 2.0.
- The file used is its ONNX conversion `onnx/model_quantized.onnx` from `Xenova/depth-anything-small-hf` at revision
  `2e942621ab9f2371c1df9eb223291b5ac31475e6` (the repository and revision Motion Previs Studio pins), SHA-256
  `e4e2ffe9b0396ebd4a0c09b855762b186aab338e79f76531b09777aafc91db84`. That repository declares no license of its
  own; it is used under the Apache-2.0 license of the base model it names.

## OpenCV

- `opencv-contrib-python` 5.0.0.93 (the build MediaPipe requires), <https://github.com/opencv/opencv> and
  <https://github.com/opencv/opencv-python>. OpenCV: Apache License 2.0. The Python packaging: MIT. The wheels carry
  third-party libraries (among them FFmpeg libraries under LGPL-2.1-or-later) with their license files inside the wheel.
- Without the Motion Previs environment, the camera solve runs on the `opencv-python` already in the Mac Studio
  install, under the same licenses.

## ONNX Runtime (Microsoft)

- `onnxruntime` 1.31.0, <https://github.com/microsoft/onnxruntime>. MIT License.

## NumPy

- <https://numpy.org>. BSD 3-Clause License.

## FFmpeg

- The `ffmpeg` and `ffprobe` on the machine's PATH are run as separate programs (trim, control passes, encoding); none
  is bundled. FFmpeg is licensed under LGPL-2.1-or-later or GPL-2.0-or-later depending on how it was built.

This file is informational and does not replace the license texts shipped inside each package.

# Third-party notices: the Library's actions

"Turnaround sheet", "Upscale" and "Remove a person" (`app/actions.py`, `app/static/actions.js`,
`spark/spark_workflows.py`, `spark/setup-workflows.sh`, `scripts/turnaround.sh`, the `--sheet` mode of
`h3_generate.py`) run other people's work. None of it is in this repository: `spark/setup-workflows.sh`
(`oneshot-setup-spark.sh --ref2va` runs it) clones each node pack from its author's repository at the commit named
here into `$PREFIX/ComfyUI/custom_nodes` and downloads the two models from their publishers. The Studio then talks to
those nodes through ComfyUI's HTTP API as a separate program. The three LoRAs come from the LoRA hub
(`oneshot-essential-bundle.sh`), each from its author's page under its own terms.

## H3 Contact Sheet, by MATLOWAI

- ComfyUI nodes: <https://github.com/matlowai/ComfyUI-H3-ContactSheet>, commit
  `3c8c8c66e69362e5abf9cf4c4e693baddab90ca3`. GNU General Public License v3.0. Used unmodified, on a DGX Spark.
- The reference sampler those nodes follow: <https://github.com/matlowai/h3-contact-sheet> (GPL-3.0). Read, not used.
- The Mac's `h3_generate.py --sheet` is this pack's own code on TensorFold's H3 family: it uses the layout the
  authors describe in their README and model card (five one-step picture latents on the time axis of a 17-frame
  clip, 28 silent audio latents, each slot decoded alone as a still clip). No code of theirs was copied.
- The LoRA: `matlod/minimax-h3-turnaround` on Hugging Face, a Model Derivative of MiniMax H3 under the MiniMax H3
  Community License Agreement.

## H3 Video Upsampler, by dntpi (Sandpies)

- ComfyUI node: <https://github.com/dntpi/ComfyUI-H3-Video-Upsampler>, commit
  `36cb612ec3df30094eeb4fec66f1528925ebba73`. MIT License. Used unmodified, on a DGX Spark. Its own
  `THIRD_PARTY_NOTICES.md` credits LBH-123-AI (the latent upscaler's layout), bbaudio-2025 (the upscale recipe) and
  Alissonerdx (the guide layout and the `h3upscale-4k` training set).
- The latent upscaler weights: `LBH-123-AI/Minimax_h3_latent_Upscaler`, file
  `minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors`, SHA-256
  `4f57821f5837f32f7142b67d815606dbd7550f194e5c769f7d6c3f83b146a5e6`. Apache License 2.0.
- The LoRA: `lora/h3upscale_v2_x2.safetensors` from `sandpies/ComfyUI-H3-Video-Upsampler` on Hugging Face. Its
  page gives no licence of its own ("see third-party notices"); it derives from MiniMax H3, whose Community License
  Agreement applies. It is installed on the user's machine from its author's page and is in no hosted package of
  this pack.

## H3 Relay and the Person Remover, by Akatz Labs

- ComfyUI nodes: <https://github.com/akatz-ai/h3-relay>, commit `cf489b7f2472fdf33866fae36de49aea03ef464f`. GNU
  General Public License v3.0. Used unmodified, on a DGX Spark: its green-mask node and its Person Remover node,
  which runs the windows and carries 18 frames of history from one to the next.
- The Studio's graph is the one of the authors' example workflow `Person-Remover-Window-Reroll-V1.json`, with its
  settings (strength 1.0, windows of 22 frames, 12 passes, `er_sde` / `simple`, sigma shift 12 / 3, mask grown by 5
  points, seed 904234), on the Ref2VA weights in bfloat16 instead of the 8-bit file, and with this pack's own way to
  build the clean first frame when the user gives none.
- The LoRA: `akatz-ai/MiniMax-H3-Person-Remover-LoRA` on Hugging Face, under the MiniMax H3 Community License
  Agreement.

## SAM 3.1 (Meta)

- **Segment Anything Model 3.1** by Meta, in the single-file build `checkpoints/sam3.1_multiplex_fp16.safetensors`
  of `Comfy-Org/sam3.1` on Hugging Face, SHA-256
  `9ba99c92703c2e8b4f47de2d34a539bb8e18923049e238b780d70dbe6368eb03`. SAM License. Run by ComfyUI's own SAM 3 nodes.

# Third-party notices: Character Swap

"Character Swap" (`app/swap.py`, `app/static/swap.js`, `spark/spark_swap.py`, `spark/swap_plan.py`,
`spark/setup-swap.sh`) replaces the person in a part of a clip with the character in a picture. Its code is this
pack's own and runs on ComfyUI's own nodes (SAM 3, MiniMax H3, the latent and video nodes) through ComfyUI's HTTP API;
no node pack is installed for it and no code of anyone below is copied into this repository.

## Openjutsu, by Comfy Org

- <https://github.com/Comfy-Org/comfy-openjutsu>, commit `70ec62df0eda7c58697ea3ac2050588513651583`. MIT License,
  Copyright (c) 2026 Comfy Org. The idea (one picture's character in place of the person in a 5 to 15 second part of
  one shot, the clip's own sound kept), its limits, the prompt and the settings of its `workflows/swap.api.json` were
  read and followed. Its optional first frame went to a paid picture service; here the studio's own FLUX3-K edit draws
  it, on this machine.

## H3 Character Swap, by Akatz Labs

- The workflow: Akatz Labs' H3 Character Swap workflow (MIT, Copyright (c) 2026 Akatz Labs), as Openjutsu carries it,
  and the example workflows on the LoRA's Hugging Face page (`examples/`, under the MIT notice kept there). Their
  settings are used: strength 1 for both LoRAs, 8 passes of `er_sde` with the `normal` schedule at CFG 1, sigma shift
  12 for the picture and 3 for the sound, SAM finding "person" at 0.35 with two refinement passes and following them
  at 0.5 with one object, the largest person, masks grown by 64 pixels, windows of at most 124 frames, 18 of them the
  history carried from the window before, seed 904239.
- Their ComfyUI node pack for it (`h3_character_swap` in akatz-labs/h3-character-swap-tool) and their H3 Relay pack
  are GNU General Public License v3.0. Neither is installed for this action and no code of either was read into or
  copied into this pack: how the mask, the history and the windows work here was written from the LoRA's model card,
  the MIT workflows' settings and H3 Relay's published documentation (`docs/windowed-edit.md`).
- The LoRA: `h3_character_swap_v1.5.safetensors` from `akatz-ai/MiniMax-H3-Character-Swap-LoRA` (revision
  `d06323b7c8e70e97cdab48b78a1e05f786c8cba9`), SHA-256
  `8f2c3c3eba161df8d0c6099598b4730f641f5ce01e04cd16d2427cff8a35218f`. A Model Derivative of MiniMax H3 under the
  MiniMax H3 Community License Agreement (its `NOTICE`: "Modified by Akatz Labs").

## MiniMax H3 Turbo, by LightX2V (ModelTC)

- `minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors` from `lightx2v/Minimax-h3-Turbo` (revision
  `3ec17a324ced54151364f24f8b5fb6bf7e26414f`), SHA-256
  `08cfe946033af7d27719b964b6e0a0e50c32138daabbd6ce4137e23df6bf9980`. Apache License 2.0
  (<https://github.com/ModelTC/Minimax-H3-Turbo>). It derives from MiniMax H3, whose Community License applies to it too.

## MiniMax H3 (MiniMax)

- Ref2VA (`minimax_h3_ref2va_pruned_int8_convrot.safetensors`, or the bf16 file where it is the one installed), its
  text encoder and decoders from `Comfy-Org/MiniMax-H3`, the files the studio's H3 install already has. MiniMax H3
  Community License Agreement: its grant does not cover the European Union, the United Kingdom, the Republic of Korea
  or the United States of America, and it carries an Acceptable Use Policy. "MiniMax H3 is licensed under the
  MiniMax H3 Community License Agreement, Copyright © 2026 MiniMax. All Rights Reserved." Powered by MiniMax H3.
- SAM 3.1 is the build listed under the Library's actions above (SAM License).
- `spark/patches/tensorfold-h3-gateless.patch` changes TensorFold's H3 CUDA family (drowzeys/TensorFold at
  `1f7ecc75ded5df6da151d4d5eeee87b35161e3c2`, Apache License 2.0) so it loads a MiniMax H3 transformer without
  FastH3's gate weights, for the optional `SWAP_ENGINE=tensorfold` route. It is not applied by any installer.

# Third-party notices: the Music tab

The Music tab (app/music.py, app/static/music.js) makes songs with MiniMax-Music3. Nothing of the model is in this
pack: the weights are downloaded by `oneshot-setup.sh --music` / `oneshot-setup-spark.sh --music` from Hugging Face.

## MiniMax-Music3 (MiniMax)

MiniMax-Music3, by MiniMax, in Comfy-Org's single-file repackaging (Comfy-Org/MiniMax-Music-3: the DiT in fp16, the
pruned text encoder in bf16, the DAV decoder). MiniMax-Music3 Community License:

- commercial use is allowed; a commercial product or service must display "MiniMax-Music3" in its user interface
  (the Music tab shows it in its title, and every song keeps `"credit": "MiniMax-Music3"`);
- a licensee with more than US$20 million of yearly revenue needs MiniMax's prior written authorisation;
- a hosted service built on it must keep safeguards against misuse;
- the weights are not redistributed by this pack.

Read the licence on the model's page before commercial use.

## ComfyUI (Comfy Org)

On a DGX Spark the song runs on ComfyUI's own MiniMax Music 3 nodes (`comfy_extras/nodes_minimax_music.py`,
`comfy/ldm/minimax_music/`), following its blueprint "Text to Music (MiniMax Music 3)", in the ComfyUI the installer
clones at a pinned commit (GPL-3.0). None of its code is copied into this pack.

## TensorFold music3 (CUDA)

On a DGX Spark the song runs on TensorFold's CUDA port of MiniMax Music 3 (drowzeys/TensorFold, branch
`music3-cuda-118`, commit 8cd08c83: the music3 family on TensorFold 1.1.8, Apache-2.0, Ash Hart and the TensorFold
contributors), from the carrier image or built from source into `$PREFIX/music3` by `spark/build-music3.sh` (Zig
0.17.0, MIT; NVIDIA's CUDA toolkit).

## TensorFold music3 (Metal)

On an M5 or later Mac the song runs on TensorFold's Metal port of MiniMax Music 3 (drowzeys/TensorFold, branch
`music3-metal-118`, commit af17c54a, Apache-2.0: the code of pull request #987 to ashhart/TensorFold on the TensorFold 1.1.8
release since 4.2.0, 1.1.5 from 3.3.1), built from source into
`$PREFIX/music3` by `scripts/build-music3-metal.sh` (Zig 0.17.0, MIT, fetched from ziglang.org).

## TensorFold music3-mlx

On an older Mac, or with `MUSIC3_BACKEND=mlx`, the song runs on TensorFold's MLX port of MiniMax Music 3 (drowzeys/TensorFold, branch `music3-mlx`, commit
6844b413, Apache-2.0), installed into its own environment by `scripts/setup-music.sh`.

## YuE2 (Multimodal Art Projection, the YuE2 authors)

The Music tab's YuE2 engine (app/music_yue2.py, spark/yue2_server.py) runs YuE2 in its own environment under
`$PREFIX/yue2`, installed by `spark/setup-yue2.sh` (`oneshot-setup-spark.sh --yue2`). None of YuE2's code or weights is
copied into this pack.

- Code: github.com/multimodal-art-projection/YuE at commit 1647252d68b70cbead046ffd3f7caf036dde47ee (the `yue2-infer`
  0.1.6 package and the yue2-music 1.2.0 skill), Apache License 2.0, Copyright (c) 2026 the YuE2 authors. Its
  `modeling_vae.py` keeps code derived from stable-audio-tools (Copyright (c) 2023 Stability AI, MIT) and SnakeBeta /
  BigVGAN (Copyright (c) 2022 NVIDIA CORPORATION, MIT). The skill's instrumental helpers (`skills/yue2-music/instrumental`,
  `convert_score`, run from that checkout to move a score's vocal melody to the instruments) are MIT, Copyright (c)
  2026 yue2-music contributors.
- Weights: m-a-p/YuE2-3B (revision 8f24312f, model.safetensors sha256 1d55c42c...) and m-a-p/YuE2-Vae (revision
  152733a1, sha256 807ce9d5...), under Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0),
  with the additional "Individual creator permission and academic use (2026-09-16)" of the repository's
  MODEL_LICENSE, which reads in part:

  > In addition to the CC BY-NC 4.0 license below, the YuE2 model licensors grant personal users, content creators
  > and musicians acting in an individual capacity permission to use the model weights free of charge to generate
  > outputs, and to publish, distribute, sell, license or otherwise monetize those outputs, subject to the
  > responsible-use conditions below.
  >
  > For these individual creative activities, the model licensors waive the NonCommercial restriction to the extent
  > necessary. These users do not need a separate commercial license from, or owe license fees or royalties to, the
  > model licensors for this use or for monetizing their outputs.
  >
  > The individual creator permission does not authorize commercial use of the model weights by companies.
  >
  > Under this permission, you must not use the model weights or generated outputs for illegal, harmful, deceptive or
  > otherwise unethical purposes, including fraud, harassment or deceptive impersonation.
  >
  > This permission concerns generating and using outputs; it does not extend to commercial redistribution or sale of
  > the model weights.

  Academic users may use YuE2 free of charge for non-commercial research and education under CC BY-NC 4.0. Companies
  wishing to use the weights commercially contact the authors for a commercial licence. The weights are not
  redistributed by this pack; read the full MODEL_LICENSE (copied next to the weights by the installer) before use.
- YuE2's environment also installs, at the versions YuE2 pins: Hugging Face transformers 4.57.6, huggingface_hub
  0.36.2, safetensors 0.7.0 and accelerate 1.13.0 (Apache 2.0), tiktoken 0.12.0 (MIT, OpenAI), NumPy 2.2.6 (BSD-3),
  soundfile 0.13.1 (BSD-3), and the studio's PyTorch 2.14.1 for CUDA 13 (BSD-3) with Triton (MIT).
- YuE2-3B's tokenizer vocabulary, `qwen.tiktoken`, is the Qwen team's (Alibaba Cloud), released with Qwen under the
  Tongyi Qianwen licence; it is downloaded with the YuE2-3B weights from m-a-p's repository as the YuE2 authors
  distribute it, and is not part of this pack.

## TensorFold yue2 decode engine (CUDA)

On a DGX Spark the YuE2 song model's decode steps can run on `libtf_yue2.so`, built into `$PREFIX/tf-yue2` by
`spark/build-yue2-engine.sh` from TensorFold (drowzeys/TensorFold, branch `music3-cuda-115`, commit 5505dbfa: TensorFold
1.1.5 with the music3 family, Apache-2.0, Ash Hart and the TensorFold contributors) with this pack's yue2 family
(`spark/tensorfold_yue2/tensorfold`) laid over it. The family's kernels include TensorFold music3's decode GEMVs and
int8 quantizers (`zig/kernels/cuda/music3_ar.cuh`, Apache-2.0); the build fetches Zig 0.17.0 (MIT) and uses NVIDIA's
CUDA toolkit. The Python side (`spark/tensorfold_yue2/__init__.py`) subclasses the `GraphAR` decode class of the YuE2
inference package (`yue2-infer`, multimodal-art-projection/YuE, Apache-2.0) and keeps its step checks; no YuE2 weights
are part of the engine or this pack (they are CC BY-NC 4.0, see the YuE2 entry).

## Typefaces

The web app ships three typefaces in `app/static/fonts`, each under the SIL Open Font License 1.1 (full text beside
each file):

- Geist and Geist Mono, Copyright 2024 The Geist Project Authors (`OFL-geist.txt`)
- Instrument Serif, Copyright 2022 The Instrument Serif Project Authors (`OFL-instrument-serif.txt`)

## Marks of other projects

`app/static/brand` holds the marks of the models and tools the web app runs (Qwen, MiniMax, Black Forest Labs, Fish
Audio, ComfyUI, TensorFold, and the agent brains), shown beside their names to identify them. They are trademarks of
their owners; sources, licences of the drawings and each owner's usage notes are in
`app/static/brand/THIRD-PARTY-MARKS.md`.

# Third-party notices: the Studio's Bolt brain

The Studio can run NVIDIA Nemotron 3.5 Bolt as its own brain (app/bolt.py, app/bolt_leash.py, spark/build-bolt.sh,
scripts/setup-bolt.sh). Nothing of the model is in this pack: `oneshot-setup.sh --bolt` / `oneshot-setup-spark.sh --bolt`
download it from Hugging Face.

## NVIDIA Nemotron 3.5 Bolt and the LiLiCorr drafter (NVIDIA)

nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB at a326822af111290f2af5ac59ef4b5040347dabc4 and
nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr at 4c6a5613b6d887194a3a5493e3a18a5b3930bb86, and on a Mac
NVIDIA's GGUF of the same model (nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF), under the OpenMDW License
Agreement, version 1.1 (https://github.com/OpenMDW/OpenMDW/blob/main/1.1/LICENSE.OpenMDW-1.1):

- permission to deal in the Model Materials without restriction, free of charge, commercial use included;
- a distribution of any portion keeps a copy of the agreement and the copyright and origin notices that apply;
- the rights end for whoever brings a patent or copyright suit claiming the Model Materials infringe;
- no restriction on the outputs; no warranty.

## Nemotron-3.5-Bolt-TensorFold-DGX-Spark (MiaAI-Lab)

https://github.com/MiaAI-Lab/Nemotron-3.5-Bolt-TensorFold-DGX-Spark at 89e1c8a0 (6f61d772 before 3.3.3), carried in the
drowzeys/TensorFold branch bolt-lilicorr-118 (zig/bolt-cuda) the CUDA engine is built from. MIT
License, Copyright (c) 2026 MiaAI-Lab:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
> documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
> rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
> permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright
> notice and this permission notice shall be included in all copies or substantial portions of the Software.
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
> WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
> COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
> OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

The engine the Studio runs on a DGX Spark is that recipe's build (`tensorfold-native` with `libbolt.so`): since 4.2.0 the
recipe's `engine/` (in `zig/bolt-cuda`) and its registration patch rebased onto TensorFold 1.1.8
(03d08f1ef5e385f6ecccb49d54e31b44e75ed422; Apache-2.0, code before 0.6.0 MIT; Ash Hart and the TensorFold contributors),
drowzeys/TensorFold branch bolt-lilicorr-118 at 7d124f36df08a5836d3a5e3e85c19b6d919c70dc (bolt-lilicorr-115 ecdc7892 on
TensorFold 1.1.5 from 3.3.3, 6f61d772 on TensorFold 46a08c18 before). The build also carries, each under its own licence (kept in `$PREFIX/bolt/share/doc/tensorfold`):
ggml's IQ2_XXS tables and block layouts (MIT), libjpeg-turbo 3.1.4.1 (BSD-3-Clause, IJG, zlib), FFmpeg 9.0.2's decoders
in libtfvideo.so (LGPL 2.1 or later), xgrammar v0.2.8 with dlpack (Apache-2.0) and picojson (BSD-2-Clause) in
libtfgrammar.so. It is built with the CUDA toolkit's nvcc and Zig; no container image is involved.

## llama.cpp

On a Mac, with `BOLT_BACKEND=llamacpp`, Bolt runs in llama.cpp's `llama-server` (MIT, Georgi Gerganov and the ggml
contributors), which the person builds; none of its code is in this pack.

# Third-party notices: Graphics, Finish, LoRA training and the Night shift

The Graphics tab (`app/graphics.py`, `app/graphics/`, `scripts/setup-graphics.sh`), Finish (`app/finish.py`,
`scripts/setup-finish.sh`), Looks → Train a LoRA (`app/train.py`, `spark/setup-train.sh`) and the Night shift's phone
notifications (`app/push.py`) run the programs below. None of it is in this repository except the two typefaces; each
installer downloads its pieces from the author's own page, at the version named here, into `$PREFIX`.

## HyperFrames (HeyGen)

- The `hyperframes` npm package 0.8.186, <https://github.com/heygen-com/hyperframes>. Apache License 2.0 (the
  package's `license` field).
- Renders the Graphics tab: a web page seeked frame by frame in a headless browser and encoded with FFmpeg. Installed
  at this pinned version into `$PREFIX/graphics/node_modules` by `scripts/setup-graphics.sh`; used unmodified.

## GSAP (GreenSock, part of Webflow)

- The `gsap` npm package 3.14.2, <https://gsap.com>, <https://github.com/greensock/GSAP>. Licence: the GSAP Standard
  "no charge" License, <https://gsap.com/standard-license>. It is not an open-source licence.
- Writes the motion of every Graphics template. Free of charge to use, commercial use included; the licence keeps GSAP's
  and Webflow's rights, and does not allow using GSAP to build a product that competes with Webflow's visual animation
  tools. It is installed from npm, not redistributed by this pack. Read the licence on gsap.com before building on it.

## Playwright and Chromium (Microsoft, Google)

- `playwright` 1.64.1, <https://github.com/microsoft/playwright>. Apache License 2.0.
- On a machine with no Chromium of its own, notably a DGX Spark (Linux arm64), `scripts/setup-graphics.sh` runs
  `npx playwright install chromium-headless-shell` to fetch the Chromium headless shell that HyperFrames renders in.
  Chromium (The Chromium Authors) is BSD-3-Clause, with third-party components under their own licences listed in the
  browser's `about:credits`. It runs as a separate program; none of it is bundled here.

## Node.js (OpenJS Foundation)

- Node.js 22.23.4, <https://nodejs.org>. MIT License, with the licences of the libraries it bundles at
  <https://github.com/nodejs/node/blob/main/LICENSE>.
- When the machine's own Node is older than 22, `scripts/setup-graphics.sh` unpacks the official tarball (SHA-256
  checked against nodejs.org) into `$PREFIX/graphics/node`, for HyperFrames only. Nothing is installed system-wide.

## Geist and Instrument Serif

- **Geist** and **Geist Mono**, Copyright 2024 The Geist Project Authors, <https://github.com/vercel/geist-font>.
  **Instrument Serif**, Copyright 2022 The Instrument Serif Project Authors, <https://github.com/Instrument/instrument-serif>.
- SIL Open Font License, Version 1.1, <https://openfontlicense.org>. The full text is beside each font:
  `app/static/fonts/OFL-geist.txt` and `app/static/fonts/OFL-instrument-serif.txt`, and the Graphics tab copies both with
  the font files into every project it renders. The fonts are used unmodified; they may be used, copied and bundled
  with software, but not sold on their own, and the licence text and copyright lines travel with them. Finish's title
  card and captions use the same two fonts.

## DejaVu Sans and Serif

- Bitstream Vera Fonts Copyright 2003 Bitstream, Inc.; changes by the DejaVu authors are in the public domain.
  Licence: <https://dejavu-fonts.github.io/License.html> (free to use, copy and bundle, not to be sold alone; a renamed
  modification may not carry the Bitstream or Vera name).
- Not bundled. Finish's title cards and the Night shift's title slates in the dailies reel draw with the DejaVu fonts
  already on the machine, when they are installed (fontconfig's sans, then `DejaVuSans.ttf`), else Helvetica.

## Mirage's Tesseract and Tesseract Converter

- **Tesseract** (`tsrct`) 0.4.0 by Mirage US LLC, <https://github.com/mirage-hq/tesseract>. Licence: Mirage's own terms,
  `TERMS.md` in the download and at <https://github.com/mirage-hq/tesseract/blob/main/TERMS.md>. It is free to download and
  use but is **not open source**. Renders Finish's final cut and mix on the person's own computer. What to know:
  - commercial use by a business whose yearly revenue is US$1,000,000 or more needs a separate written agreement with
    Mirage; personal, non-profit, educational and governmental use does not (the terms' revenue threshold, section 2.2.3);
  - the program may not be redistributed, sold, offered as a service, or used to build a product that competes with it
    (section 2.2.2), so this pack does not ship it: `scripts/setup-finish.sh` downloads Mirage's own release, checks its
    SHA-256, and installs it on the person's machine;
  - its releases send basic usage counts for some commands (no file names, paths or contents) unless
    `tsrct telemetry disable` is run. The studio never changes that setting.
- **Tesseract Converter** (`tsrct-conv`) 0.3.0, <https://github.com/mirage-hq/Tesseract-Converter>. MIT License, Copyright
  (c) tesseract-conv contributors. Turns the cut into a Premiere Pro or After Effects project; downloaded and checked the
  same way.

## kohya-ss's musubi-tuner

- <https://github.com/kohya-ss/musubi-tuner> at commit `f8a1b03794a49239a3539015075f5123d6c07d66` (version 0.3.6),
  Apache License 2.0. At that commit the repository has no separate LICENSE file; its README says "Other code is under
  the Apache License 2.0", and names directories modified from other projects that keep those projects' licences
  (HunyuanVideo's own licence for `hunyuan_model`; Apache-2.0 for Wan2.1, FramePack and comfy-kitchen).
- Trains the LoRA in Looks → Train a LoRA. Cloned unmodified into `$PREFIX/train/musubi-tuner` by `spark/setup-train.sh`.
  Licence text: the README's License section at that commit.

## bitsandbytes

- Tim Dettmers and the bitsandbytes Foundation contributors, <https://github.com/bitsandbytes-foundation/bitsandbytes>.
  MIT License.
- Gives the trainer its 8-bit AdamW optimizer, installed from PyPI into the training environment. Where it does not run
  on the GPU, the plain AdamW is used instead.

## Training files for MiniMax H3 (Comfy Org, ostris)

- `vae/minimax_h3_video_vae_fp16.safetensors` from Comfy-Org/MiniMax-H3 on Hugging Face: MiniMax H3's video encoder in
  fp16, which musubi-tuner reads. MiniMax H3 Community License Agreement, Copyright 2026 MiniMax, which limits the
  territories in which MiniMax H3 may be used: <https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE>.
- `ostris/minimax_h3_training_adapter` on Hugging Face, file `minimax_h3_training_adapter_v2.safetensors`: ostris's
  de-distillation adapter, merged under a LoRA while it trains and not used when it renders. The repository states no
  licence of its own. It is made from MiniMax H3 and is used under the MiniMax H3 Community License Agreement, above.
- Neither file is in this pack; the installer downloads each from its page and checks its SHA-256.

## pywebpush and cryptography

- **pywebpush**, the web-push-libs contributors, <https://github.com/web-push-libs/pywebpush>. Mozilla Public License
  2.0: its source files stay under that licence if changed and redistributed; this pack uses it unmodified from PyPI.
- **cryptography**, the Python Cryptographic Authority, <https://github.com/pyca/cryptography>. Apache License 2.0 or BSD
  3-Clause, at the user's choice.
- Sign and send the Night shift's Web Push notifications to a phone; installed by `oneshot-setup.sh` when they can be.

## faster-whisper (SYSTRAN)

- <https://github.com/SYSTRAN/faster-whisper>. MIT License. With the CTranslate2 conversion `faster-whisper-large-v3-turbo`
  by Mobius Labs (MIT), it hears the spoken words for the QA gate's and Voice's checks. Installed from PyPI; if it is
  not there that check is skipped.
