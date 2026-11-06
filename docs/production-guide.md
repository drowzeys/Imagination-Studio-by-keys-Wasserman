# Production, finishing and Motion Previs

[Back to the README](../README.md)

Detailed film workflows. Check the app for the features and recipes available on the current machine.

## A rough cut on the beat, straight to Resolve

Slate's **Rough cut** tab (also at the foot of **Deliver**) cuts a film to a song: pick the song (any song, voice line or sound in the Library), choose
where the cuts fall, and press **Make a rough cut**. The studio takes the best take of every shot (circled, else
good, else the newest that is not marked no-good) in scene and shot order, finds the song's beats, and lays the clips
end to end so each cut lands on the music:

- **Each shot's own length** (the default) keeps every shot near its own length and cuts it on the nearest beat;
  **One bar each** and **Two bars each** fit every shot to the bars; **One beat each** is a fast montage.
  A clip is trimmed around its middle to fill its slot; a clip shorter than its slot keeps its whole length and the
  next cut lands back on the grid.
- **Natural** lets each clip run about its own length and cuts it on the last beat it reaches.
- **As filmed**, **Shuffled** or **Calm to busy** (the stillest clips first) sets the order.
- **Foley for each clip** first makes each clip's sound for its picture (MMAudio, clips of 15 s or less; see Sound
  effects and foley), and the cut uses those clips. Slower: one foley job a clip.

The cut becomes an Editor project of its own and its export is queued with the song under it, lowered while someone
speaks. When it is done the panel plays it and offers **Open in Resolve**: a zip with the FCPXML and every clip and
the song in `media/` beside it (unzip it, then in DaVinci Resolve File > Import > Timeline), and the FCPXML and an EDL
on their own. All three are kept in `~/ImaginationStudio/exports/`.

In the **Library**, tick some clips and press **Trailer from these** for the same cut from a selection: shuffle it, or
cut it calm to busy, for a different trailer from the same clips.

The beats are found on this machine with numpy (no extra install): the tempo between 60 and 200 BPM, every beat, and
the first beat of each bar (4/4). A song is measured once; the result is kept beside it as `<name>.beats.json`.
Very fast songs may read at half tempo, which still cuts on the beat. It runs on a Mac and on a DGX Spark with no install
flag; it needs ffmpeg.
`POST /api/music/beats {path}` gives {bpm, beats, downbeats, bars, duration, confidence};
`POST /api/roughcut {film | clips, song, cut, order, foley, title, width, height}` makes the cut and
`GET /api/roughcut/{project}` follows it. Agents have the same as `music_beats`, `rough_cut` and `rough_cut_status`.

## The night shift

Plan a batch before bed, and wake up to dailies on your phone.

- **Plan it.** In the **Night shift** room of the sidebar (also a panel on **Home**): pick a film from Production and *Plan the shots without a good take*
  (every shot with a prompt and no take that is good or circled), or in the film's Slate, *Render the rest tonight*.
  An agent can do the same with the `night_shift` tool.
- **When it starts:** *When idle* (once the queue has had nothing in it for two minutes), *At 23:00* (any time you
  set), or *Now*. A planned shift can be started by hand or cancelled; one shift runs at a time.
- **While it runs** the shots go into the queue one after another, never more than two waiting, so anything you queue
  yourself still gets its turn. Each shot's take is logged in the film as it is queued, with its QA gate and shot
  check results added when it finishes. The plan is kept in `~/ImaginationStudio/nightshift/`: if the studio restarts,
  the shift carries on from the next shot that has no clip.
- **In the morning,** in `~/ImaginationStudio/dailies/`, named by date and shift:
  - a **reel** (`.mp4`): every clip after a 1.5 s title card with the shot's name, its seed and whether it passed its
    checks; 1280x720, 24 fps, with sound. Play it in the panel.
  - the **dailies package** (`.zip`) for Circle Take: each clip named by its shot, with its prompt, seed and model beside it.
  - **notes** (`.md`): each clip's verdict, why it failed and where it is, the ones to look at first.
  - a **notification**: "Night shift done: 7 of 8 clips, 2 to look at".

