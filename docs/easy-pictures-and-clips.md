# Super easy pictures and clips

**Mission:** make it super easy to create and change pictures and videos. Fewer steps, and fewer settings the user
has to set.

**Scope:**
- making a picture, and editing a picture
- clips: from text, from a picture, from scouts, from references, and long videos
- changing what was made: another take, variations, extend, restyle, fix, upscale, editing a frame

Installers, settings pages and the other rooms are out of scope.

**How this was made:** from reading the code at 4.3.1. Nothing was rendered. The draft was then reviewed by two
outside reviewers, each given the same brief, the draft, AGENTS.md and the code. Where they corrected the draft, the code was checked again; §4 lists what changed. Paths are repo-relative.

**The idea everyone agreed on:** keep the user beside their result, and carry its inputs forward. Every picture and
clip should have the next actions under it: another take, animate, edit, extend, higher quality. Each one is sent
with the inputs that made it, so nothing is typed or chosen twice.

**Rules that stay** (AGENTS.md):
- frames are 17n + 5
- canvases are multiples of 32, at most 1344x768 pixels
- the video model has no negative prompt
- the user's own sentences are never reworded; they may only be placed into fields
- measured tables change only with new measurements
- references never go with a first frame or to FastH3
- a long video takes no references and no last frame
- FLUX3-K always runs its upsampler
- the 2x decoder is an upscale, not added detail
- a failed shot check never fails a job, and its retake is the user's choice

---

## Status: what is built (branch `feat/easy-create`, 2026-11-06)

The owner's direction during the work: *"this suite has much to offer, but too complicated to use."* That added the
simple studio (below) to the plan. Each batch was checked as follows:
- the page and server tests passed
- a browser drove the new flows on a private instance with no renders
- `scripts/studio-check-ui.cjs` clicked every button of all 34 tabs (no page errors, no broken controls)
- two outside reviewers read the diff, and their findings were fixed

