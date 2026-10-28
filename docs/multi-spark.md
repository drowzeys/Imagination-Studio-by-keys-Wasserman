# Workers: local GPUs and remote DGX Sparks

A job in the Studio can use more than one DGX Spark. You choose a number from 1 to 5, **Sparks**: how many machines
the job may use, the one the app runs on counted as one. The app shows how many are there and able, uses that many,
and says what the number will do before you queue anything.

The app on a Mac leads worker Sparks too: see *A Mac at the head* below.

## Local GPUs on one workstation

The same **Sparks** room also manages one head GPU plus up to three local NVIDIA workers. Open **Machine → Sparks**
to discover cards, start/stop owned local workers and select required/preferred VIDEO placement. Local workers use
private loopback HTTP, not SSH, with isolated homes/ports and shared core runtime/model paths.
[Local setup, memory rules and physical smoke evidence](rtx-pro-6000.md#one-to-four-local-gpus).

The local limit of four includes the head. The remote-only limit remains five participants including the head;
up to three local workers can raise the combined chooser to eight. A running local worker still counts when **use**
is unticked. A combined fleet is labelled **Workers**; the Spark-only measurements below do not establish local/mixed
throughput. One clip is never split across workers.

## Required and preferred placement

**Default VIDEO placement** is saved for new single video jobs only. It does not redirect pictures, audio,
training, long videos or studio chains. **Require** waits for its target when busy/stopped and never reroutes on a
failure. **Prefer** uses that target when free and capable, otherwise allows the normal eligible workers/head.
The selected worker's own memory guard answers preflight; a full head GPU does not veto a fitting required worker.

The API accepts top-level placement on job, estimate and plan requests. Omit it to inherit the saved video default.
Use `kind` on an estimate for a non-video job. Clear the saved default with `POST /api/sparks` and `{"placement":null}`.
For a single job on the head:

```json
{"kind":"video","placement":{"worker":"head","policy":"require"},"params":{"prompt":"A paper boat","preset":"small","quality":"fh4","frames":22}}
```

For another worker, use its opaque `address` from `GET /api/sparks`; do not identify a GPU by its display name or
current index. Local addresses remain tied to their GPU UUID. Non-video placement on another worker is refused.
Initial required-worker unavailability queues a wait; loss/refusal after dispatch fails explicitly, never on another GPU.

## What you need

- The Studio installed on this Spark as usual, and on each other Spark once (`bash oneshot-setup-spark.sh`, or a copy
  of the install and of the FastH3 models). A worker needs the FastH3 models to take clips; the image model, Ref2VA,
  LoRAs and ffmpeg are optional there, and the app sends a worker only what it can render.
- A key login from this Spark to each other Spark as the user who runs the app (`ssh 10.0.10.5` must get in
  without a password). Nothing is installed as root, no system file is edited and no folder is shared.

## Set it up: the Sparks tab

1. Open **Machine → Sparks**.
2. Under *Add a Spark*, type the other machine's address as you would after `ssh` (`10.0.10.5`, or
   `user@spark-5`) and press **Add**. If its Studio is not in the same folder as on this Spark, give the folder.
3. The app looks at it and shows one of: *idle*, *rendering*, *its Studio app is not running*, *its Studio code
   differs from this Spark's*, *no Studio installed there*, *does not answer over ssh*.
4. Press **Update and start**. This sends this Spark's Studio code to the worker (rsync over ssh, as you: code only,
   never models), starts the worker's app on the worker's own `127.0.0.1:7870`, and checks it again. A worker renders
   only when its code is this Spark's, which is also how it gets the memory guard. With automatic updates on
   (Settings → Updates), this Spark does that itself when a worker falls behind its code: once per version, for an idle
   worker this Studio started; one that then fails waits for you.
5. Each Spark's row then says: its Studio's version, memory available, disk free, and what it renders (FastH3 clips
   from text, a first frame or a last frame; Ref2VA; the image model; how many LoRAs; ffmpeg).

**Check** looks again and changes nothing. **Stop** stops the worker's app and its ComfyUI server, which gives its
memory back. **Remove** takes it off the list. Untick **use** to keep a Spark in the list and give it nothing.

A worker's app is never opened to the network. This app reaches it through an ssh tunnel that it opens itself and
closes when you remove the worker or stop the app. The list is kept in `sparks/sparks.json` in the studio folder
(`~/ImaginationStudio`), not in the code.