**Notifications on your phone.** Open the studio on the phone at its Tailscale HTTPS address (see
[Using it on your phone](user-guide.md#using-it-on-your-phone)), then **Settings → Notifications → Turn on**, and *Send a test*.
On an iPhone or iPad, first *Share → Add to Home Screen*, open the studio from that icon, and turn notifications on
there: iOS shows them only for a studio added to the Home Screen (iOS 16.4 or later). Android Chrome works in a tab.
The studio makes its own keys (kept in `~/ImaginationStudio/push/`) and sends through the phone's own push service;
there is no account. It needs the `cryptography` Python package (installed by the setup, with `pywebpush`).

**What it needs.** It runs on a Mac and on a DGX Spark with no install flag. The reel needs ffmpeg; without a font on the machine the title cards are plain black. Routes:
`GET/POST /api/nightshift` (`{film, shots?, start, name?}` or `{items: [{kind: "shot", film, shot} | {kind: "job", body}], start}`;
a job item is one job, not a Restyle, Extend or Fix),
`GET/DELETE /api/nightshift/{id}`, `POST /api/nightshift/{id}/start|cancel`; `GET /api/push/key`,
`POST/DELETE /api/push/subscribe`, `POST /api/push/test`.

## Finish: the final cut and mix, to Resolve, Premiere or After Effects

**Finish** turns a timeline into the finished film, rendered by [Tesseract](https://github.com/mirage-hq/tesseract)
on your own computer: each clip cut where its In and Out say, the clips' own sound (the dialogue) kept at full level,
a narration take laid from the first shot, and the music under all of it, faded in and out and lowered wherever a clip
or the narration has sound. A title, when you give one, opens the film as a scene of its own: a card with the title, a
line and the subtitle, fading and rising in.

The Finish panel sits on the Timeline and on Edit → **Finish**; in the Library, tick clips and press **Finish these** to finish them as one
cut, in the order you ticked them. Choose the **Mix** (Dialogue first: the music at -18 dB and 12 dB lower under
speech; Balanced: -10 dB and 12 dB lower; Music forward: -5 dB and 6 dB lower), the size (720p, 1080p or 4K) and the
frames per second, then **Finish the cut**. Captions, when ticked, are the words a speech-to-text model on this
computer hears (`pip install faster-whisper`); without one they are left out, never made up. When the job is done:

- **Download MP4**, and **.tsrct project**: the editable Tesseract project with its media packed inside.
- **Open in Resolve**: the timeline with its media as a zip (File > Import > Timeline), or the cut's FCPXML; the job
  also writes an EDL.
- **Premiere** and **After Effects**: the cut as a `.prproj` or `.aep` project with its media (and the title's fonts,
  to install before opening), made by [Tesseract Converter](https://github.com/mirage-hq/Tesseract-Converter). The
  converter leaves out or approximates some features, and its After Effects projects are experimental: the zip's
  README lists what it said.

Everything lands in `~/ImaginationStudio/exports/`. `POST /api/jobs {kind: "finish", params: {project | clips[{path,
in, out}], song?, voice?, title?: {text, subtitle}, captions?, mix: dialogue|balanced|music, size: 720p|1080p|4k,
fps: 24|25|30|60}}`, `GET /api/finish` (is Tesseract ready, and where), `POST /api/finish/<job>/convert {to:
premiere|aftereffects}`. The Agent room's tools are `finish_cut` and `export_cut`.

**What it needs.** Every install includes it (`oneshot-setup.sh`, or `--finish`; `--no-finish` leaves it out).
`bash scripts/setup-finish.sh` on its own (`--verify` only reports) installs Tesseract 0.4.0 and Tesseract
Converter 0.3.0 from their GitHub releases into `$PREFIX/finish`, each checked against its published SHA-256, with
Instrument Serif and Geist for the title. A Tesseract already installed by itself is found too. About 150 MB; no
account, key or sudo. For the Studio, Tesseract runs on macOS and Linux x86_64 (with ffmpeg and a Vulkan driver), **not on
Linux ARM such as a DGX Spark**: there, run the script on your Mac as well and set the Mac as the remote computer in
Settings > Connections (Brains & tools). The Spark's Studio then copies the clips to the Mac over ssh, finishes the
cut there and brings the results back; the panel says "Runs on <your Mac> over ssh".

**Licences.** Tesseract is free to download and use but not open source: it comes under Mirage's own terms (TERMS.md
beside it), which leave out commercial use by a business with a yearly revenue of US$1 million or more without a
separate agreement, and do not allow passing it on. It may send basic usage counts for some commands (no file names,
paths or contents), on by default; the Studio never changes that setting, and `tsrct telemetry disable` turns it off.
Tesseract Converter is MIT. Instrument Serif and Geist are under the SIL Open Font License. Everything else in Finish
(the studio's own code, the cut, the mix, the Resolve files) runs on your computer, free, with no account or key.

## Motion graphics: titles, captions, slideshows and music videos

**Motion graphics** in the sidebar (also Edit → **Graphics**) makes motion graphics from what is already in the Library and renders them to MP4 on this machine,
with [HyperFrames](https://github.com/heygen-com/hyperframes) (open source, Apache-2.0) and GSAP. Pick what to make,
fill in the words, pick the clips, pictures, voice line or song, choose a size and press **Make it**:

- **Title**: a film title and a subtitle, dark and slow, over black or over a clip.
- **Lower third**: a name and a role laid over a clip, at the time you say, for as long as you say.
- **Kinetic type**: a sentence word by word, each word on a beat of a song.
- **Captions**: a clip's speech burned in, word by word as it is said. The words are heard on this machine (with
  faster-whisper, or the whisper command, as the QA gate does) the first time and kept beside the clip as
  `<clip>.words.json`, so the next captions of that clip start at once.
- **Slideshow**: pictures with a slow push-in, crossfaded, with a voice line (its length sets the slideshow's) or a song.
- **Music video**: clips cut in turn on the beat of a song, with flash and whip cuts. The beats come from the song when
  the studio can find them ("Find the beats"), otherwise from the tempo you give.
- **Explainer**: sections of a heading, a few words and a picture, timed to a voice-over: each section gets its share of
  the voice by its length.

Under a clip in the Library, **Add a title** and **Caption this clip** open the tab on that clip. Sizes: 1080p, 720p,
vertical, square, or the clip's own size; 24 frames a second. The voice is a Voice tab take and the music a Music tab
song: nothing is sent anywhere, there is no account, and nothing is rendered in a cloud.

Each graphic is one job of the queue: its page is copied to `~/ImaginationStudio/graphics/<job>/` with what it shows in
`data.json`, and the MP4 lands in `renders/<job>.mp4` and the Library. For your own tools:
`POST /api/jobs {kind: "graphics", params: {template, size?, …}}` (`template` is `title`, `lower-third`, `kinetic`,
`captions`, `slideshow`, `music-video` or `explainer`), `GET /api/graphics`; agents have `make_graphics`.

**Install** (about 400 MB, in `$PREFIX/graphics`): every install includes it (`oneshot-setup.sh`, on a Mac or a DGX
Spark; `--no-graphics` leaves it out), or `bash scripts/setup-graphics.sh` on its own. It needs ffmpeg and Node 22 or newer: when the machine's Node is
older (a DGX Spark has Node 20), the official Node 22 is downloaded from nodejs.org for HyperFrames alone, its checksum
checked. It renders in a headless Chromium: one already in Playwright's cache is used, otherwise HyperFrames' own, or
on a DGX Spark (Linux on arm64) Playwright's Chromium for arm64. `--verify` checks an install and downloads nothing.
Fonts are the studio's own Geist and Instrument Serif (SIL Open Font License).

## Motion Previs: a shot into control layers and a camera move

**New to it? Start with the walkthrough:** [TUTORIAL.md, section 9](../TUTORIAL.md#9-motion-previs-make-a-clip-that-moves-like-another)
(two worked examples, what each choice does, and what to do when a clip goes wrong), or **Tutorial → Motion Previs** in
the app.

The **Motion Previs** tab takes a clip (one you upload, or an earlier render or upload), a range of 2 to 15 seconds
and one of four reference modes (camera only, actor motion, object motion, full scene), and writes a production pack
under `~/ImaginationStudio/motion/<id>/` with a ZIP beside it: the trimmed reference clip, a pose track
(`pose_landmarks.json`), an OpenPose BODY_25 skeleton video and `openpose_keypoints.json`, a subject-masked camera
solve (`camera_motion.json`), depth (Depth-Anything-small, and an ffmpeg proxy), edges, line art, a motion mask, a
contact sheet, a shot bible and a prompt pack. The tab shows the reference, skeleton, depth and edges side by side
and says the camera move in a sentence ("The camera pushes in while it slowly tilts up ..."). Stages report their own
progress and Cancel stops the job and keeps nothing. A pack ZIP exported by Motion Previs Studio can be uploaded in
place of an analysis.

**The analysis is a Python port, modified, of [Motion Previs Studio](https://github.com/wassermanproductions/motion-previs-studio)
by Sam Wasserman** (Apache-2.0, Copyright 2026 Sam Wasserman). Packs use his file names and manifest layout, so they
are interchangeable with his app's. His NOTICE is at `LICENSES/Motion-Previs-Studio-NOTICE`; what was changed and the
notices for MediaPipe, Depth Anything, OpenCV and ONNX Runtime are in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

```bash
bash oneshot-setup.sh --motion      # or: bash scripts/setup-motion.sh   (0.5 GB, an environment of its own)
```

`--motion` is an option, not part of the default install: MediaPipe brings its own OpenCV build, which must not replace
the Studio's, so the analysis has its own environment (`$PREFIX/motion-venv`, 433 MB, and `$PREFIX/motion-models`,
70 MB). Without it the tab still trims the shot and makes the ffmpeg layers, and on a Mac the camera solve; pose and
AI depth say that they are not available on this machine. Measured on a Mac Studio M5 Ultra, a 5 second 864x480 shot
with every layer at 12 samples a second: 6.6 s in all (prepare 0.2, pose 1.3, camera 0.1, depth 3.4, encode 0.9,
pack 0.1), 0.5 GB of memory at its peak, on the processor. On a DGX Spark (`bash oneshot-setup-spark.sh --motion`;
MediaPipe 1.1.0, OpenCV 5.0.0 and ONNX Runtime 1.31.0 all installed from their Linux aarch64 wheels) the same kind of
shot with every layer took 12.6 to 17.6 s (prepare 0.4, pose 3.3, camera 0.2, depth 5.8, encode 1.9, pack 0.4 in the
quicker run), 0.4 to 0.5 GB at its peak, with the same camera sentence the Mac gave for that clip. On the Spark the
tab was driven through its API and a screenshot of it from a headless Firefox was looked at; it has not been used by
hand in a browser on either machine. `?motionref=1` in the app's address opens the create wizard on the stored motion
reference.

**Use as motion reference** makes a hand-off (one layer of the pack resampled to the length and size of the clip to
be made, the camera move in words, and the reference mode: `motion_reference()` in `app/motion.py`) and opens the
create wizard on its references screen with that layer as `<Video 1>` of a MiniMax H3 Ref2VA clip.

### Motion as a reference in the create wizard

*The clip starts from → References*, add a reference video, then **Analyse motion** under it (offered when the
motion environment is installed; without it the video is a plain reference). The analysis runs on the processor in
well under a minute and gives two choices:

- **What to carry over:** *Full scene* (the same place and motion with a new person: its person is replaced by
  `<Picture 1>`), *Actor motion* (the body motion and the camera, in a new place), *Object motion* (an object's path
  and the camera) or *Camera only*.
- **What goes to the model as `<Video 1>`:** the **source clip** (the default for *Full scene*), the **depth** layer
  as a control video (the default for the other three), or the **skeleton** (offered, never the default) or edges.
- **People in `<Video 1>`:** *One* or *Two or more*. With two or more the prompt speaks of its people and figures,
  one for one. You say it: the pose pass, its skeleton layer and the distance figures below follow **one person
  only** (asked for up to four people on a two-person duel at arm's length, it returned one in every frame).

**Fast motion, on the Mac** (through the app, 5 s at 864x480, seed 7; two cuts of a fictional martial-arts short: one
man drilling a wooden dummy while the camera pushes in to a close-up, and two fighters trading hands while the camera
cranes up to overhead; pose distance as in the table below, median / 90th percentile):

| shot | `<Video 1>` | passes | time | distance | what came out |
|---|---|---|---|---|---|
| dummy drill | source clip + a picture of a woman | Turbo 4, as first shipped | 236 s | 26.4 / 36.6 | the place kept; she drifts out of frame, strikes lost |
| dummy drill | the same request | Turbo 4, without the int8 kernels | 231 s | 3.7 / 11.2 | held |
| dummy drill | the same request | 20 | 741 s | 4.2 / 11.7 | held: she does the drill and the push-in on the same rooftop |
| dummy drill | depth + a picture of her in a bamboo clearing | Turbo 4 | 235 s | 3.5 / 11.7 | held, in the new place |
| dummy drill | depth + a picture of her on the same rooftop | Turbo 4 | 247 s | 16.0 / 25.3 | the drill and the push-in, with the layout shifted |
| duel | depth + a picture of the pair in a temple courtyard | Turbo 4 | 234 s | not valid | held by eye: both new fighters in time, the crane-up followed |
| duel | source clip + a picture of a new pair, *One* person | Turbo 4, without the int8 kernels | 294 s | 3.2 / 8.3 | motion and crane-up followed; only one fighter replaced |
| duel | the same with *Two or more* people | Turbo 4, without the int8 kernels | 284 s | 3.1 / 8.3 | both fighters replaced, motion and crane-up followed |

The first row was a defect of the Mac build, not of the source-clip route (the DGX Spark held the same request at
2.9 / 7.7): the Ref2VA Turbo adapter, a reference video and the three int8 kernels together lose the motion. Any one
kernel alone held (3.4 to 3.5), any two did not (14 to 23), and 20 passes without the adapter held with all three.
`scripts/video.sh` now runs that one case in bfloat16, about 13 s more a pass at 864x480 (`REF_INT8=1` puts the
kernels back). The depth duel's figures are not valid because the checker follows one person and picked different fighters in
source and result; the source-clip duel's are for the one fighter it followed. The adapter at 3 passes held the
dummy drill as well (3.2 / 10.5, 257 s). One seed each.

The camera move in words goes into the prompt in every case (a switch; text of your own in the Camera field wins).
With the source clip and a picture, step 4 writes "The target video is `<Video 1>` with its person replaced by
`<Subject 1>`"; calling the picture a first-frame anchor there made the picture's layout override the video. For a
control layer it writes the control-video form: `<Video 1>` is declared the structural template (layout, silhouettes,
positions, movement and camera path frame by frame), `<Picture 1>` the first-frame anchor, with a legend of the layer
(a skeleton's coloured lines are joints and limbs on black; in a depth map brighter is nearer), "These colours are
labels only, not the look of the target video", and the closing "no skeleton lines, no flat-coloured, depth-map or
segmentation-map look". Give a picture of the opening frame as `<Picture 1>` with a control layer: the look and the
setting come from it.

Where the defaults come from: one shot measured on the Mac (a martial-arts sequence with a kick and a punch toward
the camera, fictional people, 5 s at 864x480; the distance of the body's keypoints from the source's, in percent of
the frame width, lower is closer):

| `<Video 1>` | Distance | What it kept |
|---|---|---|
| Source clip, with a picture of another person | 0.6 to 2.2 | the shot move for move, in its own place, with the new person |
| Depth, with a first-frame picture | 1.3 to 2.9 | the kick and the punch toward the camera; the setting is the picture's |
| Depth, with a picture of another setting | 2.7 to 5.5 | the new place with no leak of the source's; the punch, not the kick |
| Skeleton, with a first-frame picture | 2.0 to 4.5 | the scale and the camera; the kick was lost |
| The picture alone | 5.0 to 9.8 | |

No control layer left a trace of its look in a clip. One shot: read the table as an order, not as figures to promise.
On the DGX Spark the same routes were rendered through the wizard's own API calls with the Turbo adapter and looked
at, not measured: see [the Spark's table](cli.md#reference-videos-audio-and-motion-on-a-dgx-spark).

### Sound and movement of a motion reference: Impacts or Flow

A clip's sound and its movement have to agree. The first duel renders above did not: the reference is two people
doing slow sticky hands (forearms in contact, circling), the soundscape asked for "quick slaps of forearms and hands",
and the model did both: it followed the slow hands and laid 1.7 to 1.9 slaps a second over them, on the Mac and on
the DGX Spark alike. Three things were measured before anything was changed:

- **The slaps came from the Soundscape field**, not from the source clip (its own sound is at -54 dBFS and was not
  sent).
- **The slow hands came from the reference.** The source clip's quickest movement is 14 percent of the frame width a
  second; the renders made from it are at 14 to 17. A reference video is followed whatever the prompt says about
  speed: four wordings were tried on the Spark (fast words in the action, a template that says the slow hands are
  not followed, *Camera only*, and the clip played at twice its speed and back), and the arms stayed at 15 to 21.
- **The music of the Mac's fourth test** was the model's own: nothing in that prompt asked for music.

**Sound and movement**, under *Analyse motion* on the references screen, is the choice that follows from this:

| | What the prompt says | What is sent | Sound |
|---|---|---|---|
| **Impacts** | both fight at full speed: sharp strikes and hard blocks, attacking and blocking, every strike heard as it lands | a reference that moves fast goes in as before. One that moves slowly is **left out** (a switch, on by default, when there is a picture of who is in the clip): the picture and the prompt carry the fight, and the camera move of the video stays, in words | the hits of the soundscape |
| **Flow** | the movement is slow, smooth and continuous, and no strike lands | the reference, as before | the hits are taken out of the soundscape and a video's own hits are not reused. Without music the clip is **delivered silent** (its track is muted after the render); with music named in step 3, under that music |
| **Automatic** (the default) | as you wrote it, except that a soundscape that names hits over a reference that moves slowly becomes Flow | the reference | as Flow in that one case |

The analysis measures the reference for this (`app/pace.py`, with ffmpeg and NumPy alone): **movement** is how fast
the quickest parts of the picture move against the rest of it, in percent of the frame width a second (frames lined
up on the camera's shift, each pixel's change over its edge strength, the three quickest blocks less the median
block, 90th percentile over the clip), read also against the size of the action in the frame, so that a fight seen
from across a roof is not called slow for being small; **hits** are sharp onsets a second that fill the band above
600 Hz and die away within 50 ms, which a plucked note and a spoken consonant do not. The same two figures are taken
of every finished reference clip and shown under it ("Sound and movement: agree", or "impact sound over slow motion"
in red): a flag beside the clip, like the QA gate's lines, never a failed job. `pace`, `silent` and
`motion.measured` / `motion.free` are the job's settings for scripts that drive `/api/jobs`.

Measured on the duel, through the wizard's own API calls, Turbo 4 passes, 5 s at 864x480, seed 7 (movement in percent
of the frame width a second, then the same against the size of the action; fast is 24 or 44):

| machine | Sound and movement | time | movement | hits a second | checker | by eye |
|---|---|---|---|---|---|---|
| Mac | before: source clip, slaps in the soundscape | 284 s | 14.2 / 34.8 slow | 1.7 | impact sound over slow motion | slow sticky hands under slaps |
| Mac | Impacts (video left out) | 89 s | 25.0 / 62.4 fast | 2.5 | agree | both throw straight punches and block, in time with the slaps; a new rooftop from the Scene text, the push-in and tilt followed |
| Mac | Flow, no music | 262 s | 14.5 / 35.5 slow | 0 | agree | the slow hands of the reference, silent |
| Mac | Flow, with music | 272 s | 15.6 / 40.4 slow | 0 | agree | the slow hands under a slow plucked melody |
| DGX Spark | before: source clip, slaps in the soundscape | 216 s | 16.8 / 40.6 slow | 1.9 | impact sound over slow motion | slow sticky hands under slaps |
| DGX Spark | Impacts (video left out) | 101 s | 17.0 / 59.0 fast | 2.5 | agree | both strike and block at speed, seen from further off than on the Mac; a new rooftop, the push-in followed |
| DGX Spark | Flow, no music | 206 s | 14.9 / 37.8 slow | 0 | agree | the slow hands of the reference, silent |
| DGX Spark | Flow, with music | 207 s | 15.3 / 37.6 slow | 0 | agree | the slow hands under a slow plucked melody |

What this does not do. **Impacts cannot make a slow reference fast**: with the video left out the clip keeps who is
in it (from the picture) and the camera move (in words), and loses the reference's own place and blocking, which
then come from the Scene text. A picture of the reference's first frame as a second picture was tried for the
place and brought the reference's people back with it after the first second, so it is not used. Before the track
is muted, a Flow clip asked for near silence was near silent on the Spark (-45 dBFS) and under music of the model's
own on the Mac in two of three renders, which is why the track is muted and not left to the prompt. The thresholds
were set on thirteen clips of two shots; they sort the clear cases and call the rest "moderate", which is not
flagged. One seed each.

**The mouth of a reference video.** A picture with a source clip of someone talking, in a clip where nobody speaks:
the new face mouths the reference's words (a woman asked only to smile opens and closes her mouth through the last
second, on both machines). The model copies the lip movement whatever is asked: the same clip came out with a
prompt that says her lips stay together and that the mouth of `<Video 1>` is not followed, and at 20 passes without
the Turbo adapter (761 s on the Spark). What removes it is taking the mouth out of the reference: **Keep the mouth
of `<Video 1>` out of the clip** (on by default when the source clip itself goes in, the clip has no spoken lines and
does not reuse the video's sound) smooths an ellipse over the mouth and jaw of the pose track's person before the
video goes to the model (`soft_mouth()` in `app/motion.py`, ffmpeg and NumPy; `mouth: "soft"` on
`/api/motion/reference`). With it she holds the smile to the end, on the Spark (202 s) and on the Mac (275 s); body,
head and camera follow the reference as before. It needs *Analyse motion* (the pose track) and follows one person.

**The woman who left the frame** (the first dummy-drill row above) was checked again by eye on the Mac through the
app's own job path, the way the wizard runs it, at this release's code: 259 s, she stays in frame for the whole clip
and strikes the dummy as the reference does (movement 28.8 fast, 2.3 hits a second, "agree"). The app goes through
`scripts/video.sh`, so it picks up the fix (a reference video with the Turbo adapter runs without the int8
kernels) with nothing to set.
