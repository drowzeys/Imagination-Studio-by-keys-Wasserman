# Imagination Studio on an NVIDIA RTX Pro 6000 Blackwell (96 GB)

The core install runs on an RTX PRO 6000 Blackwell: pictures, FastH3 video with sound, and a clip. The memory
figures below were taken on a DGX Spark capped to 96 GB.

## What you need

| | Minimum | Notes |
|---|---|---|
| GPU | NVIDIA RTX Pro 6000 Blackwell, 96 GB (Workstation, Max-Q or Server Edition) | Compute capability 12.0 (sm_120). A smaller card runs pictures only (`--image-only`, 48 GB). |
| CPU and OS | x86_64, Ubuntu 22.04 or 24.04 | |
| System RAM | 64 GB; 128 GB recommended | ComfyUI stages model weights in RAM when it offloads (up to about 35 GB seen). |
| Driver and CUDA | NVIDIA driver 580 or newer, CUDA toolkit 13 (`nvcc` 12.9 or newer) | The Studio's engines are compiled on your machine for sm_120. |
| Disk | 125 GB core, about 400 GB for everything | The same models as the DGX Spark build. |
| Tools | `git`, `ffmpeg`, `curl`; Docker is optional | Docker's prebuilt engines are for the DGX Spark (aarch64) and are not used on x86_64. |

## Install

```bash
# 1. the driver, the CUDA 13 toolkit and the tools (Ubuntu, NVIDIA's CUDA repository already added)
sudo apt install -y git ffmpeg curl cuda-toolkit-13-0
export PATH=/usr/local/cuda/bin:$PATH && nvcc --version     # 12.9 or newer

# 2. the Studio. The same four values again for scripts/app.sh. /data/studio is an example path.
#    No flag is the core. The flags are in the README, under Install flags.
#    STUDIO_GPU is the nvidia-smi index, and only when more than one GPU is not driving a display.
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
cd Imagination-Studio-by-keys-Wasserman
PREFIX=/data/studio SPARK_MODELS=/data/studio/models \
STUDIO_HOME=/data/studio/ImaginationStudio STUDIO_GPU=1 \
bash oneshot-setup-rtx-6000.sh

# 3. start it. The install's DONE line prints STUDIO_GPU only, so pass the same four values.
#    Then open http://127.0.0.1:7870
PREFIX=/data/studio SPARK_MODELS=/data/studio/models \
STUDIO_HOME=/data/studio/ImaginationStudio STUDIO_GPU=1 \
bash scripts/app.sh
```

What the installer does differently on this card:

- **Name the card when the machine has more than one compute GPU.** `STUDIO_GPU` is the nvidia-smi index for this process. Indexes are not the same on every machine, so the installer does not pick one of several compute cards. A single compute card behind a display GPU is used on its own. `nvidia-smi` ignores `CUDA_VISIBLE_DEVICES`; the studio passes `-i`. One studio uses one card. A second card is a second process with its own `STUDIO_GPU`, `PORT`, `SPARK_COMFY_PORT`, `STUDIO_HOME` and `STUDIO_GPU_LOCK`, sharing the same install.
- **It checks the card, not the machine.** It wants 90 GB or more of VRAM for video and 64 GB of RAM beside it.
- **It builds every CUDA engine from source for sm_120.** The video engine (TensorFold's H3 int8 blocks), Music
  (MiniMax-Music3) and Voice (Fish Audio Drama 3) each take a few minutes. Each build reads the card's compute
  capability from `nvidia-smi`; `TF_SM=120` forces it. A GB10 build (sm_121) does not run on sm_120. Compiled for
  sm_120 on a Spark: the video engine, `music3-generate` and the Drama 3 engine all build and carry sm_120 code.
- **The Bolt brain needs llama.cpp.** TensorFold's Bolt engine is built for the DGX Spark only, so on x86_64 the
  installer says so and goes on with everything else. Run Bolt through llama.cpp instead:

  ```bash
  git clone https://github.com/ggml-org/llama.cpp ~/bolt/llama.cpp
  cmake -S ~/bolt/llama.cpp -B ~/bolt/llama.cpp/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 && cmake --build ~/bolt/llama.cpp/build -j --target llama-server
  hf download nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF --local-dir ~/bolt/models
  export BOLT_BACKEND=llamacpp       # then start the app; the brains' address stays empty (studio:bolt)
  ```

  If this llama.cpp has no NVFP4 kernels for the GGUF's Mamba tensors, use a copy with those
  tensors rewritten as Q8_0, as on a Mac. Or leave Bolt out and set any OpenAI-compatible server in Settings →
  Connections.

## How memory works on a card

A DGX Spark has one 128 GB memory for the CPU and the GPU. A card has its own 96 GB, and the machine's RAM is separate.
The Studio reads the card's own memory (`nvidia-smi`) wherever it decides whether something fits:

- **Before a clip is queued and before anything loads**, the room is the smaller of the machine's RAM and the card's
  free memory. The card keeps 2 GB free (`VRAM_MARGIN_MB`); the RAM keeps the usual tenth.
- **Voice (Drama 3, about 42 GB)** stops the studio's ComfyUI first when the card's free memory is under what Drama 3
  needs. So Voice never loads beside the video models it would not fit with.
- **Bolt** is held beside a job only when the card has room for both. Otherwise it is unloaded first, as on a Spark.
- **ComfyUI offloads by itself.** When a model does not fit the card's free memory, it stages the weights in RAM and
  streams them to the card. That is slower; the first load after a LoRA job took minutes in the simulation.
- **The status bar** shows the card's memory beside the machine's ("card 52 of 96 GB").

## The simulation

**Hardware:** a worker DGX Spark (GB10, 128 GB, 2026-10-20).

**How the 96 GB card was imitated:**
- Every CUDA process the test Studio started ran through a CUDA MPS server with a per-process device memory limit of 96 GB
  (`CUDA_MPS_PINNED_DEVICE_MEM_LIMIT=0=96G`). An allocation past it fails as it would on the card.
- Checked first: under an 8 GB limit, 6 GB was allocated and 10 GB was refused.
- Every second, a sampler added up what each CUDA process held, so the whole card (all processes together) is visible
  too.
- `STUDIO_VRAM_GB=96` made the app itself reason as on a 96 GB card.

**Phase 1:** the Studio's checklist (`scripts/studio-check.py`), every feature at its test size, under the cap.

- **40 passed:** pictures, video, the Sharp Spec 8 s, a 16 s long take, Ref2VA from a picture and from video and
  audio, Upscale, Remove a person, Turnaround, Music 10 s and 30 s, sound effects and foley.
- **None of the 6 failures was memory.** They were: no local brain server and no Claude sign-in on the test machine
  (3), the Voice engine not installed there (2), and Motion Previs' depth model missing there (1).
- **The whole card never held more than 72.0 GB.**

| Job (test size) | Peak on the card |
|---|---|
| Pictures: Qwen, MOSS-Image, edits | 22 to 25 GB |
| Video, 864x480 (text, from a picture, LoRA, first and last frame) | 50 to 53 GB |
| Long take, 16 s at 864x480 | 56 GB |
| Sharp Spec, 1376x768, 8 s | 59 GB |
| Reference to video (Ref2VA) | 67 GB |
| Upscale | 67 GB |
| Turnaround | 69 GB |
| Remove a person | 72 GB |
| Music, 10 s | 15 GB |

Peaks are per job, from 1 s samples; the jobs' boundaries are approximate.

**Phase 2:** the heaviest jobs back to back, with the app reasoning as on a 96 GB card. Each job starts while the one
before still holds its models.

| Step | Result | Time | Peak on the card |
|---|---|---|---|
| The largest clip the app offers: 1376x768, 243 frames (10 s), 8 passes | done | 493 s | 62.2 GB |
| A Drama 3 voice take right after it | done; ComfyUI stopped first (34 GB free on the card, Drama 3 needs 50) | 42 s | Drama 3 alone: 39.6 GB |
| A 30 s song (MiniMax-Music3) right after | done; Drama 3 unloaded first | 99 s | 15.3 GB |
| The largest clip with a LoRA (merged into the int8 engine) | done; Music unloaded first | 508 s | 62.2 GB |
| A MOSS-Image picture, 1344x768 | done | 57 s | 56.8 GB |

- **The whole card never went over 62.2 GB in this phase.** No second was over 94 GB.
- **Why the voice step matters.** Before this port the app decided from the machine's RAM. On a card with a big
  machine, that says there is room, so Drama 3 would have loaded beside the 62.1 GB ComfyUI: 101.7 GB on a 96 GB card,
  a CUDA out-of-memory error. Reading the card, the app stopped ComfyUI first. Drama 3 and ComfyUI never held memory at
  the same second.
- **A note on the times.** They are a GB10's, from the simulation above.

Credits: the Studio's engines run on Ash Hart's TensorFold, ComfyUI (Comfy Org) and llama.cpp (Georgi Gerganov and the
ggml contributors). The models are MiniMax's, FastVideo's, Fish Audio's, NVIDIA's, Qwen's, Black Forest Labs' and
OpenMOSS's (see CREDITS.md).
