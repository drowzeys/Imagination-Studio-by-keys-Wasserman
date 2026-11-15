# One to four local GPUs: the recipe

One Studio on one Linux workstation can render its clip queue on up to four NVIDIA cards at once, one clip per card,
the way a head hands clips to other DGX Sparks. This page is the recipe: what to set, where to switch it on, how to
check it, and what each refusal means. It is written so that a person or their agent can follow it top to bottom.

The design, the memory rules and the measured runs are in
[RTX Pro 6000 and local GPU workers](rtx-pro-6000.md#one-to-four-local-gpus).

## What it is, and what it is not

| | Local GPU workers (this page) | Paired cards ([two-gpus.md](two-gpus.md)) |
|---|---|---|
| What is shared out | the **queue**: each card renders a whole clip of its own | **one clip**: every pass is split between two cards |
| Cards | 1 head + up to 3 workers | exactly 2 of the same model |
| Switched on by | **Machine → Sparks → Local GPUs → Start** | `STUDIO_GPUS` at launch |
| Faster for | many clips (a batch, a film's segments) | one clip |

They exclude each other: a Studio launched with `STUDIO_GPUS` runs no local workers. A clip is never split across
local workers and their VRAM is never added together.

## What you need

- **Linux.** Local workers are not available on Windows or macOS.
- **Two to four NVIDIA cards with VRAM of their own**, each able to render alone (an RTX PRO 6000 Blackwell 96 GB
  renders video; see [rtx-pro-6000.md](rtx-pro-6000.md) for smaller cards). More than four may be installed: the
  Studio manages four, the head included.
- **Host RAM for every clip that runs at once.** The cards do not share VRAM, but every render stages its model in
  the machine's RAM. For a plain FastH3 clip on a 96 GB card the Studio reserves 42,000 MiB while it loads and
  renders, and always keeps 8,192 MiB free:

  | Clips at once | Host RAM to plan for |
  |---|---|
  | 1 | 64 GB (the single-card minimum) |
  | 2 | 92 GB reserved, so a 128 GB machine (measured: two clips together on two cards) |
  | 3 | 134 GB reserved, so a 192 GB machine (from the reservation, not measured) |
  | 4 | 176 GB reserved, so a 192 GB machine or more (from the reservation, not measured) |

  With less RAM nothing breaks: the cards that fit render, and a clip that cannot fit waits up to two minutes for a
  running one, then fails with a RAM reason. References, LoRAs, other text encoders and smaller cards reserve more.
- **The Studio installed once**, for the head card ([Install](rtx-pro-6000.md#install)). Workers share its `PREFIX`
  and `SPARK_MODELS`: no second install, no copied weights, no ssh.

## The recipe

### 1. Pick the head card

```bash
nvidia-smi --query-gpu=index,uuid,name,memory.total,display_active --format=csv
```

The head is the card the Studio itself runs on. It also makes the pictures, voice, music and long jobs, so pick a
card that does **not** drive a display when there is one. Note its `index`.

### 2. Start one Studio, naming the head card

Use the same four values the install used. `STUDIO_GPU` is the head's nvidia-smi index; with two or more compute
cards it is required, and without it the page says *Local GPU workers are off*.

```bash
PREFIX=/data/studio SPARK_MODELS=/data/studio/models \
STUDIO_HOME=/data/studio/ImaginationStudio STUDIO_GPU=0 \
bash scripts/app.sh
```

Do **not** set `STUDIO_GPUS` (that is the paired mode), do not start a second Studio by hand for another card, and do
not add the machine to itself as an ssh worker.

### 3. Switch the other cards on

**In the app:** open **Machine → Sparks**. With more than one card detected the page opens with a **Local GPUs**
panel: one row per card with its VRAM, temperature and whether it drives a display. The head's row says *Head GPU*
and has no Start. Press **Start** on each other card you want (up to three). A card that drives a display asks first.

**By API** (what the buttons call; from the machine itself no key is needed):

```bash
B=http://127.0.0.1:7870
# the cards: "id" is what the calls below take, "head" marks the head, "reason" says why a card cannot start
curl -s $B/api/sparks | python3 -c '
import json, sys
local = json.load(sys.stdin)["local_gpus"]
print("off: " + local["unavailable"] if local.get("unavailable") else "managed %s of %s" % (local["managed"], local["limit"]))
for g in local["devices"]:
    print(g["id"], "GPU", g["index"], g["name"], "HEAD" if g.get("head") else "", "display" if g.get("display_active") else "", g.get("reason") or "")'

# start a worker on one card (repeat per card). Add "allow_display": true only for a card that drives a display,
# and only when its owner agreed: rendering there can make the desktop unresponsive.
curl -s -X POST $B/api/sparks/local -H 'Content-Type: application/json' \
  -d '{"gpu": "local:GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", "action": "start"}'
```

Starting a worker loads no model: its first clip is a cold one. Started workers are remembered, and quitting the head
stops them.

### 4. Say how many cards a job may use

**In the app:** the row at the top of the queue now reads **Workers** with a number from 1 to the cards you have
(**Workers…** beside it opens the Sparks page). The same number is **Machine → Sparks → Workers for new jobs**. Set
it to the number of cards. Each job carries the number it was queued with.

**By API:**

```bash
curl -s -X POST $B/api/sparks -H 'Content-Type: application/json' -d '{"sparks": 4}'      # the default for new jobs
# or per job: "sparks" beside "kind" and "params" in POST /api/jobs
```

### 5. Optional: send clips to one card

**Machine → Sparks → Where new clips render.** **Render on** is *Automatic* (the queue shares clips out by the
Workers number), the head, or one card. **When busy or offline** is *Wait for it* (the clip never renders elsewhere)
or *Use another renderer*.

```bash
# every new clip on one card, waiting for it; "prefer" lets it go elsewhere; null is Automatic again
curl -s -X POST $B/api/sparks -H 'Content-Type: application/json' \
  -d '{"placement": {"worker": "local:GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", "policy": "require"}}'
# or per job: the same "placement" object beside "kind" and "params" in POST /api/jobs ("worker": "head" is the head)
```

### 6. Check it

1. `GET /api/sparks`: every started card has a `worker` whose `state` is `ready`, and `local_gpus.managed` is the
   number of cards in use.
2. Ask what the number will do before queueing: `POST /api/sparks/plan` with
   `{"sparks": 4, "kind": "video", "clips": 4, "params": {...}}` says how many workers can take the clip and names
   any that cannot, with the reason.
3. Queue as many short clips as cards (5 s, Small). While they render, each card's row on the Sparks page says
   *rendering*, and `nvidia-smi` shows memory in use on each card. Each finished job records the card that made it
   (`spark` in the job; the Library's take says the same).
4. If fewer clips run at once than cards, read the waiting job's line: a RAM reason means host RAM, not the cards
   (see the table above).

## What goes where

- **To the workers:** single video clips, text to video or from a picture, at the sizes and qualities the card lists.
- **On the head only:** pictures, voice, music, sound, training, exports, long videos (each part waits for the one
  before) and compound jobs.
- **LoRAs** are per worker, not inherited from the head: install them into the worker's own studio folder,
  `<head STUDIO_HOME>/sparks/local/<GPU UUID>`, with `STUDIO_HOME=<that folder> python3 app/loras.py install GROUP`,
  then press **Check**. A clip that needs an adapter a card lacks is not sent there.

## When it refuses

| It says | What to do |
|---|---|
| *Local GPU workers are off.* … *set STUDIO_GPU* | Restart the Studio with `STUDIO_GPU=<head index>`. |
| *This Studio renders on paired cards (STUDIO_GPUS…)* | Unset `STUDIO_GPUS` and restart: paired mode and local workers exclude each other. |
| *This GPU drives a display; explicitly allow…* | Pick another card, or confirm in the app (`"allow_display": true`) with the owner's agreement. |
| *An external compute workload is using this GPU; it is left running* | Something else (another model server, a training run) holds that card. The Studio never stops it: stop it yourself, then **Start**. |
| *This GPU is owned by another Studio* | A second Studio on this machine already runs there. One Studio manages the cards: stop the other one. |
| *At most four local GPUs can be managed, including the head* | **Stop** and **Remove** a worker before starting another card. An unticked **use** still holds its slot while it runs. |
| *This device has no dedicated VRAM* | That device has no memory of its own (an integrated GPU): it cannot be a worker. |
| *The head GPU's index changed; restart this Studio…* | Cards were added, removed or reordered. Restart the head with the new `STUDIO_GPU`; workers follow their UUID. |
| *The shared PREFIX/venv Python is missing* | Start the Studio with the install's `PREFIX`. |
| A clip fails with a RAM reason while cards are idle | Host RAM is the limit: lower the Workers number, or **Stop** an idle worker whose warm model holds RAM. |

## For agents

- Configure through the app's routes above, never by starting a second Studio, a ComfyUI of your own or a
  `CUDA_VISIBLE_DEVICES` launcher beside it: the Studio's GPU leases and its shared-RAM ledger only cover what it
  started. The rules in [STUDIO-AGENT.md](../STUDIO-AGENT.md) apply on every card.
- Never stop another process to free a card, never pass `allow_display` without the owner's word, and never raise or
  switch off a memory floor to fit one more clip at once.
- Report what is: which cards started, the Workers number, and any card that refused with its sentence.

## What has been measured

Two RTX PRO 6000 Blackwell Max-Q 96 GB cards in a 128 GB-class workstation: two cold 5 s 864x480 clips rendered
together, one per card ([the runs](rtx-pro-6000.md#one-to-four-local-gpus)). Three and four cards, missing and
reordered cards and mixed capacities are covered by tests; **three and four physical cards have not been run**, and
there is no throughput figure yet. If you run four, an issue with your `GET /api/sparks` and the job times is welcome.
