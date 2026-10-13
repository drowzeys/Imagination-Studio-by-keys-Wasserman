# More than one DGX Spark: what it buys, measured, and the order to build it

State on 2026-10-13. The render-node layer (a job farm) is built and tested with two server processes on one machine.
Nothing here has run on a second Spark yet: setting one up needs an NFS export and a mount that were not permitted
in the session that wrote this (see "What needs a person"). The single-clip numbers below are ceilings worked out
from measurements, not results.

## Where a clip's time goes on one Spark

From 42 saved render logs (`[tensorfold] stages:` lines; medians) and the README's measured rows. 5 s, 864x480, 124
frames unless a row says otherwise.

| Route | Whole clip | Conditioning (text encoder, reference encode) | Transformer passes | Video decode | Sound decode |
|---|---|---|---|---|---|
| FastH3, 8 passes, TensorFold int8 engine | 72 s | 12.6 s | 49 to 57 s (7.1 s a pass; 8.1 s from a first frame) | 13.4 s | 0.4 s |
| FastH3, 8 passes, ComfyUI's own blocks (a LoRA clip) | 118 to 150 s | about 13 s | 10.5 s a pass, plus a ComfyUI restart and a bf16 load | about 15 s | 0.6 s |
| Ref2VA with the Turbo adapter, 4 passes, one picture | 102 s | 13.8 s | 68.6 s (includes the model's load) | 15.7 s | 0.6 s |
| the same with one reference video | 202 s | 49.5 s (the video's own encode) | 134.5 s | 15.1 s | 0.6 s |
| the same with two reference videos | 364 s | 98 s | 246 s | 15.3 s | 0.6 s |
| Ref2VA, 20 passes, one picture | 316 to 326 s | 14.3 s | 284 to 294 s (about 14 s a pass) | 15 s | 0.6 s |
| Ref2VA, 20 passes, one reference video | 677 s | 45.9 s | 613 s | 14.3 s | 0.6 s |
| MiniMax H3 first-and-last frame, 25 passes, bf16 | 324 to 362 s | not split out | not split out | not split out | |
| FastH3 1280x720, 8 passes | about 190 s | not split out | 19.7 s a pass | not split out | |
| Remove a person, 5 s | 11 to 13 min | mask 40 to 58 s | overlapping 22-frame windows in a relay | | |
| Upscale 5 s to 2 MP | 303 to 319 s | | tiled re-draw | | |
| Turnaround sheet, 1024 | 174 to 188 s | | | | |
| Edit a picture, 1024x1024 | 15 s (five views 86 s) | | | | |

Two facts decide the rest:

- On the fast route **about 26 s of a 72 s clip is not the transformer** (text encoder and decoders). No way of
  splitting the transformer touches that part.
- Every video route the Studio ships runs **guidance-distilled, one forward a pass** (`cfg 1.0`; `SPARK_REF_CFG` is an
  experiment and `--cfg 4.0` is the base image model). Splitting the conditional and unconditional forwards over two
  Sparks, the usual cheap and exact 2x, has nothing to split here.

Not measured: the per-stage profile inside one engine pass (`TF_H3_PROFILE=1`), which needs a ComfyUI start of its
own on a free GPU.

## The fabric

`ib_write_bw` from one Spark to another, one rail, one queue pair, 4 MB messages: **109 Gb/s (13.6 GB/s)**. The second
rail and several queue pairs were not measured.

## The ways to use N Sparks

Speedups are for the whole job. "Ceiling" means compute splits perfectly and the only cost added is the bytes moved
at the measured 13.6 GB/s; real collectives cost more (in the GLM-5.3 engine they were the bound).

| | What is split | 2 Sparks | 3 | 4 | 5 | Work | Quality risk | P(success) |
|---|---|---|---|---|---|---|---|---|
| a. Job farm | Whole jobs: a project's segments, takes, seeds, QA retakes, angle sets, turnaround views | 2.0x on a batch | 3.0x | 4.0x | 5.0x | Built (below) | None: each job is the one-Spark job | 90% once a second node is set up |
| b. Span parallel | One long video: spans between keyframe stills, each rendered first-and-last-frame on its own Spark, joined | about 1.8x | about 2.6x | about 3.3x | about 4x | 1 to 2 weeks | Sound at the joins; motion that should carry across a keyframe stops and restarts | 60% for picture, 40% for seamless sound |
| c. Pipeline split | Text encoder and decoders on another Spark | 1.15 to 1.2x on a 72 s clip | same | same | same | 3 to 5 days | None | 70% |
| d. CFG split | The two forwards of a guided pass | not applicable: no shipped route has two forwards | | | | | | |
| e. Sequence parallel | The rows of one pass across ranks | 1.2x (FastH3 480p), 1.5x (20 passes) | 1.5x / 1.8x | 1.8x / 2.0x | 2.0x / 2.2x | 4 to 8 weeks of engine work | Rounding differs from one Spark unless made exact | 35% to reach 80% of the ceiling |
| f. Windows and tiles | Remove a person's relay windows, Upscale's tiles | 1.0x (the relay is serial by design) / up to 2x for tiles | | | | 1 week each | Seams between tiles | 50% |

### e. Sequence parallel, worked out

Read from the engine and the model code: hidden size 7168 (56 heads of 128), 50 blocks, one stream of text, video and
sound rows, sparse tile attention (tiles of 4x4x4 tokens, 20% kept). A 5 s 864x480 clip is about 12,000 to 13,000 rows
(from the tile geometry; not read from a run).

The per-row work (norms, the QKV, attention-out and MLP products) splits cleanly: each rank does rows/N. Attention is
the exception: a rank's queries can pick key tiles anywhere in the clip, so every rank needs every other rank's keys
and values each block. That is one all-gather a block of about 13,000 x 7168 x 2 tensors x 2 bytes = 373 MB, 18.6 GB
a pass.

| Ranks | Compute a pass | Bytes a rank receives a pass | Time to receive at 13.6 GB/s | Pass, ceiling | Against 7.1 s |
|---|---|---|---|---|---|
| 1 | 7.1 s | | | 7.1 s | 1.0x |
| 2 | 3.55 s | 9.3 GB | 0.69 s | 4.24 s | 1.67x |
| 3 | 2.37 s | 12.4 GB | 0.91 s | 3.28 s | 2.16x |
| 4 | 1.78 s | 14.0 GB | 1.03 s | 2.81 s | 2.53x |
| 5 | 1.42 s | 14.9 GB | 1.10 s | 2.52 s | 2.82x |

A whole FastH3 480p clip keeps its 26 s of text encoder and decoders: 72 s becomes about 60 s on two Sparks (1.2x)
and about 36 s on five (2.0x, with the decode split too). Routes with more passes or more pixels gain more because
that fixed part is smaller. Rows do not have to divide evenly, and nothing here needs a power of two: ranks take
whole tiles, the last rank takes the remainder.

What it needs: kernels that take keys and values from a buffer other ranks filled; a transport (the RoCE exchange in
our TensorFold tree carries a gather only up to 1 MiB today, so this is NCCL or a larger RoCE path); int8 keys and
values on the wire would halve the bytes; a gate at every world size comparing with one Spark.

## What is built

`app/nodes.py`, `app/static/nodes.js`, `spark/setup-worker.sh`, `tests/test_nodes.py`.

- **Nodes.** `nodes.json` in the studio folder lists other machines running the same studio. A queued job goes to a
  free node: the files it reads are sent, the node renders it through its own job API, progress and a cancel are
  relayed, and the outputs come back to the same paths. `shared: true` marks a node whose studio folder is this one.
- **Token.** A node only answers a caller that presents its token (`node-token` in its studio folder, or
  `STUDIO_TOKEN`). With a token set, any caller from another machine needs it; a browser gets it once as `?token=`.
- **Failure.** A node that refuses a job (a model or LoRA it lacks) is skipped for that job. A job a node fails, or a
  node that stops answering for 30 s, sends the job back to the queue for another machine, twice at most.
- **Queue kept.** `jobs.json` keeps the queue across a restart: finished cards stay, queued jobs run, a job on a node
  is picked up again, a job that was rendering here is marked failed. `STUDIO_JOB_STORE=0` turns that off.
- **This machine.** "This machine renders too" can be switched off, so the head only hands jobs out; it still takes
  what no node can.
- **Order.** A clip that opens or ends on a clip still being made waits for it.
- **Panel.** "Render nodes" in the Queue card: add by address and token, state and free memory, drain, remove.

Tested: `tests/test_nodes.py` starts two real servers whose render scripts are stand-ins. Four clips split two and
two and finish in under 80% of the serial time; a first frame travels; a job the node fails is rendered here; cancel
reaches the node; a restart of the head picks a node's job up again; a node killed mid-job loses it to the head;
drain and remove; the head as a hand-out-only machine. The panel passes 6 of 6 checks in headless Chromium.

Not built: a "Sparks" choice in the wizard, spreading one long video over nodes, a check of a node's LoRAs before a
job is sent (today the node refuses and the job moves on), per-node time estimates, anything in the engine.

## Setting up a node

`bash spark/setup-worker.sh <node address>` on the Spark that has the studio. The node gets a copy of the install
(about 20 GB) and the pack, reads the models from the head over NFS at the same paths (no weights copied), and runs
the studio with no page open. It needs passwordless `sudo` on both machines: an export in `/etc/exports` on the head,
a mount and `ffmpeg` on the node. `stop` and `remove` undo the running parts. **Not run**: the session that wrote it
was not permitted to change `/etc/exports`.

Copying the weights instead does not fit every node: the fast route alone reads 96 GB (transformer 44 GB, its int8
copy 21 GB, text encoder 27 GB, decoders 3.4 GB).

## Order of work

1. **Second node, farm proven on real Sparks** (an hour once the setup script may run): two clips in the time of one,
   then three, four, five.
2. **Span parallel for long videos and projects**, on the farm and the last-frame input. This is the first thing that
   makes one piece of work faster.
3. **Pipeline split**: text encoder and decoders on a second Spark. Small, safe, and it removes the part sequence
   parallel cannot.
4. **Sequence parallel in the engine**, aimed at 720p, long clips and 20-pass routes, after a real profile of a pass
   and a micro-bench of 373 MB gathers at 2 to 5 ranks.

## What needs a person

- Permission to run `spark/setup-worker.sh` (it edits `/etc/exports` on the head and mounts on the node).
- Which Sparks the Studio keeps. The GLM-5.3 serve needs all four of the others; each one the Studio holds is one the
  serve cannot use.
- Whether the head renders or only hands out. It has `vm.panic_on_oom=1`, so as a rank it needs its memory guard.
