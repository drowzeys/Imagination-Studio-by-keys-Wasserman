# Two GPUs in one machine

One render on two NVIDIA cards in the same machine: every sampler pass of TensorFold's int8 FastH3 engine is split
between the cards by tile, the video decoder's chunks are shared between them, and the text encoder and decoders live
on the second card. Off unless asked for (`STUDIO_GPUS`); the output is the one-card output byte for byte.

Measured on two RTX PRO 5000 Blackwell (48 GB each, PCIe Gen5 x8 each behind one host bridge, Ryzen 9 9950X), 1280x736,
`fh8`, warm (models loaded), job time:

| Clip | One card | Two cards | |
|---|---|---|---|
| 5 s | 68.2 s | 39.1 s | 1.74x |
| 15 s | 273.2 s | 147.5 s | 1.85x |
| 30 s (`long`, two parts) | about 575 s | 309.3 s | about 1.86x |

A 15 s pass takes 15.6 s against 30.2 s on one card, the 15 s decode 12.0 s against 22.2 s. One card peaks at 47.3 GB
on a 15 s clip (the memory guard refuses it on a 48 GB card); two cards peak near 42 GB each.

## What it needs

- **Two NVIDIA cards of the same model, 40 GB or more each** (the install pairs them only then). Each card holds a
  whole copy of the int8 blocks (21 GB) besides its share of the pass: a 15 s clip peaks near 42 GB a card on 48 GB
  cards. Two cards are used; a third is not.
- **A TensorFold engine with the two-GPU exchange.** The pinned engine (`TF_COMMIT` in `spark/build-engine.sh`) has
  none: the exchange is TensorFold `studio-two-gpus` (https://github.com/davidlowjw/TensorFold/tree/studio-two-gpus,
  one commit on `upstream/h3-cuda-parallel`). `spark/build-engine.sh` builds a checkout for the card it finds:

  ```bash
  git clone -b studio-two-gpus https://github.com/davidlowjw/TensorFold.git
  TF_SOURCE="$PWD/TensorFold" PREFIX=/path/to/the/install bash spark/build-engine.sh  # the PREFIX it was installed with
  ```

  With an engine that lacks it the Studio renders on one card and says so in the ComfyUI log; nothing else changes.
  An upgrade builds the pinned engine again: build this one again after it.
- **For the fastest exchange, PCIe peer to peer that works.** On a desktop with the IOMMU in DMA mode (AMD-Vi or VT-d;
  `cat /sys/kernel/iommu_groups/*/type` says `DMA` or `DMA-FQ` for the cards' groups) copies between the cards report
  success and move nothing, though the driver says peer access is supported (CUDA's programming guide: peer to peer
  is not supported on bare-metal Linux with the IOMMU on). Boot with `iommu=pt` (`GRUB_CMDLINE_LINUX_DEFAULT`, then
  `update-grub` and a reboot) or turn the IOMMU off. GeForce cards have peer to peer off in the driver. Either way the
  Studio checks before it uses peer copies (the cards' IOMMU groups, then a copy each way between them) and otherwise
  exchanges through host memory: correct, a little slower (a 15 s clip 164.8 s against 146.3 s).

## Turning it on

In the Studio's environment (the systemd unit or the shell that starts `scripts/app.sh`):

```bash
STUDIO_GPUS=0,1            # nvidia-smi indexes, the head's card (STUDIO_GPU) first
STUDIO_TP_EXCHANGE=p2p     # p2p | dma | shm; p2p falls back to dma where peer copies do not work
```

`STUDIO_GPUS` makes `spark/gpu.sh` expose both cards, the memory guard ask about both, and refuses 4.3's local GPU
workers (one card a worker), which would end the pairing.

## What runs where

- **Each sampler pass** (`spark/tensorfold_h3/`): rank 0 is ComfyUI's own engine on the first card, rank 1 a process
  of its own on the second (`tp_rank.py`). Each block the ranks swap their tiles' keys and values.
  - The split follows the passes: after each one, rows move toward the rank that waited less for the other
    (`balance()`). The first card also holds the prefix tiles (text, a start image), which every tile attends to.
  - With peer to peer, each rank computes its q k v work in parts and sends a part while it computes the next
    (`STUDIO_TP_CHUNKS`, 4).
- **The video decode** (`spark/tensorfold_h3/decode2.py`, routed by `spark/gpus.py place_decode`): MiniMax H3's
  decoder decodes independent temporal chunks and blends their overlaps in order. The chunks go to whichever card is
  free, each from a fully loaded copy of the decoder replaying a CUDA graph per tile batch (launching the kernels one by
  one costs about as much CPU as the GPU spends on them, so two cards driven from one process waited on each other);
  the blending runs on the decoder's card as before.
- **Text encoder and decoders** on the second card (`spark/gpus.py place`).

## Switches

| Variable | Default | |
|---|---|---|
| `STUDIO_GPUS` | unset | `0,1`: render on both cards |
| `STUDIO_TP_EXCHANGE` | `shm` | `p2p` (GPU to GPU), `dma` (bulk copies through host memory), `shm` |
| `STUDIO_TP_CHUNKS` | `4` | parts of the q k v work with `p2p`; `0` for one |
| `STUDIO_TP_BALANCE` | `on` | `off` keeps an even split by rows |
| `STUDIO_DECODE_SPLIT` | `on` | `off` decodes on one card |
| `STUDIO_PEER_COPIES` | `auto` | `off`: every copy between the cards through host memory |
| `STUDIO_TP_REGION_GB` | `4` | the exchange region; a sequence that needs more renders on one card |
| `TF_H3_PROFILE` | unset | `1`: each pass's stages on both ranks in the ComfyUI log |

## Checking it

- `tests/tf_h3_tp_rig.py` replays one pass outside ComfyUI on one GPU and on two (each exchange) and says which rows
  differ, in seconds: set `TF_H3_CAPTURE=/path/pass.pt` on the Studio and render one clip to capture a pass, then
  `CUDA_VISIBLE_DEVICES=0,1 $PREFIX/venv/bin/python tests/tf_h3_tp_rig.py /path/pass.pt --lib $PREFIX/tf-h3/libtf_h3.so --modes shm,dma,p2p`.
- A render is checked by its bytes: a cold seed-42 clip on two cards has the md5 of the same clip on one card.

## Limits

- Two cards of one model. Different models of one architecture should work (the split and the decode adapt to
  speed) but are untested; cards of different architectures cannot share one engine build.
- The memory guard adds the cards' memory together, while each card holds the whole weights: near the 40 GB floor it
  can admit a clip that does not fit.
- A LoRA clip renders on one card (the second card holds the checkpoint's weights).
- A long video's parts still run one after another (each opens on the last frame of the one before).
