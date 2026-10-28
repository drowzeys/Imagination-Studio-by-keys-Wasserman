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

- **Name the head card when the machine has more than one compute GPU.** `STUDIO_GPU` is its nvidia-smi index, not a UUID. Indexes differ by machine, so the installer does not pick one of several compute cards. A single compute card behind a display GPU is used on its own. `nvidia-smi` ignores `CUDA_VISIBLE_DEVICES`; the Studio passes `-i`. Add the other cards through **Machine → Sparks**, rather than maintaining a second launcher or localhost SSH worker by hand.
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

## One to four local GPUs

Launch one head with the same `PREFIX`, `SPARK_MODELS`, `STUDIO_HOME` and explicit numeric `STUDIO_GPU` as above.
Then open **Machine → Sparks**:

1. **Local GPUs** shows every detected card separately, including its own VRAM and current GPU readings. The head
   has no Start/Stop control. Another card can be started only when it has dedicated VRAM and no external compute
   workload or competing Studio ownership. A display card asks for consent.
2. Press **Start** on an additional card. This creates a private local Studio process, app/Comfy ports, home and GPU
   lock for that UUID. It shares `PREFIX` and `SPARK_MODELS`; it does not copy the core weights or need SSH.
   Starting the worker does not preload a model.
3. A Studio manages **four local GPUs including its head**. Extra cards remain visible; stop and disable/remove a
   worker before choosing a different subset. Unticking **use** stops new assignment, not its process, and a running
   unticked worker still occupies a slot. Missing cards remain identified by UUID, never replaced by the new occupant
   of their old index.
4. Set **Default VIDEO placement** to the desired card. **Require** waits while that worker is busy or stopped and
   never falls back; a failure after dispatch fails the job explicitly. **Prefer** can use the head or another
   eligible worker when the selected worker is unavailable or cannot fit the clip. **Automatic** uses the worker count.
5. **Check** only refreshes status. **Stop** drains that owned worker and its model processes, leaving external
   workloads and the other GPUs alone. Quitting the head stops its local workers too. Remote Sparks remain separately
   managed through their existing SSH controls.

On two cards, a useful separation is required VIDEO placement on the second GPU, with pictures and native voice/music
on the head. This avoids the head's audio/image switches evicting the second GPU's video model. It is not a model
preloader or a guarantee that every model remains resident: existing idle/memory guards and switches on the same card
still apply. Only independent single video jobs move to workers; images, audio, training, exports and long/compound
jobs remain on the head. No render spans multiple cards and their VRAM is never added together.

Optional LoRAs are per-studio, not inherited from the head's registry. Install the desired adapters into the worker's
`<head studio>/sparks/local/<GPU UUID>` studio folder with the existing `app/loras.py install GROUP|all` CLI, using that
folder as `STUDIO_HOME`, and press **Check**. The same authors' licences and gates apply. The scheduler checks actual
worker capabilities and does not silently drop a missing adapter.

**Shared host RAM matters even with four large cards.** Local queues coordinate through a private per-user RAM ledger;
the existing measured peaks and error margins remain unchanged, with at least max(8 GiB, 10% of physical RAM) free.
Already allocated anonymous RAM in this GPU session's identity-validated model servers is not counted twice; file
cache and other GPUs are not credited. Other active GPUs still see the full peak reservation. Unmeasured GPU work is
exclusive rather than assigned a fabricated budget. Jobs can wait for **shared host RAM** even when their card has room.
This does not replace the per-card preflight or the runtime guards.

**Physical smoke, not a throughput benchmark:** two RTX PRO 6000 Blackwell Max-Q 96 GB cards, 128 GB-class host RAM.
GPU 1 rendered 864x480 FastH3 at 22 frames and four passes, producing H.264 with AAC. A cold clip took 26.04 s; the
next clip took 4.12 s. With native Drama 3 resident on GPU 0, another clip took 4.11 s on the same GPU 1 Comfy process.
Native MiniMax Music 3 and a 44.1 kHz PCM voice take also completed on the head. The UI showed both cards; stopping
GPU 1 through it freed that card while preserving the resident head voice process. One run per case, not a scaling
claim. Three/four GPUs, excess inventory, missing/reordered UUIDs and mixed capacities have deterministic test coverage;
three/four physical cards have not been measured.

## How memory works on a card

A DGX Spark has one 128 GB memory for the CPU and the GPU. A card has its own 96 GB, and the machine's RAM is separate.
The Studio reads the card's own memory (`nvidia-smi`) wherever it decides whether something fits:

- **Before a clip is queued**, the room is the card's free memory, including what a loaded Drama 3, Music, or Bolt
  will give back (each is unloaded before the job). **Before anything loads**, those engines are already gone, so the
  room is the card's free memory then. The card keeps 2 GB free (`VRAM_MARGIN_MB`), already taken out of that room.
  The machine's RAM still caps it: the models are read through RAM and ComfyUI's guard stops a render on RAM, so a
  job also has to leave the usual tenth of RAM free. Whichever runs out first refuses the job, and the message names
  it ("this GPU" or "this machine"). `STUDIO_ROOM_MB` (a card smaller than the models, [rtx-5090.md](rtx-5090.md))
  replaces the card's room with a RAM budget.
- **Voice (Drama 3, about 42 GB)** stops the studio's ComfyUI first when the smaller of the card's free memory and
  the machine's available RAM is under what Drama 3 needs. So Voice never loads beside the video models it would not fit with.
- **Bolt** is held beside a job only when the card has room for both. Otherwise it is unloaded first, as on a Spark.
- **ComfyUI offloads by itself.** When a model does not fit the card's free memory, it stages the weights in RAM and
  streams them to the card. That is slower; the first load after a LoRA job took minutes in the simulation.
- **The status bar** leads with the card's memory. It does not also quote system RAM as a second memory.
- **No time estimate yet.** The times the page shows were measured on a GB10. A card shows none until it has a warm
  sample of its own for that canvas (`CARD_WARM` in `app/server.py`, empty for now), so a clip on a card says only
  how much memory it needs.

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
