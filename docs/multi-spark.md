# More than one DGX Spark: the Sparks setting

A job in the Studio can use more than one DGX Spark. You choose a number from 1 to 5, **Sparks**: how many machines
the job may use, the one the app runs on counted as one. The app shows how many are there and able, uses that many,
and says what the number will do before you queue anything.

The app on a Mac leads worker Sparks too: see *A Mac at the head* below.

## What you need

- The Studio installed on this Spark as usual, and on each other Spark once (`bash oneshot-setup-spark.sh`, or a copy
  of the install and of the FastH3 models). A worker needs the FastH3 models to take clips; the image model, Ref2VA,
  LoRAs and ffmpeg are optional there, and the app sends a worker only what it can render.
- A key login from this Spark to each other Spark as the user who runs the app (`ssh 10.100.10.5` must get in
  without a password). Nothing is installed as root, no system file is edited and no folder is shared.

## Set it up: the Sparks tab

1. Open **Sparks** (the tab beside Library).
2. Under *Add a Spark*, type the other machine's address as you would after `ssh` (`10.100.10.5`, or
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
3 Sparks.* with a time estimate, and which Sparks are left out and why (*spark-0060 (the LoRA camera-motion is not
installed there)*). Choosing more than there are is allowed: the app uses what is there. Numbers above the ones
available are shown dimmed.

Through the API the number is `sparks` beside `kind` and `params`:

```bash
curl -s -X POST http://127.0.0.1:7870/api/jobs -H 'Content-Type: application/json' \
  -d '{"kind": "video", "sparks": 5, "params": {"prompt": "…", "preset": "small", "quality": "fh8", "frames": 56}}'
curl -s -X POST http://127.0.0.1:7870/api/sparks/plan -H 'Content-Type: application/json' \
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
on the fetched clip, where their tools are. The job's card says *Rendered on gx10-5482*, the Library shows the
Spark's name and the seed under the clip, and `sparks/takes.jsonl` in the studio folder keeps one line per clip:
path, seed, settings, the Spark, and the time it took.

## What keeps it safe

- **One job on a Spark at a time.** A worker that is rendering anything, for this Spark or for someone else, is
  given nothing until it is idle.
- **Every clip passes the worker's own memory guard** (*The memory guard* in the README): this app asks it before
  anything is sent, the worker's app asks it again when it queues the job, and the guard stands beside the worker's
  ComfyUI server while it renders. A clip a worker refuses is rendered on another machine and the worker stays in
  use for other clips.
- **A worker that stops answering is not waited on.** After 60 seconds of silence while it renders it is marked
  *stopped answering*, its tunnel is closed, and its clip goes back to the queue for another machine. It is given
  nothing more until you press **Check**. A clip is taken back from a worker at most twice; after that it renders
  on this Spark.
- **Cancel** cancels the clip on the worker too.
- **Stopping the app** closes its tunnels. The workers' apps keep running, idle, until you press **Stop** on each;
  their models stay in memory until then.

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
reaches only the Spark that leads them (here the Mac reaches `192.168.4.174` but not the `10.100.10.x` fabric), give
that Spark under **Through** (`keyspark@192.168.4.174`). The Mac then logs in there with its own key, and that Spark
logs in to the worker with its own key: no key is added to any worker. The tunnel is one ssh from the Mac to that
Spark, forwarding a local port to a socket in `~/.cache/studio-sparks` there (a folder only its user can open); on that
Spark a second ssh forwards the socket to the worker's own `127.0.0.1:7870`. Nothing listens on a network on any of the
three machines. The second ssh lives only as long as the first: it ends when the Mac's ssh closes, is killed or loses
its network (the Mac sends a heartbeat every 10 s and the relay ends after 30 s without one), and its socket is removed
with it. *Check*, *Update and start* and *Stop* go the same way (`ssh` to that Spark, then `ssh` from there).
**Update and start** sends the Mac's code with macOS's own rsync (openrsync) through that Spark; a Mac and a Spark on
the same commit have the same `CODE` files, so a worker that is level with one is level with the other.

**Once, by hand, on the Mac:**

1. A key login from the Mac to the Spark it goes through: `ssh keyspark@192.168.4.174 true` must answer without a
   password. Add the Mac's public key (`~/.ssh/id_ed25519.pub`) to that Spark's `~/.ssh/authorized_keys` if it is not
   there.
2. That Spark's host key in the Mac's `~/.ssh/known_hosts`: the app uses ssh with `BatchMode`, which never asks. Run the
   `ssh` of step 1 once in a terminal and answer yes after comparing the fingerprint with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on that Spark.
3. Nothing for the workers: the Spark that leads them already has a key login to each (it is how it leads them).

Instead of **Through**, a person who prefers the Mac to log in to every worker itself can authorise the Mac's key on
each worker (`ssh-copy-id -o ProxyJump=keyspark@192.168.4.174 keyspark@10.100.10.5`, after step 2) and put
`ProxyJump keyspark@192.168.4.174` for `10.100.10.*` in `~/.ssh/config`: then add the workers with no Through, as if
the Mac reached them.

**Measured.** On 2026-10-19, a test app of this branch on a Mac Studio M5 Ultra (port 7871, its own folder), worker
gx10-5482 (`10.100.10.5`) through the Spark at `192.168.4.174`, one run:

| Step | Time |
|---|---|
| Add, then *Check* (ssh through the Spark, the relay, `/api/spark/self`): "differs in 5 files" | under 15 s |
| *Update and start* (openrsync through the Spark, its app restarted, checked again): *idle* | 7 s |
| A 5 s 864x480 FastH3 clip (8 passes, seed 7), `sparks` 2, the Mac's own queue busy: handed to gx10-5482 | 1.0 s after it was queued |
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
- **The queue across a restart.** Jobs are kept in memory: if this app stops while a worker renders, the worker
  finishes its clip in its own folder and this app, started again, does not fetch it.
- **Starting a worker's install.** The app sends code, not models or the environment.
- **Five Sparks at most.** The limit is 5 (`LIMIT` in `app/sparks.py`), the most that have been measured.