## Choose the number

The chooser, **Sparks 1 2 3 4 5**, is in four places and is one setting:

- at the top of the **Queue**, on every tab;
- in the last step of **Create**, above *Queue this clip*;
- on the **Production** board, above *Generate every segment not yet rendered*;
- in a **Slate** shot, under *Render this shot*.

Under it the app says what will happen, for example *3 of 5 available. One clip per Spark, 3 at a time: 8 clips on
3 Sparks.* with a time estimate, and which Sparks are left out and why (*spark-node3 (the LoRA camera-motion is not
installed there)*). Choosing more than there are is allowed: the app uses what is there. Numbers above the ones
available are shown dimmed.

Through the API the number is `sparks` beside `kind` and `params`:

```bash
K="X-Studio-Key: $(bash scripts/studio-key.sh --key)"   # the app's access key (app/access.py)
curl -s -X POST http://127.0.0.1:7870/api/jobs -H "$K" -H 'Content-Type: application/json' \
  -d '{"kind": "video", "sparks": 5, "params": {"prompt": "…", "preset": "small", "quality": "fh8", "frames": 56}}'
curl -s -X POST http://127.0.0.1:7870/api/sparks/plan -H "$K" -H 'Content-Type: application/json' \
  -d '{"sparks": 5, "kind": "video", "clips": 8, "params": {"preset": "small", "quality": "fh8", "frames": 56}}'
```

A job queued without `sparks` takes the number set on the Sparks tab. `GET /api/sparks` is the list and its state.

## What the number does

| The job | With Sparks = N |
|---|---|
| Several clips: jobs in the queue, the segments of a Production board, the shots of a Slate scene | One clip per Spark, side by side, on this Spark and the first N - 1 workers that are up and can render the clip. |
| One clip | It renders on one Spark, the first that is free. One clip is not split across Sparks in the app (see the end of this page). |
| A segment or shot that opens on the last frame of the one before | It waits for that one, then renders on any free Spark. A board that is one chain renders one clip at a time. |
| A long video (over 15 s) | One Spark, this one: it is a chain, each piece opens on the last frame of the one before. |
| A clip with a LoRA, or from references (Ref2VA) | Only on Sparks that have that LoRA, or Ref2VA, installed, and only when that Spark's memory guard accepts the clip. With none: this Spark. |
| Pictures, edits, Library actions (Turnaround, Upscale, Remove a person), a timeline export, a clip that starts from a new picture | This Spark. |

A clip rendered on a worker comes back into this Spark's studio folder at the path it would have had here (the
Library, a project's segment, a Slate take), and the QA gate, the shot check and the silenced sound track run here,
on the fetched clip, where their tools are. The job's card says *Rendered on spark-node5*, the Library shows the
Spark's name and the seed under the clip, and `sparks/takes.jsonl` in the studio folder keeps one line per clip:
path, seed, settings, the Spark, and the time it took.

## Names and each machine's own status

The Sparks tab and the Machine tab show a card for every machine, this one and each worker: what it is doing, its GPU
use, temperature, memory used and free (unified memory on a Spark or a Mac, a card's own memory beside it where there
is one), disk free, load, and how many LoRAs and which models it can render with. The figures are the ones each Studio
app reports about itself on `/api/spark/self` (`status`, from `telemetry.compact`); the head already looks at every
connected worker about every 20 seconds, so the panel only reads what `/api/sparks` holds and asks no worker anything
more. A worker on code from before this field shows what the older answer carried (memory and disk) and says it needs
“Update and start” for the rest.

Machines are called by their host names. To call them something else, write `sparks/labels.json` in the studio folder:

```json
{"head": "fa5a (head)", "spark-d197": "d197", "user@192.0.2.10": "Mac Studio"}
```

