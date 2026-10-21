# Wasserman Slate AI Studio, powered by TensorFold: install it, click it, make things

**Thank you first** to Sam Wasserman (Slate, the Sharp Spec, Motion Previs Studio), the Qwen team (Qwen-Image-2.1),
Black Forest Labs (FLUX 3 Image Klein) and felix-ab (flux3-mlx), OpenMOSS (MOSS-Image), MiniMax (H3 and MiniMax-Music3), FastVideo / Hao AI
Lab (FastH3), Fish Audio (Drama 3), Ash Hart (TensorFold), mrbizarro, LightX2V, Viggle, speach1sdef178 and Apple's MLX
team. Full list in [CREDITS.md](CREDITS.md). **Built with Fish Audio.**

What you get: an app on your Mac or DGX Spark that turns a prompt into an **image**, an edited picture, or a **video
with sound** from under a second to 30 minutes at 480p up to 2K; speech in cast voices (**Voice**); and whole songs with
sung vocals (**Music**). The app's own **Tutorial** tab has a short lesson for each of these, with *Show me in the
app* buttons that take you to the control.

## 1. Check your Mac

| Need | Why |
|---|---|
| Apple silicon with an **M5-family** chip | the fast kernels use its tensor units; other chips run far slower |
| **64 GB** memory or more | a FastH3 clip peaks near 32 GB on an M5, near 40 GB on an M1 to M4 (8-bit weights, much slower); 128 GB for the optional MiniMax H3 Turbo |
| **185 GB** free disk (250 GB with Turbo) | model weights: 31 GB image, 65 GB FastH3, 72 GB text encoder and decoders |
| Homebrew | to install Python 3.11, uv and ffmpeg |

Everything here was measured on one machine: a Mac Studio M5 Ultra with 256 GB.

## 2. One-shot install

```bash
brew install python@3.11 uv ffmpeg
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
cd Imagination-Studio-by-keys-Wasserman
bash oneshot-setup.sh --app
```

That one command installs the engine into its own folder (`~/.local/opt/tensorfold-studio`), downloads the models,
builds the app with a Desktop shortcut, and renders a 5 second test clip to prove it works. It can be stopped and
run again; downloads resume. Variants:

| Command | What it installs |
|---|---|
| `bash oneshot-setup.sh --app` | images and FastH3 video (about 175 GB); needs 64 GB of memory on an M5 |
| `bash oneshot-setup.sh --turbo --app` | also MiniMax H3 Turbo (about 240 GB); needs 128 GB of memory, and adds a second engine card |
| `bash oneshot-setup.sh --image-only --app` | image model only (33 GB) |
| add `--flux3` | the FLUX3-K Fast and Full picture models (41 GB; accept the FLUX Kommunity License on Hugging Face first) |
| add `--image-edit` | Qwen's base weights: Qwen 2.1 Full and the 40-step choice in Edit a picture |
| add `--ref2va` | reference pictures, videos and audio to video, MiniMax H3 Ref2VA (64 GB more; 128 GB of memory) |
| add `--motion` | the Motion Previs analysis (0.5 GB) |
| add `--voice` | Fish Audio Drama 3 for the Voice tab |
| add `--music` | MiniMax-Music3 for the Music tab (22 GB of weights; on an M5 also TensorFold's Metal engine, built in a few minutes) |
| `bash oneshot-setup.sh --verify` | checks an existing install, downloads nothing |
| `git pull && bash oneshot-setup.sh --upgrade` | moves an existing install to the newest version: the engine and scripts, no model downloads, your clips and projects untouched |

The models have their own licenses: Qwen-Image-2.1 is **non-commercial**; MiniMax H3 and FastH3 are under the
MiniMax H3 Community License; FLUX3-K under the FLUX Kommunity License; Drama 3 under the Fish Audio Community License
(a hosted voice or cloning service needs Fish Audio's own licence); MiniMax-Music3 under its Community License (a
commercial product must show "MiniMax-Music3" in its interface). The script prints the links before each download.

**On a DGX Spark** the same `bash oneshot-setup.sh` installs the CUDA build (it hands over to
`oneshot-setup-spark.sh`). It passes on `--image-only`, `--no-render`, `--verify`, `--motion`, `--voice`, `--flux3`,
`--music` and `--upgrade`; run `bash oneshot-setup-spark.sh --ref2va` (or `--image-edit`) directly for those. There is
no `--turbo`, no `--app` and no 2x decoder there. The steps below are the same on both machines unless they
say otherwise.

**Prefer to let an agent do it?** Give Claude Code (or another coding agent) this:

> Clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman and follow its AGENTS.md section "Install for a
> person and build the one-click app". Tell me before any large download starts and when the app is ready.

## 3. The clickable app

`--app` already built it. To build or rebuild it yourself:

```bash
bash scripts/make-app.sh                # ~/Applications/Wasserman Slate AI Studio.app
DESKTOP=1 bash scripts/make-app.sh      # and a shortcut on the Desktop
```

Double-click **Wasserman Slate AI Studio**. It starts the studio if needed and opens it in your browser; double-click again
any time to come back. Drag it to the Dock to keep it there.

- It is a small launcher around the folder you cloned. If you move that folder, run `make-app.sh` again.
- Stop it: `"$HOME/Applications/Wasserman Slate AI Studio.app/Contents/MacOS/TensorFoldStudio" stop`
- Log: `~/Library/Logs/TensorFoldStudio.log`. Everything you make: `~/ImaginationStudio`.
- It listens on this Mac only and asks nothing there. `HOST=0.0.0.0 bash scripts/make-app.sh` opens it to your
  network, and another device then signs in once with the access key (Settings → Access key shows a QR code); do
  that only on a network you trust.
- **Production:** two ways to plan a film.
  - The **Production board**: a project of segments, each with its own scene, action, line and length. A segment can
    continue from the last frame of the one before. One button renders all that are not done, and the board has the
    LoRA hub and the Sharp Spec for the whole project.
  - **Wasserman Slate AI Studio**: Sam Wasserman's Slate, built in. A First AD sets up scenes and shots from a plain
    description, the Bible and Studios hold the look and the cast, and every shot renders through the same queue. In
    its Sound tab, *Cast voice* gives a character a Drama 3 voice that follows them into shots that open on cast
    stills. The ✦ buttons need a "brain": your own Claude Code or Codex sign-in, or a local model.
- **Editor:** put clips in order, trim them, preview, export one file. Once the Music tab has a song, *Song under the
  export* lays it under the whole export, lowered while someone speaks.
- **Library:** everything you have made.
  - Under a picture: *use* (a first frame), *last frame* (end a clip on it), *edit* (Edit a picture), *new angle*
    (with the Multiple Angles LoRA) and *Turnaround*.
  - Under a clip: *continue* (a new clip on its last frame), *+ timeline*, *Upscale* and *Remove a person*.
  - A clip made on another Spark shows that Spark's name and its seed.
  - Tick files and press **Delete selected** to delete several; **Restore last deletion** puts them back; **Empty
    trash** erases what the trash holds and frees the space.
- **Tutorial:** short lessons for every tab, one step at a time. *Show me in the app* opens the tab and outlines the
  control; a bar at the bottom carries the lesson on from there.
- **Clearing:** every prompt form has **Clear** (with **Undo**). In the queue, 🗑 deletes a finished job and what it
  made, and **Clear failed** / **Delete all finished** tidy the list. Deleted files go to `~/ImaginationStudio/trash`.

## 8.5 The LoRA hub: a trained look or move for a clip

A LoRA is a small file that teaches the video model one thing: a camera move, a fighting style, a drawn look. The
**Keys and Wasserman LoRA Hub** is in step 1 of Create (and on the Production board and in a Slate film's settings).

1. Press **▸ Browse the hub**. The LoRAs are in groups: *Wasserman*, *01 Camera & Viewpoint*, *02 Transformation &
   Morphing*, *03 Character Editing*, *04 Motion & Action*, *05 Art Style*, *06 Support & Finishing*, *07 Fun*. Pick a group or
   type in the search box.
2. Each card says what the LoRA does, who made it, what it works from, its licence and its size. After
   `bash oneshot-essential-bundle.sh` (the installer runs it) every usable LoRA is already installed; otherwise press
   **⤓ Install** on a card, or **Install all in this category**.
3. Press **Use on this clip**. A row appears with **Strength** (the author's range is in its tooltip) and, when the
   LoRA has them, a choice: a **Look**, or for *Camera Motion* a **Camera move** (push-in, pull-back, handheld
   follow, orbit, crane reveal, tilt, pan). Its trigger word is written into the prompt for you. Up to four can be
   stacked. If the LoRA needs something of the clip (a last frame, a source video, a picture), its row says
   **Needs:** with a button that takes you to the step, and step 5 will not queue the clip until it is there.
4. Write the prompt the way the card's note says, then render as usual. A clip with a LoRA takes a slower path and
   shows no time estimate. On a Mac, FastH3 then runs on the MLX engine, not the native one. On a DGX Spark the
   LoRA is merged into TensorFold's int8 engine (since 2.7.1), at the same memory as a clip without one.

A slider LoRA such as *Anime Slider* has a track centred on 0: drag right for flat 2D anime (3 is plenty on a clip
from text), left for a photographic look, or press *Off (0)*. On a Mac, a slider on a clip *from references* pulls
about half as hard as on a DGX Spark: use about twice the strength there.
*Equirectangular 360°* wants the Custom size 1120x480; the clip is the whole sphere, to be resized to 2:1 for a VR
player.

### LoRAs for a clip made from references

Three LoRAs in the hub do one job each on a clip made from references (step 1 of Create, *From references*). Pick
one and its row says what the clip needs and whether it has it; *Go to References* takes you there.

| LoRA | Put under References | Then |
|---|---|---|
| **Character Swap** | your clip as Video 1, a picture of the new character | under Video 1, in "who or what it shows", say who is replaced: *the man in the blue jacket* |
| **Lineart Anime** | any clip as Video 1, and no picture | press *Make line art of Video 1* in the LoRA's row (fine, medium or outlines only), then describe the finished picture in Scene and Action |
| **Keyframe Animation** | one drawing, or two (the first drawing and where the action ends) | choose 20 passes; Scene is the framing and the medium (*clean line on white*), Action is the movement |

The prompt in Review is then written the way that LoRA's author trained it; you can still edit it. The **Anime
Slider** works on any clip, references or not: plus leans to flat 2D anime, minus to a photographic look.

Worked example, Lineart Anime: add a clip of a man practising at a wooden dummy as Video 1, pick Lineart Anime,
press *Make line art of Video 1*, and write Scene "A young martial artist with short black hair in a dark blue
jacket on a rooftop at dusk, red paper lanterns", Action "He strikes a wooden training dummy". The clip comes back
drawn as anime on the same outlines and timing.

## 8.6 Library actions: turn a picture round, make a clip larger, take a person out

Three LoRAs of the hub have a job of their own, and the Library has a button for each. On a DGX Spark they come with
`oneshot-setup-spark.sh --ref2va` (three node packs, SAM 3.1 and the H3 latent upscaler, 2.5 GB). They are queued like any
render; the result lands in the Library.

**Turnaround** (under a picture; Mac and DGX Spark). Five views of the picture's subject, turning from where it
faces now, made together so the character is the same in all five.

1. Press **Turnaround** under a picture. A character or an object standing upright, whole in the frame, works best.
2. Choose the size of each view: *512* is the fastest, *1024* the one to keep. Choose **The turn** (goes furthest
   round) or **The look** (turns less, stays closer to your picture).
3. Press **Make the turnaround sheet**. The sheet shows the five views side by side; each view is also a picture of
   its own, with **first frame** (start a clip on it) and **reference** (add it to a reference clip's pictures).

If a view comes out tilted or seen from above, change the seed. The instruction box takes your own wording; it
names the picture as `<Picture 1>`.

**Upscale** (under a clip; DGX Spark). A finished clip made larger and sharper, with its motion, mouths and sound
left as they were.

1. Press **Upscale** under a clip and choose the size: *2 megapixels* is about 1920 x 1080.
2. Leave the re-draw on **Light**. *None* only enlarges; *Stronger* adds more detail of its own.
3. Press **Upscale this clip**. A 5 second 864x480 clip took about five minutes at 2 megapixels.

**Remove a person** (under a clip; DGX Spark). One person taken out, the background drawn again.

1. Press **Remove a person** under a clip and say who, in a few words: *man in a blue shirt*, *woman in a red coat*.
2. Press **1. Find the person**. In under a minute the clip comes back with the person painted green. Look at the
   hands, the hair and the shadow. If something is missed, or someone else is green, describe the person more
   exactly and press it again.
3. The removal needs a first frame without the person. **Build it for me** makes one from the clip. With **My own
   picture** you load your own edit of the clip's first frame (same size, the person painted out).
4. Press **2. Remove the person**. The clip is worked through in overlapping pieces of 22 frames; a 5 second clip
   took about eleven minutes.

The whole picture is drawn again, so small things elsewhere can change too. It works best on a short, continuous
shot. The clip keeps its own sound, which still holds the removed person's voice: tick **No sound** to leave it out.

## 9. Motion Previs: make a clip that moves like another

The analysis in the **Motion Previs** tab is a port, modified, of Motion Previs Studio by Sam Wasserman
(wassermanproductions.com), Apache-2.0. The app has the same lesson step by step: **Tutorial → Motion Previs**.

**What it is for.** You have a shot whose movement you like: a drill, a fight, a camera move. Motion Previs takes the
movement apart (the camera move in words, a pose skeleton, a depth layer, edges, a motion mask), and the create
wizard then makes a new clip that keeps the part you choose: the same moves with another person, in another place,
or only the camera. A reference video without the analysis is a plain reference: the model sees the clip and nothing
tells it what to keep.

It needs the motion environment (`bash oneshot-setup.sh --motion`, 0.5 GB) and, to render the new clip, MiniMax H3
Ref2VA (`--ref2va` on a Mac; part of the DGX Spark install).

### 9.1 Analyse the shot

![The Motion Previs tab](samples/ui_motion_previs.png)

1. **1 · The shot:** *Upload a clip*, or *Pick from the studio folder* for an earlier render or upload. Choose the
   part to analyse, 2 to 15 seconds. (*Upload a pack ZIP* takes a pack exported by Motion Previs Studio.)
2. **2 · Reference mode:** what the new clip keeps.

   | Mode | Keeps | Use it for |
   |---|---|---|
   | Camera only | the camera move and its timing | the same push-in or crane on another subject |
   | Actor motion | the body motion and the camera | the same moves, a new person in a new place |
   | Object motion | an object's or a vehicle's path and the camera | a car, a ball, a prop |
   | Full scene | camera, blocking, subject motion and depth rhythm | the same shot with another person in it |

   The mode does not change what is measured, and you can change it again in the wizard.
3. **3 · Layers:** leave them all ticked the first time. *Samples a second*: 12 is the original's setting, 24
   follows every frame of a Studio clip. *Pose model*: Lite is fastest, Heavy most exact.
4. **Run analysis.** It runs on the processor beside the render queue. A 5 second 864x480 shot with every layer took
   **6.6 s on a Mac Studio M5 Ultra** and **13 to 18 s on a DGX Spark**.

### 9.2 Read the result

![Review: the camera move in words and the layers side by side](samples/ui_motion_review.png)

**4 · Review** shows the length, the size and a quality figure, then **The camera move, in words** with its numbers
(pan and tilt in pixels, zoom, roll, confidence). Under it the reference, the skeleton, the depth and the edges play
side by side; *▶ Play all together* starts them in step. Check two things:

- **The sentence matches the shot.** A pan and a sideways track look alike to this solve; with a low confidence, read
  it against the clip.
- **The skeleton sits on the right person.** The pose pass follows **one person only**: in the duel above it is on
  one fighter, and asked for up to four people it still returned one in every frame.

### 9.3 Send it to a new clip

![5 · Motion reference](samples/ui_motion_handoff.png)

In **5 · Motion reference** pick the *layer that becomes `<Video 1>`*, the reference mode, the length, the size and
the timing (*Fit the whole shot to the length*, or *Keep its speed: cut or hold*), then press **Use as motion
reference**. The create wizard opens on its references screen with that layer as `<Video 1>` and the camera move
ready for the prompt.

| You want | Send | Add as `<Picture 1>` |
|---|---|---|
| the same place, a new person | *Reference clip* (the tested route) | a picture of the new person |
| a new place | *AI depth*, as a control video | a picture of the opening frame: the look and the place come from it |
| only the camera move | *None: the camera move in words only* | nothing, or a first picture of your own |

The *OpenPose skeleton* is offered too; it held the motion most loosely (a kick was lost in our test).

You can also start in the wizard: *The clip starts from → References*, add a video, and press **Analyse motion**
under it.

### 9.4 The choices in the wizard

![Motion of Video 1 in the create wizard](samples/ui_motion_wizard.png)

- **What to carry over** is the reference mode again; **What goes to the model as `<Video 1>`** is the layer.
- **People in `<Video 1>`:** *One*, or *Two or more*. You have to say it; the analysis does not count them. With a
  picture of a new pair and *One*, only one fighter of the duel was replaced; with *Two or more*, both were.
- **Sound and movement.** A reference is followed at its own speed, whatever the prompt says, so the sound has to
  agree with it:

  | Choice | Movement | Sound |
  |---|---|---|
  | Impacts | fast strikes and blocks. Over a slow reference the video is left out and the picture and the prompt carry the fight; the camera move stays, in words | the hits |
  | Flow | slow, smooth, continuous; no strike lands | none, or music if you name it in step 3 |
  | Automatic | as you wrote it, except that hits over a slow reference become Flow | as Flow in that one case |

  The line under the choice says what was measured in `<Video 1>` (for example "the quickest movement is 14.4 percent
  of the frame width a second (slow; fast is 24 or more)"). When the clip is done its job card says
  **Sound and movement: agree**, or **impact sound over slow motion** in red: a flag beside the clip, never a failed
  job.
- **Keep the mouth of `<Video 1>` out of the clip** is on when nobody speaks: the mouth of the person in the source
  clip is smoothed over before the model sees it. Left in, the new face mouths that person's words. It turns itself
  off when the clip has spoken lines or reuses the video's own sound.
- **Add the camera move to the prompt** writes the solved sentence into the prompt, unless the Camera field of step 1
  has text of its own.
- **Passes** (step 1, *Reference to video*): *Turbo 4 passes* to start with, *20 passes* for the model as released,
  *Custom* for a number of your own.

### 9.5 Two worked examples

Both are 5 seconds at 864x480, seed 7, from two cuts of a martial-arts short with fictional people.

**The dummy drill with a new person.** A man drills a wooden dummy while the camera pushes in to a close-up.
Analyse it; *Full scene*; *Source clip* as `<Video 1>`; a picture of a woman as `<Picture 1>`; *People: One*.

| Passes | Mac Studio M5 Ultra | Pose distance (median / 90th percentile) | What came out |
|---|---|---|---|
| Turbo 4 | 231 s | 3.7 / 11.2 | she does the drill and the push-in on the same rooftop |
| 20 | 741 s | 4.2 / 11.7 | the same, no closer |
| Turbo 4, depth + a picture of her in a bamboo clearing | 235 s | 3.5 / 11.7 | the drill, in the new place |

On a DGX Spark the first row held at 2.9 / 7.7.

**The duel with two people.** Two fighters trade hands while the camera cranes up. The same route with a picture of
the new pair and *People: Two or more*. The source's hands are slow and sticky, so choose the sound to match:

| Sound and movement | Mac Studio M5 Ultra | DGX Spark | What came out |
|---|---|---|---|
| (before the choice existed) | 284 s | 216 s | slow hands under slapping sounds: flagged "impact sound over slow motion" |
| Impacts | 89 s | 101 s | both punch and block at speed, with the hits |
| Flow, no music | 262 s | 206 s | slow hands, silent |
| Flow, with music | 272 s | 207 s | slow hands under a plucked melody |

One seed each, so expect some difference from run to run.

**Reading the distance figures:** they are how far the body's keypoints in the new clip are from the source's, in
percent of the frame width, as a median and a 90th percentile over the clip; lower is closer. In our runs the motion
was held under about 5 and 12; 26.4 / 36.6 was a clip that lost it. The figures follow one person, so they are not
valid for a clip where the check picked a different fighter than in the source.

### 9.6 When it goes wrong

| What you see | Why | What to do |
|---|---|---|
| The person drifts out of frame and the moves are lost (Mac) | The Turbo adapter, a reference video and the int8 kernels together; fixed since 2.6 by running that case in bfloat16 | Update; if it still happens, use 20 passes |
| Only one of two people is replaced | *People* is set to *One* | Set *Two or more* |
| A fight stays slow under hit sounds | The reference is slow and is followed at its own speed | *Impacts* (the video is left out) or *Flow* (no hits) |
| The new face mouths words nobody says | It copies the mouth of the person in the source clip | Leave *Keep the mouth of `<Video 1>` out of the clip* on |
| Music nobody asked for | The model adds it by itself, even when told "No music" | *Flow* without music delivers the clip silent |
| The layout of the place shifts | Depth with a picture of the *same* place pulled two ways (16.0 / 25.3 in our run) | For the same place send the source clip; keep depth for a new place |

## 10. Voice: speech with Fish Audio Drama 3

The **Voice** tab turns text into speech with Fish Audio Drama 3, on this machine, through TensorFold's port: MLX on a
Mac (`bash oneshot-setup.sh --voice`), native CUDA on a DGX Spark (`bash oneshot-setup-spark.sh --voice`). Nothing
is sent to Fish Audio. **Built with Fish Audio.**

1. **What is said.** Type the words. Speaker tags switch voices, `<|speaker:0|>` to `<|speaker:15|>`, one turn on each
   line. Directions in square brackets steer the delivery: `[whispering]`, `[sighs]`, `[laughs]`, or a description
   of the voice. *Two-speaker example* fills the box.
2. **Seed, Temperature, Top-p, Top-k.** Leave the sampling on *Fish defaults* (1.0, 0.9, 30). The seed picks the take.
3. **Voice to clone (optional).** A clean recording of one speaker, about 10 to 30 seconds, and what it says (left
   empty, Whisper writes it). Tick **I have the speaker's consent to clone this voice**: the take will not render
   without it. Without a clip, Drama 3 makes a voice of its own; describe it in a direction.
4. **Render the take.** The take plays in the tab while it is made. Whisper checks the words; a part with missing
   words is drawn again if it has not been played yet, or reported. On a Mac, untick *Play while it is made* to have
   every short part drawn again before you hear it.
5. **Takes.** Under each: *Download WAV*; *Clone this voice* (the same speaker for the next line); *Again, next seed*;
   and **Use as Ref2VA voice reference**, which opens the create wizard with the take as reference audio (2 to 15 s; a
   longer take is cut to its first 15).

| | Mac Studio M5 Ultra | DGX Spark |
|---|---|---|
| Load, first take | about 5 s | about 18 s |
| Memory while loaded | about 50 GB | about 42 GB |
| Making the speech | about 0.35 s for each second of speech | about 0.7 s for each second |
| First sound, model loaded | 0.2 to 0.3 s | about 0.3 s |

The model stays loaded for five idle minutes and is unloaded at once when a video or picture job starts: Drama 3 and
the video model never run together.

## 11. Music: a song with MiniMax-Music3

The **Music** tab makes a whole song with sung vocals from your lyrics and a style, on this machine: up to five
minutes, 44.1 kHz stereo. Install it with `bash oneshot-setup.sh --music` (on either machine). The tab, and anything
that plays a song, shows "MiniMax-Music3", as its licence asks.

Which engine runs depends on the machine; the tab says which:

| Machine | Engine | Plays while it is made | A 60 s song |
|---|---|---|---|
| Mac with an M5 or later | TensorFold's native Metal engine (Zig and Metal, since 2.9.2) | no, not yet: the song plays when it is done | 59.5 to 63.0 s on an M5 Ultra on TensorFold's command line, 17.8 GB at the peak; through the app a 33.4 s song took 35.2 s |
| Mac with an M1 to M4, or `MUSIC3_BACKEND=mlx` | TensorFold's MLX port, int8 | yes: it starts once enough is made that it never stops, about 13 s into a 60 s song | about 63 s on an M5 Ultra, through the app |
| DGX Spark | TensorFold's CUDA engine, int8 (ComfyUI's nodes where it is not built) | no | about 149 s on one GB10, through the app |

1. **Lyrics.** Section tags on lines of their own, above the lines they hold; the buttons under the box add
   `[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`, `[Bridge]`, `[Instrumental]` and `[Outro]`. Lines in
   (parentheses) are backing vocals. Leave the box empty for an instrumental. A note says when there are no tags, or
   a tag the model may sing as words. *Pop example* fills both boxes.
2. **Style.** A caption: genre, tempo, key, mood, the voice that sings, the instruments. The **Style helper** has a
   box for each; **Write the style** turns them into the caption.
3. **Length (s)** is the longest the song may be, 10 to 300; the model may end it sooner. **Seed** picks the take.
4. **Generate.** On a Mac with the MLX engine, leave **Play while it's made** ticked to hear it as it is made. Tick
   *Have Whisper listen for the lyrics after* for a rough score: sung words are harder to hear than spoken ones.
5. **Songs.** Under a song: *Download WAV*, *Again, next seed*, and **Use as soundtrack**. Choose a video (a long
   video, the Editor's export or any Library clip), set the **Music level (dB)** and press **Lay it under**. The song
   sits under the video's own sound, is lowered while someone speaks, and no peak goes over -1 dBFS. The result is a
   new file in `exports/`.

One song runs at a time, never beside a video or a voice take. The engine stays loaded for five idle minutes and is
unloaded at once when another job starts. The Metal engine was measured on an M5 Ultra only.

## 12. More than one DGX Spark

On a DGX Spark a job may use more than one Spark. **Sparks**, a row of numbers from 1 to 8, is how many machines a
job may use, this one counted. It is at the top of the queue on every tab, in step 5 of Create, on the Production
board and on a Slate shot. Choose more than there are and the app uses what is there: *5 of 8 available*. Under the
row it says what the number does for this job, and lists any Spark not used, with the reason.

- **Several clips** (queued clips, a board's segments, a Slate scene's shots) render one per Spark, side by side, and
  come back into this Spark's Library and project.
- **One clip** renders on one Spark. A long video, pictures, edits, Library actions, voice takes and songs stay on
  this Spark.
- **The Sparks tab** lists the other Sparks. Under *Add a Spark*, type the address you would give `ssh` and press
  **Add**. The other Spark needs the Studio installed once (`oneshot-setup-spark.sh`) and a key login from this one;
  no password is asked and nothing runs as root. Per row: **Check** only looks; **Update and start** sends this
  Spark's Studio code (not the models) and starts its app; **Stop** frees its memory; untick *use* to give it no
  clips.
- A worker that does not answer for 60 seconds during a job is marked down and its clip goes back to the queue. The
  job card says *Taken back from* that Spark, with the reason. Press **Check** on it before it is used again.

Measured through the app, eight 56-frame 864x480 FastH3 clips: 263 s on one Spark, 130 s on two, 98 s on three, 69 s
on four, 65 s on five. Six to eight Sparks are not measured, and one clip split across several Sparks is not in the
app. On a Mac the tab says there is one machine.

## 13. Memory on a DGX Spark

On a Spark the GPU and the system share one pool of memory, and a memory guard stands beside every render.

- **Before you queue.** The estimate in step 1 and step 5 adds *about N GB of this machine's memory*, from measured
  peaks. A clip that would leave the machine under its floor (a tenth of the memory, 12.4 GB) shows **This clip will
  be refused: memory**, with the reason and the nearest size and length that fit. Choose those. Do not lower the
  floor to make a clip fit.
- **While it runs.** The guard reads the memory twenty times a second and stops the render server if memory falls
  under the floor, falls too fast, or the kernel reports work stalled on memory. The job fails with that reason in
  its error line and its **Log**. Close what else holds memory and queue it again.
- **On other Sparks.** A clip sent to a worker passes that worker's own guard first; a worker that refuses is listed
  as not used, with its reason.

A LoRA on FastH3 is merged into TensorFold's int8 engine and takes what a clip without one takes, measured up to
1376x768 at 10 seconds. A Spark whose engine predates 2.7.1 renders LoRA clips on ComfyUI's own blocks, and refuses
them above 1280x736 at 124 frames.

## 14. Without the app

```bash
bash scripts/fast.sh "a prompt" out.mp4                          # FastH3: STEPS=4|8|20 RES=480p|720p UPSCALE=1
bash scripts/image.sh "a prompt" out.png                         # image only
bash scripts/studio.sh "the picture" "what happens" out.mp4      # Qwen image, then video (ENGINE=fasth3 for FastH3)
FIRST_FRAME=photo.jpg bash scripts/video.sh "what happens" out.mp4
```

## 15. Your phone and other devices: the access key

On the computer running the studio there is nothing to do: a browser there (opened as `localhost`) never asks for a
key, and updates work as always. The access key is for reaching the studio from anywhere else: a phone, a tablet,
another computer. Each device signs in once and stays signed in.

**Sign in your phone, step by step**

1. Install [Tailscale](https://tailscale.com) on the computer and on the phone, signed in to the same account (free for
   personal use).
2. On the computer, once, in a terminal: `tailscale serve --bg 7870`. Only your own devices can open that address,
   over HTTPS.
3. On the computer, open **Settings → Access key**. It shows a QR code.
4. Point the phone's camera at the QR code and open the link. The studio opens on the phone, already signed in.
5. *Share → Add to Home Screen* (iPhone) or *⋮ → Add to Home screen* (Android). The studio then opens full screen from
   its own icon, still signed in.

**Without a camera.** In **Settings → Access key** press **Show** or **Copy**, and type the key on the device when the
studio asks ("Sign this device in"). In a terminal on the computer, `bash scripts/studio-key.sh` prints it too. The key
is a password to your studio: send it only to your own devices.

**A lost phone, or a key that got out.** **Settings → Access key → Make a new key.** Every other device is signed out at
once; the browser you pressed it in stays signed in. Sign your devices in again with the new QR code.

**Another machine's studio.** Slate's *Import a take rendered on another machine* asks for that studio's key beside its
address: copy it from that machine's Settings → Access key.

The same steps are in the app: **Learn → Your phone and other devices**.

## If something goes wrong

| What you see | What to do |
|---|---|
| "run oneshot-setup.sh first" | the install did not finish; run it again, it resumes |
| "no FastH3 checkpoint" | `bash oneshot-setup.sh --no-render` |
| The app says it did not start | read `~/Library/Logs/TensorFoldStudio.log`; often another program holds port 7870 (`PORT=7871 bash scripts/make-app.sh`) |
| A job fails | open its **Log** in the queue; the last lines say why |
| Very slow renders | check the chip is M5-family and nothing else is using the GPU |
| The job says "MLX engine" for FastH3 | the native engine is not installed or the chip is older than M5, or the clip has a LoRA: run `bash oneshot-setup.sh --verify` and look for the `tf-h3-dit` line |
| "This clip will be refused: memory" (DGX Spark) | choose the size or length the message names (section 13) |
| A song does not play while it is made | on an M5 Mac the Metal engine does not stream yet, and a DGX Spark never does; `MUSIC3_BACKEND=mlx` runs the MLX engine on a Mac, which does |
| The Voice or Music tab says "Not available here" | install it: `bash oneshot-setup.sh --voice` or `--music`; the tab says what is missing |
| The Render the take button stays off with a clip to clone | tick the consent box |
