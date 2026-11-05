# Command-line rendering recipes

[Back to the README](../README.md)

These are **Mac/Linux shell commands**, not Native Windows commands. Windows uses [PowerShell setup and the app](windows.md). Keep the documented model/frame/canvas constraints.

## One-shot

```bash
git clone https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman.git
cd Imagination-Studio-by-keys-Wasserman
brew install python@3.11 uv ffmpeg
bash oneshot-setup.sh               # both models and the 2x decoder: 182 GB of weights
bash oneshot-setup.sh --image-only  # or just Qwen-Image-2.1: 33 GB
```

`oneshot-setup.sh` does the following:

1. Gets TensorFold 0.6.5 with the H3 and Qwen-Image families (`drowzeys/TensorFold` at `a2b592fe`), from the **GHCR
   prebuilt carrier** when Docker is available (checksums verified), otherwise from git at the same commit.
   The native FastH3 engine (`tf-h3-dit`) comes from the same carrier, or without Docker as a checksummed download
   from the [v2.0 release](https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/releases/tag/v2.0); no Zig toolchain is needed.
2. Installs it with mflux at `add5164e` and the dependency lock ([`requirements.lock`](../requirements.lock): mlx 0.32.3,
   mlx-lm 0.32.0, mlx-vlm 0.7.4, …) into its own venv at `~/.local/opt/tensorfold-studio`.
3. Downloads `Qwen/Qwen-Image-2.1-Turbo` (33 GB) to `~/qwen-models/Qwen-Image-2.1-Turbo`. Where an earlier install's
   `Qwen-Image-2.1` sits beside it, only the transformer (14 GB) is fetched and the rest is linked.
4. Unless `--image-only`: clones minimax-h3-mlx at `79190205`, downloads the FastH3 transformer (65 GB), the MiniMax
   H3 `FL2VA` partition without its transformer (72 GB: the text encoder and the two decoders) to
   `~/h3-models/MiniMax-H3` and the 2x video decoder (5.2 GB, checksum verified). With `--turbo`, the whole
   partition (134 GB) and the video Turbo adapter (1.96 GB, checksum verified).
5. Renders a test: a 5 second clip from a generated image (`outputs/test.png`, `outputs/test.mp4`), or a test image
   with `--image-only`.

Override `PREFIX`, `QWEN_MODEL_DIR` or `H3_MODEL_DIR` through the environment. `--verify` checks an existing install;
`--no-render` skips the test.

