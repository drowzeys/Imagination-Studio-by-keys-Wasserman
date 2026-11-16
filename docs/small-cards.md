# Imagination Studio on a small card (RTX 5060 Ti 16 GB, RTX 4060 8 GB)

[Back to the README](../README.md)

A GeForce under 24 GB beside less than 64 GB of RAM: an RTX 5060 Ti (16 GB, sm_120), an RTX 4060 (8 GB, sm_89), and
the like. The Studio's video models are sized for a DGX Spark's 128 GB pool or a 96 GB card, so this is an opt-in
tier with its own install flag, `--small`, and its own limits. **Nothing in this tier is measured yet**: the figures
below are the models' sizes and what the Studio does with them, not render times. A report of the card, the RAM and
the times is welcome as an issue.

## What runs

| | On a small card |
|---|---|
| Pictures (Qwen-Image-2.1-Turbo) | Yes, on Comfy-Org's **8-bit Turbo checkpoint** (7.3 GB, in place of the 14 GB bf16 file), which a 16 GB card holds whole; the 9 GB text encoder runs first and is let go. On 8 GB ComfyUI streams what the card does not hold: slower, but it is the part that is known to run. `SPARK_IMAGE_WEIGHTS=bf16` picks the bf16 file once it is there. |
| Video (FastH3) | Tried, on FastH3's **8-bit transformer** (22 GB, in place of the 44 GB bf16 file) on ComfyUI's own blocks. Each pass streams the blocks the card does not hold from RAM, and with less than 48 GB of RAM the text encoder (27 GB) and the transformer (22 GB) do not sit in RAM together, so the kernel pages between them from disk. Expect a 5 s clip at 864x480 to take a long time, and keep clips short. |
| TensorFold's int8 engine | Not built. It keeps all 50 of FastH3's blocks on the card in int8 (22 GB), so it cannot start on a card under 24 GB. `bash spark/build-engine.sh` builds it anyway on a 24 GB card and up. |
| LoRAs | Not with the 8-bit file: a LoRA is merged into the bf16 transformer. The essential bundle is left out, and a clip with a LoRA says what to add (the 44 GB bf16 file; `hf download FastVideo/FastVideo-FastH3-Comfy diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors --local-dir $SPARK_MODELS/FastH3-Comfy`). |
| Reference to video (Ref2VA) and Character Swap | `--ref2va` fetches the **8-bit Ref2VA** (21 GB) in place of the 40 GB bf16 file. Reference clips run the model's own 20 passes on it, since the 4-pass Turbo adapter only merges into the bf16 file; a clip that asks for Turbo says so and runs 20. `--swap` runs on the same file plus two small LoRAs and SAM 3.1 (2.1 GB), so the two flags together cost 21 GB once. Streamed to the card like FastH3: slow, not measured. |
| H3 base, Music 3, YuE2, Voice, Bolt | Their flags are taken, with their models' sizes (40, 22, 14, 41 and 25 GB), and run on ComfyUI's streaming the same way or not at all; none of them is tried here. Voice's engine and Bolt's are TensorFold's and hold their weights on the card. |
| Motion graphics, Finish, the timeline, planning, the script | As on any machine: they do not need the card. |

The memory guard's estimates are a DGX Spark's (the models held whole: 54 to 75 GB), so on a small card they would
refuse every clip and say nothing about what really fits. An install made with `--small` leaves a mark
(`$PREFIX/small-card`) and the guard then **warns instead of refusing** (`spark/memory_guard.py`, `small_card`), as it
does on an RTX Spark N1X: the clip is tried, and one that really runs out fails with ComfyUI's error. It is the
install's choice, not the card's size: a 32 GB RTX 5090 set up as in [docs/rtx-5090.md](rtx-5090.md) keeps its strict
guard. `STUDIO_MEMORY_GUARD=strict` turns refusals back on; `STUDIO_SMALL_CARD=1` or `0` overrides the mark. The watcher
beside ComfyUI still stops a render when the machine's available RAM falls under its floor, but on a small-card
install that floor is a tenth of the RAM (3 GB on 31 GB) instead of the Spark's 8 GB: with a 22 GB transformer
streamed through 31 GB, 8 GB held back stopped every clip ("the render was stopped to protect the machine"). The
kernel's OOM killer and the machine memory guard stand behind it. `SPARK_MEMORY_FLOOR_MB` names any floor. Close
browsers and other large programs before a clip: on 31 GB they are the difference between fitting and paging.

## What you need