| Plan item | Built | Where |
|---|---|---|
| **The simple studio** (new) | On for a new studio: Home, Video, Image, Library (+ Machine, Settings); every room one ⌘K away; one switch under the rooms, in a phone's More sheet and in Settings › Studio layout; the owner's own layout returns when it is off; a studio with older settings keeps every room and is invited. Home shows the box (clip or picture), what was just made and recent takes. Video and Image open on Create. | `app/settings.py` (`layout.simple`), `settings.js` (`roomsShown`, `simpleSet`), `shell.js`, `home.js`, `studio.css` |
| 1. Another take | Create's Another take queues at once with the next seed; Same seed every take; Settings' random seed draws per take at queue time; fixed keeps "Render again repeats a take". Home's Retake re-sends the request that made the take with the next seed. More like these on pictures. | `shell.js` (`createQueue`, `createAgain`), `home.js` (`hcReseed`), `index.html` |
| 2. Actions under results | Pictures: Animate, Edit (Quick Edit), More like these. Clips: Edit this frame, Higher quality, Restyle, Extend, Fix, Upscale (Create; Home and the Library in part). | `home.js`, `index.html`, `quick.js` |
| 3. Scouts | Animate this (queues at once), Use as end, the picture prompt follows the scene until edited, Make scouts follows typing. "Pictures to choose from". | `index.html` |
| 4. Quick Edit is the edit | Edit on any picture opens Quick Edit; More tools carries picture, words and model to the full Edit (reset to a plain edit at the source size); the full Edit folds size, seed and steps and moves the seed on per edit. | `quick.js`, `edit.js`, `index.html` |
| 5. Change the first frame in place | "Change something in it" under Create's first frame, Use this version, Undo. | `index.html` (`frameChange`) |
| 6. Edit this frame | The player's frame to Quick Edit; the clip is not changed. | `quick.js` (`qkFrame`) |
| 7. Shape | **16:9 part only.** The page compares the picture with the canvas the clip starts from and offers *Crop it to fit* (POST `/api/fit`, a centre crop, nothing scaled), with Undo. Portrait and square **clips** are not built: they need a measured render on each machine. | `server.py` (`fit_picture`, presets' `start`), `index.html` (`shapeNote`) |
| 8. Recipes | Draft / Final (/ Sharp Spec), each with the size it delivers; ids unchanged, no migration. Retake, seed and Sparks folded into *Render options*. | `shell.js`, `home.js`, `settings.js`, `index.html` |
| 9. Higher quality vs Upscale | Higher quality re-sends the take's own request at the Final recipe (said: a new render); not for references, long takes, custom sizes or a take already Final. Upscale stays the Library action. | `shell.js` (`betterAsk`) |
| 10. Reference chips | Insert puts `<Picture N>`, `<Video N>`, `<Audio N>` at the cursor in the scene, action or camera only. | `index.html` (`refInsert`) |
| Hints | "Use 20 passes" when the action holds three or more actions; the shootout's picture model for faces/posters and crowds/illustration/hands. One click each, never automatic. | `index.html` |

**Not done, and why:**
- **Portrait and square clips.** These need one measured render on a Mac and a Spark (AGENTS.md: no unmeasured
  canvas without saying so).
- **Higher quality from the Library.** The Library has no copy of the request that made a take; the job's own
  params are changed while it renders, so resending them is unsafe.
- **Spoken lines from Home's box.** Moving a quoted sentence into a line would change the user's own words.
- **Retake on by default for finals.** This is the owner's decision (AGENTS.md).
- **The running app on port 7870 serves this branch's page but still runs the old server code.** Until it is
  restarted, *Try it* (simple studio) and *Crop it to fit* answer with an error. `deploy.sh` restarts it once the queue
  is empty.

## 1. Where it is hard today

| Workflow | What the user has to do | Where |
|---|---|---|
| **Another take** | In Create, the "another take" button sends the **same seed** again. "Random" draws one seed when the page loads, not one per take. Home's Retake changes the seed but re-reads the *current* recipe, length and model, and has lost the picture. | `index.html:764`, `shell.js:420`, `settings.js:61-65`, `home.js:162` |
| **Scouts → clip** | Choose the recipe *before* scouting, because scouts are made at its size. Type the picture prompt. Make scouts (a button that may stay greyed while typing). Pick. Scroll. Render. The picture prompt stops following the scene once filled. | `index.html:713-725`, `server.py:95` |
| **Picture → clip** | A square or 9:16 picture is **stretched** onto a 16:9 clip. The page only says "match its aspect ratio". Home's picture tiles have no Animate. | `scripts/video.sh:22`, `h3_generate.py:1087`, `index.html:631, :725`, `home.js` |
| **Picture, better** | No "same picture, full quality". Rebuild the prompt with another model by hand. | `index.html:625-647` |
| **Edit** | Two paths: Quick Edit, and the full Edit with up to 8 decisions. Editing a first frame or a frame of a clip means leaving Create or the clip. | `quick.js`, `edit.js:89-126` |
| **Clip, better** | No "this take, higher quality". Rebuild it in Create with another recipe. Extend, Restyle, Fix and Upscale exist, but only under the Library's *More actions*. | `actions.js:18-27`, `index.html:953` |
| **Settings** | Engine names, "Turbo 5 + base sound", passes, the text encoder file, "17n + 5". "2K" means two sizes, and "Final 2K" is 720p on a Spark. | `index.html:300-310`, `server.py:74-75`, `settings.js:57-60` |
| **References** | `<Picture N>` is already linked to each named subject by the composer (`compose_references`). To place a subject in a sentence the user still types the tag. | `server.py` compose |
| **Home with speech** | Home sends no spoken lines. A typed quote is not a line, and the composer may add the quiet-speech wording. | `home.js:147`, `server.py` `QUIET_PICTURE` |

---

## 2. The plan

They are ranked by steps saved times how often users hit them, against effort. Rankings are judgement, not usage
data.

### 1. Another take that really is another take. Effort: S
- Draw the seed **when the take is queued**, not when the page loads.
- Clone the request that made the take, and change only its seed. Home's Retake then keeps the recipe, length, model
  and picture it was made with.
- **Same seed** stays as an explicit choice, and the take shows its seed.
- The Sharp Spec keeps its prescribed first seed.
- For pictures, add **More like these**: the same `scout` job with the next seeds.

Files: `app/static/index.html` (`createQueue`, step 4), `shell.js`, `home.js` (`hcGenerate`/`hcRequest`: keep the
request it sent), `settings.js`, `app/settings.py`.

### 2. The next actions under every result. Effort: S–M
Under a finished **picture** (Image › Create, Home's tiles, the Library):
- **Animate**: Create with this as the first frame
- **Edit**: Quick Edit with the picture loaded
- **More like these**
- **Full quality**: the same prompt and seed with the installed full model: Qwen 2.1 Full or FLUX3-K Full. A `scout`
  job already takes `image_model`, so the server needs nothing.

Under a finished **clip** (Create, Home, the Library): **Another take**, **Extend**, **Restyle**, **Fix**, **Edit a
frame**, **Higher quality**, **Upscale**. These are the existing actions, moved out of *More actions*. Each one is
greyed with its reason when it does not apply. For example: a long video cannot be re-rendered at higher quality as
one take.

Files: `index.html`, `home.js`, `actions.js`, `revise.js`, `quick.js`.

### 3. Scouts that do not trap you. Effort: S
- **Animate this** on each scout picks it and queues the clip through `createQueue`. It shows the recipe and length
  beside it, and guards against a double click. Clicking the picture still only selects it, for comparing.
- Add **Use as end** beside it (`lastFramePanel`), so a first and last frame take two clicks.
- **Make scouts** follows typing (repaint on input, as `createReadyPaint` does for Render).
- The picture prompt follows the scene until it is edited by hand: the same `promptEdited` pattern as the video
  prompt.
- Make scouts independently of the final recipe. Each platform has its own scout size (1344x768 on a Spark, where
  `qhd` is not offered). The pick is fitted to the final canvas by the shape rule of item 7, never stretched. The
  shapes differ by up to 3.5 % (864x480 against 1280x736).

Files: `index.html`, `shell.js`, `server.py` (`SCOUT_SIZES`).

### 4. Quick Edit is the edit. Effort: S
- **Edit** on any picture opens Quick Edit with the picture loaded: say it, go, with a new seed each turn and the
  source size kept.
- **More tools** carries the picture, the instruction and the model into the full Edit, for several pictures, angles
  and LoRAs.
- In the full Edit, the steps (8/40) and the size fold away. 8 steps and the source size are the defaults already.

Files: `index.html`, `quick.js`, `edit.js`, `shell.js`.

### 5. Edit the first frame where it is. Effort: M
On the selected first frame in Create, add a one-line **Change something** box. It runs the existing `edit` job and
shows the result with **Use this version**. The original is kept, with Undo. There is no re-upload and no change of
room.

Files: `index.html`, `quick.js`, `edit.js`. The job is `app/picture_edit.py`, unchanged.

### 6. Edit the frame that is playing. Effort: S–M
1. **Edit this frame** under a clip sends the player's `currentTime` to `/api/frame` and opens Quick Edit on it.
2. The edited still offers **Continue from here** (Create, with it as the first frame) and **Animate**.

The page says plainly that this edits a still, not the video.

Files: `index.html`, `home.js`, `quick.js`, `edit.js`. The frame endpoint is `server.py`, unchanged.

### 7. A picture keeps its shape. Effort: M
On Animate, or when a picture is uploaded as a first frame, derive a legal canvas from the picture with the existing
`revise.size_of()`. That is the native preset of that size, or a size of its own: multiples of 32, within the custom
sides and pixels. Show how the picture sits in that canvas, with crop position and *Fit (pad)* as the overrides.

- **16:9 pictures first.** Portrait and square canvases go in only after one measured render on each machine. They
  are not measured: neither the 2x decoder on a portrait canvas nor the quality.
- An unmeasured canvas says "no measurement for this shape" instead of an estimate.
- Today the second frame is centre-cropped and the first stretched (`h3_generate.py:1087-1096`). Make the first frame
  follow the same rule on every route, including the Spark's `crop_frames`.

Files: `app/revise.py`, `server.py`, `scripts/video.sh`, `h3_generate.py`, `spark/spark_generate.py`, `index.html`,
`home.js`.

### 8. Recipes you can read. Effort: S, plus a settings migration
- **Draft · Final · Sharp Spec.** Each card prints the size it delivers.
- The engine choice, the H3 qualities and the text encoder go under *More*, in plain words.
- "2K" is used for one size only.
- `presets.recipe` values already saved (`quick`, `final`) are migrated in `app/settings.py`.

There is no single "Best" promise across machines. On a Mac the 2x decoder is an upscale of the same render. On a
Spark, Upscale is a separate action and can be missing. Each is offered as what it is (item 9).

Files: `settings.js` (`recipeParams`), `shell.js` (`takeRecipes`), `home.js` (`HC_RECIPES`), `index.html`,
`app/settings.py`.

### 9. Higher quality and Upscale are two different things. Effort: M
- **Higher quality** re-renders the take: the same inputs and seed, a stronger recipe. It is labelled "a new render;
  the motion may differ", because the same seed at another size or pass count is not the same take.
- **Upscale** enlarges the finished clip and keeps the performance: the Library action, where it is installed.
- Both are greyed with the reason for long videos and reference clips.

Files: `actions.js`, `shell.js`, `index.html`, `home.js`, `server.py`, `app/actions.py`.

### 10. Reference chips, not tag syntax. Effort: S–M
Each named reference thumbnail is a chip. Clicking it inserts `<Picture N>` (or `<Video N>`, `<Audio N>`) at the
cursor. The chip never replaces words on its own.

The composer's subject definitions and speaker mapping stay as they are. Choosing references switches to reference
mode explicitly and clears the first and last frame, with a note.

Files: `index.html`, `shell.js`.

### Then
- **Spoken lines on Home.** A quoted sentence in Home's box is offered as a chip: "Make this a spoken line?". It is
  moved into `dialogue` only when the user accepts, so the composer does not add the quiet-speech wording to a clip
  that talks.
- **Suggestions, not automation:**
  - "Several actions? Use 20 passes", following AGENTS.md's rule, as a one-click hint.
  - A length suggestion from the lines, brought to a count `lengths.py` takes and **capped at 15 s**. If the lines do
    not fit, say so instead of chaining or cutting. The rate is a starting figure until measured: `wordcheck.py` keeps
    no word timings.
- **Picture model hint.** The shootout's winner for the prompt type is shown beside the model choice ("Faces:
  Qwen 2.1 Full is better here, 43 s"), and the user's own choice stands.

  The everyday default should follow the shootout's conclusion (FLUX3-K Fast where it is installed). This is a
  default for the owner to set, not a classifier.
- **Long video:** the length menu shows "N chained clips", and the controls that do not apply say why at once.
  Splitting into beats waits.

---

## 3. Dropped or for the owner to decide

- **Splitting one box into scene, action and camera: dropped.** Home already sends the paragraph as the scene, and
  the composer joins scene, camera and action into one shot description. Nothing is lost. Asking the brain engine for
  it would load and unload it around renders, for little gain.
- **Choosing the picture model automatically from keywords: dropped.** The shootout supports recommendations, not a
  classifier. A keyword can silently pick a model several times slower. It survives as the hint above.
- **Automatic passes and length: reduced to suggestions.** A sentence count is a poor count of actions, and
  "2 actions → 8 passes" was invented. There are no word timings to set a speech rate from.
- **Inserting reference tags into the text by name: dropped.** It risks changing the user's sentence and naming the
  wrong subject. Chips at the cursor replace it.
- **Retake on by default for finals: the owner's decision.** AGENTS.md makes the retake the user's choice. The shot
  check itself already runs on every clip by default (`server.py:2340`, `shot_check` defaults to true). Only the
  second render is opt-in.
- **Beats split from a paragraph: waits.** Copying sentences into beats while the whole action paragraph is still sent
  can give each part conflicting instructions.

---

## 4. What the review corrected in the first draft

| First draft said | Checked in the code |
|---|---|
| Make the seed random by default | The preference exists. The bug is that it is drawn once, in `prefsToCreate`, and Create resends `C.seed`. Fix it at queue time. |
| Turn the shot check on | It already runs by default. Only the retake is opt-in. |
| Scout canvases are within 1 % of each other | Up to 3.5 % (864x480 against 1280x736). Fit by the shape rule, never stretch. |
| Portrait Draft is 480x864 | Draft is 1344x768 through the 2x decoder, so its portrait twin is 768x1344, from 384x672. Neither is measured. |
| Speech rate from Whisper word timings | `wordcheck.py` keeps words, not timings (`wordcheck.py:10, :324`). |
| Users type every reference tag | The composer already links subjects to `<Picture N>`. Only placing them in a sentence is manual. |
| Draft → Final keeps the take | A new size or pass count is a new render. "Higher quality" and "Upscale" must stay separate. |
| "Best" = 2K everywhere, with Upscale queued on a Spark | Upscale can be missing or refused (`actions.missing`), and it is a different operation from the Mac's decoder. No single "Best". |

---

## 5. Order of work

| # | Change | Effort |
|---|---|---|
| 1 | Another take = clone of the request with a new seed; More like these | S |
| 2 | Actions under every picture and clip | S–M |
| 3 | Scouts: Animate this, Use as end, follow typing, independent of the recipe | S |
| 4 | Quick Edit is the edit; full Edit behind More tools | S |
| 5 | Change something on the first frame | M |
| 6 | Edit this frame from a clip | S–M |
| 7 | A picture keeps its shape (16:9 first, others after a measured render) | M |
| 8 | Draft / Final / Sharp Spec with real sizes; jargon under More | S |
| 9 | Higher quality and Upscale, separate | M |
| 10 | Reference chips | S–M |

**First release: items 1, 3, 4 and 8.** They change the page and the settings only, not a render route.

**Then items 2, 5, 6 and 10:** page work that reuses existing jobs.

**Items 7 and 9 change what reaches the renderer.** Each needs a render on a Mac and a Spark through
`scripts/studio-check.py` before release.

**Tests to keep green:** `test_shell_page.py`, `test_settings.py`, `test_quick_page.py`, `test_api_answers.py`, and
`test_slate_page.py` where recipes are shared.