`studio.sh` and `video.sh` use MiniMax H3 Turbo when it is installed (`--turbo`) and FastH3 otherwise; `ENGINE=fasth3`
or `ENGINE=h3` picks one. `QUALITY=high` and `QUALITY=full` need `--turbo`. For FastH3 the sizes measured here are 864x480,
1280x736, 1344x768, and 672x384 and 1024x576 with the 2x decoder (the app's Draft and 2K cards: 44 s and 93 s for 5 seconds).

### Text to image to video

```bash
bash scripts/studio.sh "A lighthouse keeper in a yellow raincoat on a cliff at dusk, storm clouds behind him" \
                       "He raises a lantern, wind pulls at his coat, waves crash below. He says: Storm's coming." \
                       out.mp4
```

The image is written beside the clip (`out.png`). Defaults: 1344x768, 124 frames (5 s), native decode. `QHD=1` makes
a 2560x1440 clip; `X2=1` generates the video at half of `WIDTH` x `HEIGHT` and decodes at 2x (multiples of 64, up to
2688x1536); `IMAGE_FILE=picture.png` animates an image you already have. Set `FRAMES=192` for 8 seconds, `WIDTH`/`HEIGHT` for another canvas (multiples of 32, at most 768x1344 pixels in total, the video model's
released limit), `SEED`, or `IMAGE_SEED` and `VIDEO_SEED` separately. The script opens the video prompt with the line
MiniMax's own image-to-video prompts use; `RAW_PROMPT=1` passes yours untouched.

### Image only

```bash
bash scripts/image.sh "A neon shop sign that reads OPEN LATE, rainy night, reflections on wet pavement" out.png
TURBO=0 bash scripts/image.sh "..." out.png                 # 40 steps, base model
WIDTH=2048 HEIGHT=2048 bash scripts/image.sh "..." out.png  # sizes in multiples of 16
IMAGE_MODEL=flux3k-fast bash scripts/image.sh "..." out.png # FLUX3-K Fast, 16 steps (oneshot-setup.sh --flux3)
IMAGE_MODEL=flux3k-full bash scripts/image.sh "..." out.png # FLUX3-K Full, 50 steps at CFG 3.5
IMAGE_MODEL=flux3k-fast UPSAMPLE=0 bash scripts/image.sh "a full scene caption ..." out.png   # no prompt upsampler
```

### Edit a picture

```bash
bash scripts/edit.sh photo.png "The same statue at night under a full moon" night.png
LORA=~/ImaginationStudio/loras/qwen-multiple-angles.safetensors:0.9 \
  bash scripts/edit.sh photo.png "<mva> right side view, high-angle shot" side.png
SET_FILE=edits.json bash scripts/edit.sh photo.png          # [["prompt", "out.png"], ...] from one load
BASE=1 bash scripts/edit.sh photo.png "..." out.png         # 40 steps, base model (--image-edit)
IMAGES="person.png" bash scripts/edit.sh room.png "The person of image 2 sits on the sofa of image 1" out.png   # up to nine more, one a line
SIZE=model RESOLUTION=1024 bash scripts/edit.sh photo.png "..." out.png   # the model's own size; the default is the picture's own
IMAGE_MODEL=flux3k-fast IMAGES="person.png" bash scripts/edit.sh room.png "..." out.png   # FLUX3-K Fast (flux3k-full: Full), --flux3
```

The result has the size of the first picture (`WIDTH` and `HEIGHT` ask for another). The noise is drawn for `SEED`
and the pictures together, so the seed a picture was made with is as good as any other for editing it.

### Video only, or from your own image

```bash
bash scripts/video.sh "A hummingbird hovering over red flowers, soft wing hum" out.mp4
FIRST_FRAME=photo.jpg WIDTH=1344 HEIGHT=768 bash scripts/video.sh "She turns to the camera and smiles" out.mp4
QUALITY=high bash scripts/video.sh "..." out.mp4            # 20 steps, no adapter
```

`FRAMES` must be `17n + 5` (56, 73, 90, 124, 192, 243, 362). `FIRST_FRAME` is stretched onto the canvas, so match its
aspect ratio.

### A last frame: the clip ends on a picture

MiniMax H3's first-and-last-frame mode holds a picture at the end of the clip the way it holds one at the start: the
end keyframe is encoded as a condition latent at the clip's last latent frame and kept at every pass. `LAST_FRAME`
works with `FIRST_FRAME`, or alone (the clip then arrives at the picture). Beside a first frame the last one is fitted
by a centre crop, so give both the clip's aspect ratio. A keyframe is read by the model, not written into the clip:
in one production run 63 of 64 were held and one first frame was not, and FastH3 cut to a close-up inside 9 of 32
such clips. The app checks both after the render (see [the shot check](user-guide.md#what-the-app-tells-you-about-a-prompt-and-a-clip)).

```bash
FIRST_FRAME=teapot.png LAST_FRAME=bonsai.png bash scripts/video.sh "The teapot opens and a bonsai grows out of it" out.mp4
LAST_FRAME=bonsai.png bash scripts/video.sh "A potter's table; a bonsai is set down" out.mp4      # arrives at the picture
FIRST_FRAME=statue.png LAST_FRAME=statue.png WIDTH=768 HEIGHT=768 FRAMES=73 LORA="orbit.safetensors:1.0" \
  bash scripts/video.sh "One frozen instant. Only the camera moves. In a continuous 360 orbit. ..." out.mp4
```

In the app, step 2 of **Create** has a **Last frame** slot under the first frame. Ways to fill it: upload a picture;
*Pick from the Library*; *Same as the first frame* (an orbit or a loop); *From a clip in the Library* (its last
frame, its first frame, or the frame at a time you type: `POST /api/frame` saves the still to the Library at the
clip's own resolution); or *make one from a prompt* with Qwen-Image-2.1. Beside *Upload an image*, *Or continue from a
clip in the Library* puts a clip's last frame in as the new first frame; in the **Library**, *continue* on a video
and *last frame* on a picture do the same in one press. Step 4 opens the prompt with a line that places `<Picture 1>`
at 0.00 seconds and `<Picture 2>` at the clip's end (MiniMax documents that line for a first frame only; the two-picture
form here follows its wording and is our reading, and the keyframes are held whatever the line says). A LoRA from the
hub that needs both ends asks for the last frame in step 2, and *Queue this clip* waits for it. A job takes
`last_image` (a picture in the studio folder), `last_same` (the first frame again) or `last_frame_from` (a clip: its
first frame). Not for reference clips (a reference is not a frame of the clip) and not for videos longer than 15 s.

![The Last frame slot in step 2](../samples/ui_last_frame.png)

Measured 2026-10-12, one seed each, 5 s at 864x480 unless noted; SSIM is the clip's final frame against the picture
it was given (0.3 to 0.5 is what an unrelated frame of the same scene scores):

| Machine | Engine | Request | Time | Final frame vs the picture | By eye |
|---|---|---|---|---|---|
| Mac | MiniMax H3, Turbo 5 passes | first + last | 105 s | 0.86 | the teapot becomes the bonsai and the clip ends on the picture |
| Mac | the same | first only (same seed) | 101 s | 0.46 | a tree grows, in another pot |
| Mac | the same | last only | 105 s | 0.86 | another teapot, arriving at the picture |
| Mac | FastH3 8 passes (MLX engine) | first + last | 103 s | 0.86 | as the first row |
| DGX Spark | FastH3 8 passes, TensorFold int8 blocks | first + last | 130 s | 0.90 | ends on the picture |
| DGX Spark | the same | first only (same seed) | 86 s | 0.32 | a tree grows, in the teapot |
| DGX Spark | the same | last only | 85 s | 0.90 | arrives at the picture |
| DGX Spark | the same, queued through the app's job API (`image` + `last_image`, prompt from `/api/compose`) | first + last | 126 s | 0.90 | ends on the picture |

![First and last frame, first only, last only (DGX Spark), last only and FastH3 first and last (Mac)](../samples/last_frame_plain.jpg)

Two LoRAs from the hub that need both ends, same seed with and without, the authors' prompts and strength 1.0:

| LoRA | Machine | Engine | Time | With the LoRA | Without |
|---|---|---|---|---|---|
| 360° Orbit (first = last, 768x768, 73 frames) | Mac | MiniMax H3, Turbo 5 passes | 86 s without; with it the wall time was not clean (the run queued behind other renders) | a full orbit of the statue, back to the start | frozen |
| the same | DGX Spark | MiniMax H3 bf16, 25 passes (`SPARK_BASE=h3`) | 324 s | a full orbit | frozen |
| the same | DGX Spark | FastH3 bf16, 8 passes | 147 s | a full orbit | not run |
| Transformation (teapot to bonsai) | Mac | MiniMax H3, Turbo 5 passes | 108 s | the teapot dissolves into painted ribbons that form the tree | a cross-fade through a plant pot |
| the same | DGX Spark | MiniMax H3 bf16, 25 passes | 362 s | the same ribbons | a cut through another object |
| the same | DGX Spark | FastH3 bf16, 8 passes | 158 s | the same ribbons | not run |

![360° Orbit: Spark MiniMax H3 with and without the LoRA, Spark FastH3 with it, Mac with and without](../samples/last_frame_orbit.jpg)
![Transformation: the same five rows](../samples/last_frame_transformation.jpg)

On a Mac the zig engine does not hold an end keyframe, so a FastH3 clip with a last frame runs on the MLX engine
(the script says so). On a DGX Spark the last frame works on both block engines. Both LoRAs were trained on MiniMax
H3's own first-and-last-frame transformer, which a Spark install does not have by default (it renders on FastH3, where
both worked here): `bash oneshot-setup-spark.sh --h3-base` fetches it (40 GB) and `SPARK_BASE=h3` renders a clip on
it, 25 passes by default, on ComfyUI's own blocks with dense attention. Not tested: lengths other than 3 and 5 s,
sizes above 864x480, a last frame with the Qwen scout route in one job, and *make one from a prompt* in a browser.

### Another text encoder, your own size and passes

From the scripts, `TEXT_ENCODER` may be the path of another MiniMax H3 text encoder file (on a DGX Spark, the name
of a file in ComfyUI's `text_encoders` folder); unset, it is MiniMax's own. On the Mac FastH3 then runs on the MLX
engine, not the native one.
In the app, *Text encoder* is *Regular* (MiniMax H3's own), any file found in the `text_encoders` folder beside the
model (by its file name), or *Your own*: the path of a MiniMax H3 text encoder file on your machine (on a DGX Spark,
the name of a file in ComfyUI's `text_encoders` folder). The Studio ships MiniMax H3's own text encoder and no other;
it does not provide, check or recommend the file you choose. That file, its license and what you make with it are
your own choice and responsibility.

In the app, *Resolution → Custom* takes a width and a height (multiples of 32, each side 256 to 1536, at most
1344x768 pixels in all). A number of passes of your own, from 1 to 50, is the *Custom* card beside *20 passes* in each
passes choice (FastH3's *Passes*, the reference choice, and MiniMax H3's *Quality*, where it follows the two
20-pass recipes and keeps the one that was chosen); a number box opens under the cards. No time is estimated for either. From
the scripts these are `WIDTH`, `HEIGHT` and `STEPS` (FastH3, and everything on a Spark) or `POINTS` (MiniMax H3 on
the Mac: one more than the passes).

### Reference pictures to video (Ref2VA)

A first frame is the clip's opening picture. A reference picture is not in the clip at all: it fixes how a person or
a thing looks, and the prompt puts them in a new scene. This is MiniMax H3's second transformer, Ref2VA, installed
with `bash oneshot-setup.sh --ref2va` (62 GB; the text encoder and the two decoders are the ones already installed,
linked into `Ref2VA/`).

```bash
REF_IMAGES="face.jpg" bash scripts/video.sh \
  "Use <Picture 1> as the person. The person walks through a rainy neon street, turns to the camera and says hello." out.mp4
REF_IMAGES="face.jpg bike.png" bash scripts/video.sh "Use <Picture 1> as the person and <Picture 2> as the bicycle. ..." out.mp4
REF_TURBO=1 REF_IMAGES="face.jpg" bash scripts/video.sh "Use <Picture 1> as the person. ..." out.mp4   # 4 passes
```

- Up to nine pictures, named `<Picture 1>`, `<Picture 2>`, ... in the order given. Each is used at the clip's pixel
  area with its own aspect ratio (a 768x1024 portrait becomes 544x736 for an 864x480 clip), so nothing is stretched.
- 20 plain passes with the model's own sound by default: 3 min 43 s for 5 s at 864x480 on an M5 Ultra (9.7 s a pass
  over 16.2K rows, 64 GiB peak). `REF_TURBO=1` uses LightX2V's 4-step Ref2VA adapter and then makes the sound again
  without it: 1 min 28 s, 103 GiB peak while the adapter is merged. `QUALITY=high` runs the 20 passes with the fast
  recipe: 1 min 58 s. Two pictures (a person and a bicycle) took 4 min 5 s at 20 passes and 1 min 29 s with the adapter.
- Not with `FIRST_FRAME`, and not with FastH3, which has no reference-trained weights.
- **Your own number of passes:** `STEPS=12` (or `POINTS=13`, one more, which wins when both are set), and the
  *Custom* card of the passes choice in the app. It applies to every kind of reference, and with `REF_TURBO=1` too: the adapter then runs the
  number asked for instead of the 4 it was trained for, and the render says so. A reference render starts by naming
  the passes it will run, and the app's job card shows them. Before this, `STEPS` was read for FastH3 only and a
  reference render from the scripts ignored it; the app already converted its field.
- In the app: **The clip starts from → Reference pictures**. Step 4 writes the prompt in the model's own reference
  form (`subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, and from 2.6 the sound under
  its own `overall_soundscape` heading); edit it freely.
- **On a DGX Spark** the same `REF_IMAGES` and `REF_TURBO=1` work after `bash oneshot-setup-spark.sh --ref2va` (39 GB:
  the Ref2VA transformer for ComfyUI in bf16 and LightX2V's adapter). Measured on one Spark, 5 s at 864x480, one
  portrait: 20 steps 288 s, `REF_TURBO=1` (4 passes, the adapter's own sound) 107 s; the app's *Reference pictures*
  start works there too (101 s). The passes run on ComfyUI's own blocks, not on TensorFold's CUDA engine. Free memory
  fell to 7 GB of 128 during these renders, with a browser and the app open: close what you can first. One picture
  was tested there, not several.
- Checked on one fictional portrait: the same face, hair and glasses in the clip, against a different person from the
  same prompt and seed without the reference, and the same identity as h3.c's `--ref-image` gives. The token layout
  matches h3.c's to the last float32 bit. Not checked: sound by ear, lengths over 5 s. Reference videos and
  reference audio are the next section.
- **What the text encoder sees of a picture (corrected after 2.5).** A first frame or a reference picture also goes
  to the text encoder's vision tower. Up to 2.5 it was normalised there with the mean and deviation of the earlier
  Qwen-VL models, which minimax-h3-mlx builds its image processor with; MiniMax H3's own processor
  (`processor/preprocessor_config.json` in the checkpoint) uses 0.5 and 0.5, pixels to -1..1. The renderer now reads
  that file (0.5 and 0.5 when it is absent). For one 864x480 picture the values handed to the tower were up to 1.12
  away from MiniMax's processor before (0.39 on average, range -1.79..2.09) and are equal to it now, and to h3.c's
  `x * 2 - 1`. The video VAE's view of the picture, which carries most of the likeness, was never affected. Same
  prompt and seed, before and after, one first-frame clip (Turbo, 5 passes) and one reference-picture clip (20
  passes), 5 s at 864x480: a different take each time, as any change to the conditioning gives, with the same
  person, colours and sharpness; we could not see a gain or a loss. `EXTRA="--vision-stats mlx"` renders with the
  old values. This covers MiniMax H3 and FastH3 on the Mac, both engines; the DGX Spark build encodes text in
  ComfyUI, which already used 0.5 and 0.5. The sample clips and timings in this README from first frames and
  reference pictures were made with the old values: the timings stand, the clips would come out as other takes.

### Reference videos and reference audio (Ref2VA)

The same Ref2VA transformer takes two more kinds of reference, as MiniMax's model card describes: videos, which
carry a subject and how it moves, and audio clips, which carry a voice or music. **On a Mac** the passes need TensorFold's H3 family to pack these references and to hold reference audio rows, which is on
the engine branch `ref-video-audio` of the fork this pack installs from (pinned to `a2b592f` since 2.6.1). An install
made with 2.6 or earlier has the older engine, with which the renderer stops with a message saying so: run
`oneshot-setup.sh` again and it replaces the engine. Reference pictures alone are unaffected. **On a DGX Spark** they run now,
through ComfyUI's node: see [below](#reference-videos-audio-and-motion-on-a-dgx-spark).

```bash
REF_VIDEOS="baker.mp4" bash scripts/video.sh \
  "<Subject 1> is the man in the green apron in <Video 1>. ... the bakery is replaced by a market stall ..." out.mp4
REF_IMAGES="singer.png" REF_VIDEOS="baker.mp4" bash scripts/video.sh \
  "<Subject 1> is the woman shown in <Picture 1>. <Video 1> is the motion reference. ..." out.mp4
REF_IMAGES="singer.png" REF_AUDIOS="voice.wav" bash scripts/video.sh \
  "<Subject 1> is the woman shown in <Picture 1>. <Audio 1> is the voice timbre reference for her voice. ..." out.mp4
REF_VIDEOS="baker.mp4" REF_VIDEO_SOUND=1 bash scripts/video.sh "..." out.mp4   # the video's own sound is <Audio 1>
```

- **Limits (MiniMax's):** up to 9 pictures, 3 videos and 3 audio clips, 12 files in all; each video and each audio
  clip 2 to 15 seconds, and 15 seconds in all per kind; audio never alone (it needs a picture or a video beside it).
  The scripts and the app refuse a request outside these and say which file or count is wrong.
- **Names in the prompt:** `<Picture N>`, `<Video N>`, `<Audio N>`, each numbered in the order given. The model is
  always handed pictures first, then videos, then audio clips. With `REF_VIDEO_SOUND=1` every video's own soundtrack
  is a reference as well and those sounds are numbered first: with one video, its sound is `<Audio 1>` and the first
  `REF_AUDIOS` clip is `<Audio 2>`. Without it a video's sound is ignored. The render prints the name of every file.
- **What is read:** a video at 24 fps up to the clip's own length, cut down to `17 n + 5` frames, at its own aspect
  ratio and brought down to the clip's pixel area (`REF_VIDEO_SIZE=full` keeps the model's 768-pixel canvas; not
  tested). Audio at 32 kHz stereo, cut to the clip's length. `ffmpeg` and `ffprobe` do the reading.
- **Cost:** every reference latent rides through every pass. Measured on an M5 Ultra, 5 s at 864x480, 20 passes:
  one 5 s reference video 12 min 32 s (34.9 s a pass over 33.2K rows), a picture and a 5 s video 13 min 36 s, a
  picture and a 5 s audio clip 4 min 10 s (11.3 s a pass over 17.0K rows), against 4 min 6 s with that picture alone;
  64 GiB peak in each. `REF_TURBO=1` with a picture and an audio clip: 1 min 35 s, 103 GiB peak. A shorter reference
  video costs less; none was timed.
- **In the app:** *The clip starts from → References*; step 2 takes pictures, videos and audio, shows each one's name,
  and step 4 writes the prompt in the model's own form (`<Video 1> is the reference video for ...`,
  `<Audio 1> is the voice timbre reference for ...`). No time is estimated when a video or an audio clip is used.
- **On a DGX Spark** the same variables, limits and messages: see the next section.
- **How it was checked.** The token layout (row order, positions, which rows are held) is equal to the last float32
  bit to MiniMax's own `build_ref2va_packed_sequence` and to h3.c's `h3_layout_build`, on six mixed requests. The
  prompt's token sequence equals MiniMax's `build_ref2va_presentation`; the frame sampling for the text encoder,
  the frames kept for the video VAE and the frame size rule equal MiniMax's functions. Reference rows decode back to
  what went in (video 38.7 dB PSNR, audio loudness envelope correlation 0.999). Two differences from MiniMax's
  pipeline remain, both shared with the picture path: the video latent is the middle of the encoder's distribution,
  not a draw from it, and a reference is not enlarged to the 768-pixel canvas.
- **What the renders showed** (fictional people from earlier renders of this pack; 5 s, 864x480, 20 passes, one seed):
  - *A reference video* (a baker lifting a loaf in a bakery), with a prompt that moves him to a market stall: the
    same man, clothes and motion, second for second, in front of a market. The same prompt and seed without the
    video gave another man holding the loaf up throughout.
  - *A picture and a video* (a woman's still, the baker clip as motion reference): the woman, in the baker's
    bakery, doing his motion second for second. Her mouth also moves as his does, though the prompt said nobody
    speaks.
  - *A video with its own sound* (`REF_VIDEO_SOUND=1`): the clip's sound follows the reference closely (pitch 120 Hz
    against 122, spectrogram correlation 0.86 moment by moment, against 0.43 for the run without the sound). With
    the app's prompt and a picture beside the video: 0.99 at 20 passes. **With `REF_TURBO=1` this was broken until
    the engine's re-voice was corrected** (branch `ref-video-audio`, `a2b592f`): the sound made again after the
    adapter's passes came out near silent, correlation 0.22, where the adapter's own sound had 0.98. The audio-only
    re-voice kept the held rows beside the clip's finished audio; with reference audio in the sequence they are now
    kept beside the starting noise: 0.98, same time. A voice clip as `<Audio 1>` through Turbo was not affected
    (139 Hz and distance 6.7 after, 140 Hz and 6.6 before; 193 Hz and 13.8 without the clip).
  - *A picture and an audio clip of the same kind of voice* (a woman's still, 6 s of a woman singing, the prompt
    asks her to sing): median pitch 281 Hz, the reference's own 281, against 314 without the clip; closer in
    timbre statistics as well (4.9 against 6.7).
  - *A picture and a voice that does not fit it* (the woman's still, a man's voice as `<Audio 1>`): **it carries
    when the prompt describes the voice, and not when the prompt is silent about it.** Measured against a clean
    5.8 s male voice at 117 Hz (the macOS speech voice "Daniel"; distance = MFCC statistics to the reference, lower
    is closer; 20 passes, one seed):

    | prompt | with the clip | same prompt without it |
    |---|---|---|
    | says only "the voice timbre references `<Audio 1>`" | 225 Hz, distance 14.1 (no carry-over) | 182 Hz, 11.7 |
    | also calls it a deep male voice (written by hand) | 137 Hz, 5.0 | 169 Hz, 11.6 |
    | the app's prompt with *Voice* = "a deep male voice" | 130 to 134 Hz, 4.1 to 4.8 | 141 Hz, 10.1 |
    | one plain sentence: "speaks with the voice in `<Audio 1>`, a deep male voice" | 132 Hz, 3.9 | not run |

    The other way round (a man's still, a woman's voice at 180 Hz, the prompt silent about the voice) it carried
    as well: 174 Hz and distance 4.3 with the clip, 143 Hz and 10.6 without. The first test of this pack found no
    carry-over because its prompt did not describe the voice and its reference was a noisy clip with a voice in
    a fifth of it; with the voice described, that same clip carries too (117 Hz against its own 122, distance 6.0
    against 15.9 without). So: **describe the voice in the line's *Voice* field as it sounds in the clip, and use a
    clean recording.** The pitch and the tone come close to the reference; none of this was judged by ear, and it
    is not an exact copy of a voice.
- **Not checked:** sound by ear (the figures above are measurements: pitch by autocorrelation, MFCC statistics,
  spectrogram correlation), more than one reference video or audio clip in a request, two seeds, lengths over
  5 s, references longer than the clip, `REF_VIDEO_SIZE=full`, `QUALITY=high` with a video, the app's screens by eye
  in a browser (its checks and the prompt it writes were exercised through its API, two clips were rendered
  through its queue, and the Create page's choosers and reference step were driven in a scripted DOM, not looked
  at).

### Reference videos, audio and motion on a DGX Spark

`REF_VIDEOS`, `REF_AUDIOS`, `REF_VIDEO_SOUND=1` and `STEPS` work on the Spark as on the Mac, in the
scripts and in the app, after `bash oneshot-setup-spark.sh --ref2va`: the same limits, checked before any model loads,
with the same messages. `spark/spark_generate.py` reads each reference with `ffmpeg` (a video at 24 fps, up to the
clip's own length, LANCZOS-scaled to its own aspect ratio and brought down to the clip's pixel area, stored without
loss; sound at 32 kHz stereo, cut to the clip's length) and hands it to ComfyUI's `MiniMaxH3ReferenceToVideo` node
(`ref_videos`, `ref_video_audios`, `ref_audios`), which numbers pictures, videos and audio clips as the Mac build
does. The passes run on ComfyUI's own blocks in bf16. Scaling the video before the node sees it matters: left alone,
the node takes a reference at up to 768 pixels on the short side whatever the clip being made, several times the
rows; for that reason `REF_VIDEO_SIZE=full` is refused on a Spark. The reference files are taken out of ComfyUI's
input folder again after the render. Nothing here needs the TensorFold engine change the Mac build is waiting for.

Measured on one DGX Spark (GB10, 121 GB usable), 5 s at 864x480, seed 7, fictional people from earlier renders, a
browser and the app open, each render under a watcher on free memory; "lowest free" is the lowest `MemAvailable`
seen during the render:

| Render | Passes | Time | Lowest free |
|---|---|---|---|
| One picture (as in 2.5, to see nothing changed) | Turbo 4 | 100 s | 39.8 GB |
| A picture and a 2.3 s reference video | Turbo 4 | 147 s | 39.1 GB |
| A picture and a 5 s reference video | Turbo 4 | 202 s | 38.3 GB |
| A picture and a 5 s audio clip | Turbo 4 | 98 s | 39.1 GB |
| Wizard through the API: a picture and the source clip, full scene | Turbo 4 | 208 s | 37.8 GB |
| Wizard through the API: a picture and the skeleton layer | Turbo 4 | 187 s | 37.8 GB |
| Wizard through the API: a picture and the depth layer, actor motion | Turbo 4 | 193 s | 15.4 GB (see below) |
| A 5 s reference video alone (the 8-bit transformer) | 20 | 677 s | 57.1 GB |

A 5 second reference video about doubles the time of a Turbo clip and costs under 2 GB more memory at this size; at
20 passes a pass over the clip's and the video's rows took 30.7 s (613 s of passes), the reference's encoding 46 s.
What the clips showed, besides the table: the 20-pass clip has the baker of the video, his clothes and his motion in
front of a market stall, as on the Mac. With the Turbo adapter: the woman of the picture doing the
baker's motion second for second in his bakery; the martial-arts shot repeated move for move with the woman of the
picture in it; the skeleton layer steering the scale and the camera with no skeleton look in the clip. The audio
clip (a man's spoken line beside a woman's picture) gave **no carry-over we could measure**, as on the Mac: her voice
at 192 Hz with the clip and 195 Hz without, the reference near 110 Hz.

The depth layer with a picture of the new person kept the stances and the punch toward the camera in a rooftop
drawn afresh from the prompt, with no depth-map look.

**Memory after a FastH3 clip.** The depth row, and a repeat of the source-clip row (210 s, 14.0 GB), ran right after
a FastH3 clip: the server then still holds what ComfyUI's unloading does not give back (TensorFold's engine keeps its
int8 weights), and the reference model loads on top. That is the 7 GB figure of 2.5 as well. A reference render
that follows a FastH3 clip now restarts ComfyUI first ("a reference render starts from a server with nothing
loaded"): the same picture and 5 s video then ran in 213 s, restart included, with 37.9 GB lowest free.

**What a sound reference does (measured on the Spark, 2.6).** Same picture, prompt and seed with and without the
reference, 5 s at 864x480, Turbo 4 passes unless said; pitch by autocorrelation, likeness as the correlation of the
two spectrograms moment by moment (two unrelated spoken clips measure about 0.3), words by a speech-to-text pass:

| Request | With the reference | Without it |
|---|---|---|
| A woman's picture, a recording of a woman's voice, the prompt's line is what the recording says | the recording itself: likeness 0.98, pitch 229 Hz (the recording's 229) | 188 Hz, likeness 0.28 |
| The same picture, a man's recording, the prompt's line is what he says | the recording itself: likeness 0.98, pitch 113 Hz (his 122) | not run |
| The same picture, the man's recording, another line | nothing carried: 188 Hz, likeness 0.11; the prompt's words | 188 Hz |
| The same picture, the man's recording, another line, and the voice described too ("in the deep male voice of `<Audio 1>`") | pitch 122 Hz (his 122), timbre distance 8.5; the prompt's words | 156 Hz, distance 13.4 (the description alone) |
| The same picture, the woman's recording, another line | pitch 229 Hz (the recording's), but the words came out garbled | 183 Hz, the words right |
| A man's picture, his own recording, another line | pitch 130 Hz (his 122); the prompt's words | 147 Hz |
| A video with its own sound (`REF_VIDEO_SOUND=1`) | likeness 0.81, loudness envelope 0.90, pitch 115 Hz (the video's 122); the prompt's words | likeness 0.34, envelope 0.48, 131 Hz |
| A picture, a video with its own sound, and a voice recording (`<Audio 2>`) | her voice at 216 Hz (the recording's 229), the prompt's words; neither sound reproduced | not run |

So a reference audio clip is a **performance to repeat** more than a voice to borrow: when the spoken line is what the
recording says, the clip plays the recording back with the mouth moving in time with it (mouth movement against
speech, from face landmarks: r 0.55 to 0.59, as for clips with the model's own voice), whoever is in the picture.
With other words, the voice comes across when the prompt describes it as well as naming the clip (the pitch went to
the recording's); named without a description, a voice unlike the pictured person changed nothing, and with the
Turbo adapter a voice of the same kind blurred the words. It is not voice cloning, and the app says so where the clip is added. A video's own sound carries
as on the Mac. *The videos' own sound* stays **off** by default in the app: the box swaps the sound written in step 3
for the video's own, which has to be a choice.

**A voice nobody asked for.** Up to this change the app wrote the soundscape on the end of the shot description of
a reference prompt. With some pictures the model then read it out: a clip of a woman drinking coffee, whose prompt
says that nobody speaks, had a voice talking nonsense in 41 % of its frames (63 % on a second seed, 49 % at 20
passes), not in time with her mouth. With the same words under an `overall_soundscape:` heading, as text-to-video
prompts have always had, the voice is gone (0 % on both seeds, the room tone at -40 dBFS), and a spoken line is still
spoken as written. The app now writes the heading. Guidance was tried as well (ComfyUI's CFG at 5 with an empty
negative): it also removes the voice but burns the picture with the 4-step adapter and doubles the time, and at 2 it
does nothing; the reference model is run without guidance, as MiniMax ships it.

**The sound of every clip on a Spark** is now made from a lossless copy of the decoder's output (it used to be
ComfyUI's own AAC track at about 130 kb/s): 256 kb/s, cut to the picture's length, and limited to -1 dBFS when the
decoded peak is higher (it usually is: speech comes out of the decoder touching full scale, and the lossy track
then overshot it, 31 samples over full scale in one test clip). The render says what it found
(`[tensorfold] sound: peak -0.5 dBFS as decoded, limited to -1 dBFS`); `AUDIO_EQ=off` keeps ComfyUI's track. Every
clip of this section was checked for sample rate (32 kHz stereo), length against the picture (within one frame),
start offset (0), silence, channel balance (within 0.6 dB), clicks at the ends, and full-scale samples (none after
the change).

More of the 2.6 surface, on the same machine (Turbo 4 passes, 864x480):

| Render | Time | Lowest free |
|---|---|---|
| Two 5 s reference videos (a man from one, a street and its camera from the other) | 373 s | 33.8 GB |
| An 8 s clip from a picture and an 8 s source shot, full scene, through the app | 345 s | 30.3 GB |
| A picture, a 5 s video with its own sound and an audio clip | 229 s | 33.2 GB |
| The 8 s clip again with the shot's own sound as a reference: the clip's sound is the shot's, likeness 0.96 (0.02 without) | 344 s | 33.4 GB |
| Object motion: the depth layer of a ball rolling over a table, no picture, a prompt for an apple on marble | 208 s | 38.1 GB |
| Camera only: the depth layer of a street shot, no picture, a prompt for an empty canyon | 198 s | 36.9 GB |

The object-motion clip has the apple on the ball's path over a table of the same shape in a new kitchen; the
camera-only clip has a canyon with the street's layout and no person, with a boulder where the figure stood (a depth
layer shows the figure, so "camera only" keeps its place filled). The same request with the 4-step adapter at 4, 3
and 2 passes (*Custom*; a picture and a 5 s reference video) took 219, 191 and 156 s; the three clips are the same
shot to the eye, equally sharp (median Laplacian variance 70, 73, 84) and equally close to the reference's motion
(pose distance 1.1 % of the frame width in each), so 2 or 3 passes are worth trying for drafts; one request, one seed.

**The shared motion test** (two fast martial-arts shots, each as *Full scene* with the source clip and as *Actor
motion* with the depth layer and a new setting; Turbo 4 passes, seed 7, the wizard's own prompts, the same on both
builds): on the Spark 202 to 216 s a clip and 29.9 GB lowest free. Pose distance from the source, as a share of the
frame width: 2.9 % median (7.7 % at the 90th percentile), 3.2 % (8.1 %) and 3.2 % (7.6 %) for the first three; the
fourth, two fighters in a new courtyard, follows by eye, and the checker, which tracks one person, picked the other
fighter (its 28 % is not a measure of that clip).

Not tested on the Spark: larger sizes than 864x480 with a reference video, clips over 8 s with one, three reference
videos at once, and sound by ear (the figures above are measurements).

### GHCR prebuilt carrier

```bash
docker pull ghcr.io/drowzeys/keys-tensorfold-studio:3.3.0
docker run --rm -v "$PWD":/out ghcr.io/drowzeys/keys-tensorfold-studio:3.3.0 cp -a /payload/. /out/payload/
```

One image for both builds. For a Mac it holds the TensorFold wheel, `requirements.lock`, the render scripts, the
prebuilt native FastH3 engine (`zig-engine/tf-h3-dit`, built for Apple Silicon at the pinned commit) and `SHA256SUMS`.
For a DGX Spark it holds the prebuilt CUDA engine (`spark-engine/libtf_h3.so`, GB10, aarch64; since 2.8.0 the
`h3-cuda-lora` build at `1f7ecc75`, with LoRA support, so a Spark install no longer builds it from source), which
`spark/build-engine.sh` takes when Docker is there and otherwise builds from source; since 2.9.1 also MiniMax Music 3's
`spark-music3/music3-generate` (`music3-cuda` at 6b714531; from 3.3.0 `music3-cuda-115` at 5505dbfa, on TensorFold 1.1.5), which `spark/build-music3.sh` takes the same way, and
from 3.1.0 Fish Audio Drama 3's `spark-drama3/` (`drama3-generate` and `libtensorfold_drama3.so`, `drama3-cuda` at
81173e6c, the engine only: no codec, no weights), which `spark/build-drama3.sh` takes the same way. The Mac's
Drama 3 and Music engines are not in it: their installers clone TensorFold at the pinned commits. **It is not a runtime**: neither
Metal nor the Spark's models run in a container, so the setup installs natively. Rebuild it with
`PUSH=1 bash scripts/build-carrier.sh` (on a Spark, after `spark/build-engine.sh`, `spark/build-music3.sh` and `spark/build-drama3.sh`, to include its engines). Up to 2.2
the image was `ghcr.io/drowzeys/keys-mac-tensorfold-studio`; the Mac setup still falls back to it.