| | Notes |
|---|---|
| GPU | An NVIDIA card with 8 GB or more. Under 12 GB, pictures are the known part and a clip is tried. |
| System RAM | **24 GB or more** (the 8-bit transformer alone is 22 GB, and the models are read through RAM). 48 GB or more keeps a clip's text encoder and transformer in RAM together. Swap (zram or a swap file) keeps a clip going when the two do not fit; it is slow. |
| Driver and CUDA | A driver that runs CUDA 13's PyTorch (580 or newer). `nvcc` is not needed: the engine is not built. |
| Disk | About 80 GB for the core (the 8-bit Qwen-Image 19 GB, FastH3's 8-bit file and its text encoder and decoders 53 GB, the environment, motion graphics and Finish); 26 GB with `--image-only`. A copy of any of these files already on the machine (another ComfyUI's `models` folder) is linked, not downloaded again: `STUDIO_SCAN_ROOTS` names folders the scan should look in first. |
| Tools | `git`, `ffmpeg`, `curl`. Fedora: `sudo dnf install git ffmpeg curl`; Ubuntu: `sudo apt install git ffmpeg curl`. |

## Install and run

```bash
nvidia-smi --query-gpu=index,name,memory.total,display_active --format=csv   # which index is the card you mean
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
cd Imagination-Studio-by-keys-Wasserman
STUDIO_GPU=0 bash oneshot-setup-rtx-6000.sh --small        # the core: pictures, video on the 8-bit file, graphics, Finish
STUDIO_GPU=0 bash oneshot-setup-rtx-6000.sh --small --image-only   # pictures only (33 GB)

STUDIO_GPU=0 bash scripts/app.sh                            # http://127.0.0.1:7870, the same STUDIO_GPU
```

- **Name the card.** `STUDIO_GPU` is its nvidia-smi index. On a machine with two compute cards and no display on
  either (an RTX 5060 Ti beside an RTX 4060), the installer and the app stop and ask for it rather than pick one.
  Choose the larger card; the second one is not used by this Studio (two 8 to 16 GB cards do not pair: pairing wants
  40 GB a card, [docs/two-gpus.md](two-gpus.md)).
- **`--small`** lifts the installer's 90 GB card floor and its 64 GB RAM floor (to 24 GB), fetches FastH3's 8-bit
  transformer and Qwen-Image-2.1-Turbo's 8-bit checkpoint in place of the bf16 ones, skips the engine build and the
  LoRA bundle, and renders a 56-frame test clip instead of a 124-frame one.
- **An H3 text encoder you already have** (another build of `qwen3vl_32b_minimax_h3`, such as the NVFP4 one at
  15.7 GB) is kept in place of the 27 GB int8 download, and clips use it by default when it is the only one there
  (`TEXT_ENCODER=<file name>` or the app's text encoder choice picks another). An NVFP4 file runs natively on a
  Blackwell card (an RTX 5060 Ti); on an Ada card (an RTX 4060) the int8 one is the safer choice. On 31 GB of RAM
  the smaller encoder is the difference between paging and not.
- **Weights you already have** (another ComfyUI's `models/diffusion_models`, `clip`, `vae`) are found by the Studio's
  scan and linked where it looks, before each download. The scan walks your home folder with a time budget, so name
  the folders when they are deep: `STUDIO_SCAN_ROOTS=/path/to/ComfyUI/models:/mnt/models` on the install command, or
  `python3 app/discover.py --scan --link` with it first. **Machine → Models & weights** shows what was linked. An install made with it stays a small-card install through `--upgrade` (the 8-bit file
  is what it holds).
- **Clips render on the 8-bit file by default** when the bf16 file is not there (`SPARK_WEIGHTS=int8` is the default
  `spark/spark_generate.py` picks then; `SPARK_WEIGHTS=bf16` after you add the bf16 file). `SPARK_ENGINE=comfy` is the
  default without the engine.
- **Keep the card free.** A game, a second model server or a browser's GPU process on the same card takes from
  what ComfyUI can hold; the guard's warning names the Studio's figure, not theirs.
- **Keep clips short and small** at first: 864x480, 56 to 124 frames, 8 passes. Longer clips add rows to every pass
  that the card streams through.

## The two cards on one machine

An RTX 5060 Ti and an RTX 4060 in one machine are two compute cards with no display on either, so `STUDIO_GPU` is
required and names which one this Studio uses. A second Studio on the other card is another process with its own
`STUDIO_GPU`, `PORT`, `SPARK_COMFY_PORT`, `STUDIO_HOME` and `STUDIO_GPU_LOCK` (as for an RTX Pro 6000,
[docs/rtx-pro-6000.md](rtx-pro-6000.md)); both Studios share the install and the models, and read the same RAM, so on
31 GB of RAM run one at a time.

## Not here

- No TensorFold engine, no LoRAs on the 8-bit file, no measured times.
- The RTX 4060's sm_89 would build the engine's kernels (they are int8 `mma.sync` and `cp.async`, sm_80 and up), but
  the engine needs the card to hold the blocks, which an 8 GB card does not: so it is not built, whatever the chip.
- Windows: the native Windows build targets Windows on Arm with an RTX Spark ([docs/windows.md](windows.md)); a
  GeForce under Windows is not set up by this tier. On Linux it is.