A key is `head` (or this machine's host name) for this Spark, and a worker's address or its listed name for a worker. The
file is only read, by people and scripts, so a save of the list or an update of the Studio never changes it. A label is
display only: jobs, takes and the log keep each machine's own name, and the page maps them
(`names` on `/api/sparks`).

## What keeps it safe

- **One job on a Spark at a time.** A worker that is rendering anything, for this Spark or for someone else, is
  given nothing until it is idle.
- **Every clip passes the worker's own memory guard** (*The memory guard* in the README): this app asks it before
  anything is sent, the worker's app asks it again when it queues the job, and the guard stands beside the worker's
  ComfyUI server while it renders. Automatic/preferred jobs may move after refusal; a required target never silently
  falls back to another machine.
- **A worker that stops answering is bounded.** After 60 seconds of silence while rendering it is marked *stopped
  answering*, its tunnel is closed, and automatic/preferred work can return to the queue. A required job fails instead.
  A down worker is given nothing more until **Check**. Automatic movement is bounded to two attempts, then the head;
  that fallback is never applied to required placement.
- **Cancel** cancels the clip on the worker too.
- **Stopping the app** closes its tunnels. Remote worker apps keep running until **Stop** on each; their models can
  stay warm. Owned local workers are drained and stopped with their head, not left unmanaged.

## Measured

Eight plain FastH3 clips (56 frames, 864x480, 8 passes, TensorFold's int8 engine, seeds 101 to 108), queued through
the app's own API with `sparks` 1 to 5 on 2026-10-13: this Spark and four workers on the 200G fabric, models loaded
on all five beforehand, one run each. Wall time is from the first job queued to the last clip finished and checked.

| Sparks | Wall time, 8 clips | Against one Spark | The most there is to have (8 clips in rounds) | Clips on each Spark |
|---|---|---|---|---|
| 1 | 263.2 s | 1.00x | 1.00x | 8 |
| 2 | 129.8 s | 2.03x | 2.00x | 4 + 4 |
| 3 | 97.5 s | 2.70x | 2.67x | 3 + 3 + 2 |
| 4 | 69.0 s | 3.81x | 4.00x | 2 + 2 + 2 + 2 |
| 5 | 65.0 s | 4.05x | 4.00x | 2 + 2 + 2 + 1 + 1 |

A clip took 32 to 35 s on every Spark, the hand-over to a worker and the fetch included (0 to 2 s more than a clip
made here). **Five Sparks were not much better than four here** (65 s against 69 s): eight clips go in two rounds either
way. The fifth Spark pays when the count of clips makes a round less, ten clips or more. All 40 clips landed in this
Spark's Library with the right seed, settings and Spark in the takes list; available memory, read every 5 s, stayed above 52 GB on
this Spark and 61 GB on the workers.

Earlier the same day, without this setting (a hand-written driver over the same job API), a production's 22 clips
on five Sparks came to 4.09 times one Spark.

The time the app shows at a number is worked out from this table: the clips' rounds at that number, less what the
measured run lost at four. It is an estimate. Six to eight Sparks are not measured: five machines exist here.

## A Mac at the head

The Studio on a Mac uses the same Sparks tab, chooser and queue. The Mac is the machine the app runs on, counted as
one; the workers are DGX Sparks running their own Studio, as above.

**What goes to a Spark from a Mac.** Single FastH3 clips that a Spark renders with its own settings: the Small,
Native, 720p, Sharp and custom sizes, 4, 8 or 20 passes (or passes set by hand), from text, a first frame or a first and
last frame, with a LoRA the Spark has, and reference clips where the Spark has Ref2VA. These stay on the Mac, and the
page says why beside each Spark:

- MiniMax H3 Turbo's qualities (Standard, High, Full, Fast): a Spark renders FastH3 only;
- the presets made with the 2x decoder (Draft, 2K, 2K 2560x1440, 960p): a Spark has no 2x decoder. A job that names
  no preset is Draft on a Mac;
- a text encoder file of the Mac's: it is an MLX file; a Spark reads only the files in its own ComfyUI folder.

The same seed does not give the same frames on a Mac and on a Spark: they run different engines.

**Reaching the Sparks.** When the Mac reaches each Spark with ssh itself, add a Spark as on a Spark head. When it
reaches only the Spark that leads them (here the Mac reaches `192.168.1.10` but not the `10.0.10.x` fabric), give
that Spark under **Through** (`spark@192.168.1.10`). The Mac then logs in there with its own key, and that Spark
logs in to the worker with its own key: no key is added to any worker. The tunnel is one ssh from the Mac to that
Spark, forwarding a local port to a socket in `~/.cache/studio-sparks` there (a folder only its user can open); on that
Spark a second ssh forwards the socket to the worker's own `127.0.0.1:7870`. Nothing listens on a network on any of the
three machines. The second ssh lives only as long as the first: it ends when the Mac's ssh closes, is killed or loses
its network (the Mac sends a heartbeat every 10 s and the relay ends after 30 s without one), and its socket is removed
with it. *Check*, *Update and start* and *Stop* go the same way (`ssh` to that Spark, then `ssh` from there).
**Update and start** sends the Mac's code with macOS's own rsync (openrsync) through that Spark; a Mac and a Spark on
the same commit have the same `CODE` files, so a worker that is level with one is level with the other.

**Once, by hand, on the Mac:**

1. A key login from the Mac to the Spark it goes through: `ssh spark@192.168.1.10 true` must answer without a
   password. Add the Mac's public key (`~/.ssh/id_ed25519.pub`) to that Spark's `~/.ssh/authorized_keys` if it is not
   there.
2. That Spark's host key in the Mac's `~/.ssh/known_hosts`: the app uses ssh with `BatchMode`, which never asks. Run the
   `ssh` of step 1 once in a terminal and answer yes after comparing the fingerprint with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on that Spark.
3. Nothing for the workers: the Spark that leads them already has a key login to each (it is how it leads them).

Instead of **Through**, a person who prefers the Mac to log in to every worker itself can authorise the Mac's key on
each worker (`ssh-copy-id -o ProxyJump=spark@192.168.1.10 spark@10.0.10.5`, after step 2) and put
`ProxyJump spark@192.168.1.10` for `10.0.10.*` in `~/.ssh/config`: then add the workers with no Through, as if
the Mac reached them.

**Measured.** On 2026-10-19, a test app of this branch on a Mac Studio M5 Ultra (port 7871, its own folder), worker
spark-node5 (`10.0.10.5`) through the Spark at `192.168.1.10`, one run:

| Step | Time |
|---|---|
| Add, then *Check* (ssh through the Spark, the relay, `/api/spark/self`): "differs in 5 files" | under 15 s |
| *Update and start* (openrsync through the Spark, its app restarted, checked again): *idle* | 7 s |
| A 5 s 864x480 FastH3 clip (8 passes, seed 7), `sparks` 2, the Mac's own queue busy: handed to spark-node5 | 1.0 s after it was queued |
| Rendered there (its memory guard: about 58 GB needed, 118 GB there) | 71 s |
| Back in the Mac's folder and shot-checked on the Mac | 73.7 s after it was queued |

The clip in the Mac's Library is byte for byte the worker's (SHA-256 `288513c0…`), 124 frames with sound, with the
Spark and the seed in `sparks/takes.jsonl`. The app killed with `kill -9` left nothing: the Mac's ssh and the relay on
the Spark were gone within 5 s. The table above, 1 to 5 Sparks, was measured with a Spark at the head; from a Mac the
estimate is marked not measured and says so.

**Not the same as a Spark at the head.** The time estimate multiplies the Mac's own time for one clip by the Spark
table above: it was not measured with a Mac. A Mac has no memory guard of its own (its row shows none); every clip on a
Spark still passes that Spark's. The Mac renders the clips no Spark can take, at the Mac's speed.

## What is not built

- **One clip split across Sparks.** It is measured, outside the app, on the engine's `h3-cuda-multispark` branch
  with ranks on the other Sparks: a 5 s 864x480 FastH3 clip took 72.2 s on one Spark and 51.4, 41.8 and 40.9 s on
  two, three and four (1.40x, 1.73x, 1.77x), with the same bits in every frame; a 10 s 1280x736 clip's pass was 1.78x
  faster on two and 2.81x on four. Only plain FastH3 clips on the int8 engine, no LoRA and no Ref2VA. What is
  missing before it can be in the app: the installer does not build that engine branch; each rank needs the 21 GB
  int8 weights and the engine library on its Spark, started, stopped and shown by the app; a rank that dies fails
  the clip, with no fall back to one Spark; a rank has only its own 8 GB check, not the memory guard; this Spark as
  a rank, and so five on one clip, was never run. Until then one clip renders on one Spark and the page says so.
- **A long video's pieces side by side.** The pieces are a chain. Rendering them at once needs keyframes between
  the pieces, which is another way of making a long video and is not built.
- **Pictures and Library actions on a worker.** Only single clips are handed over.
- **The queue across a restart.** Jobs are kept in memory. A remote worker may finish in its own folder, but a restarted
  head does not fetch that clip. Owned local workers stop with their head.
- **Starting a worker's install.** The app sends code, not models or the environment.
- **Five remote-only participants at most.** The limit is 5 (`LIMIT` in `app/sparks.py`), including the head. Local
  workers have a separate four-card bound; the combined selector is at most eight, not eight remote Sparks.
