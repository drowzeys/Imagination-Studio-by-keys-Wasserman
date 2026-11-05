# Voice, music, sound and the local brain

[Back to the README](../README.md)

Detailed engine and feature reference. These Mac/Linux recipes do not bypass [native Windows setup and qualification](windows.md#qualification); use the Windows app's PowerShell commands there.

## Voice (Fish Audio Drama 3)

**Built with Fish Audio.** The **Voice** tab turns text into speech with Fish Audio Drama 3, run on this machine by
TensorFold's ports of it: on a Mac the MLX port (the 35B Qwen3.5 MoE backbone, its codebook tower and the Fish codec,
in MLX), on a DGX Spark the native CUDA port ([below](#voice-on-a-dgx-spark-cuda)). No Fish Audio cloud API is called
and the pack ships no Drama 3 weights.

- **Write what is said.** Text without a tag is speaker 0; `<|speaker:0|>` to `<|speaker:15|>` switch voices.
  Directions in brackets steer the delivery: `[whispering]`, `[sighs]`, `[laughs]`, or a description of the voice
  (`[a deep, gravelly man in his late fifties, slow and patient]`), which designs a voice without a recording and is
  not read out (measured: that one at 76 Hz, "a bright young woman" at 228 Hz, both transcribed without the bracket).
  Long text is split at sentence ends and keeps one voice.
- **Clone a voice (optional).** A clean clip of one speaker, about 10 to 30 seconds (2 to 60 are taken), and what it
  says; left empty, Whisper writes the transcript (on a Mac the engine's own, already loaded; on a Spark the app's). The consent box (*I have the speaker's consent to clone this
  voice*) must be ticked, and the API refuses a clip without `"consent": true`. Measured with a 3.9 s clip at 125 Hz:
  the takes came out at 130 and 133 Hz with every word right.
- **Settings.** Seed, and Fish's sampling defaults: temperature 1.0, top-p 0.9, top-k 30.
- **Each take** is a job in the queue, so it never runs beside a video render, and it holds the studio's GPU lock
  folder (`~/studio-gpu.lock.d`) while the model runs, waiting if a script outside the app holds it.
- **A turn a line.** Text with two or more speaker tags is sent with each turn after the first on a line of its own,
  on both machines (`voice.dialogue`): written on one line, Drama 3 dropped later turns far more often (TensorFold's
  measurement: 12 of 48 streamed dialogue takes short on one line, 1 of 48 a turn a line).
- **The word check.** On a Mac the engine checks itself (TensorFold `drama3-mlx` 4a8a982f or later): every part it
  speaks is heard by Whisper large-v3-turbo on MLX and aligned to the text; words after the end, or a long tail of
  sound, are cut; a part with words missing is drawn again with a new seed (twice at most, the best kept); and a
  take that passed a likely end and went on is stopped on the model's own end signal. The tab shows the check part by
  part (ok, trimmed, redrawn, words not heard) and its count as the take's score, with what was heard on hover. On a
  Spark the app runs the same check (`app/wordcheck.py`, the engine's rules) on what Whisper (the QA gate's own,
  `app/qa.py`, on the CPU) hears in the finished take, and makes a take with missing words again with another seed,
  not played, twice at most, keeping the best (2.7.8). With an older Mac engine Whisper only scores the take.
- **Plays while it is made**, on both machines: the tab plays the take from `/api/voice/takes/{id}/live` as the
  engine writes it. On a Mac a heard part cannot be drawn again, so a take whose check finds a part short while it
  plays is made once more without playing, and the take says so; untick *Play while it is made* to skip the played
  take (`"live": false` in the API).
- **A take as a MiniMax H3 voice reference.** *Use as Ref2VA voice reference* opens the create wizard on reference
  to video with the take as the next `<Audio N>` (2 to 15 s a clip, 15 s in all: a longer take is cut to its first 15
  seconds, asked first). Add a picture or a video of who speaks, and describe the same voice in the line's Voice box:
  what a reference audio clip does is in [Reference videos and reference audio](cli.md#reference-videos-and-reference-audio-ref2va).
  *Clone this voice* uses a take as the clone clip of the next, for the same speaker across lines.
- **Slate.** On the Sound tab a voice sheet has **Cast voice**: the sheet's sample line, with its description (age and
  gender, accent, timbre, pitch, pacing, energy, texture) as the direction. The take is kept on the sheet (`drama` in
  the project file); ticked, a shot that opens on cast stills sends it as the speaker's reference audio and names it
  in the prompt. A take over 15 s is cut on the sheet first; what does not fit MiniMax's limits is said in the
  preflight and left out.

| Measured, Mac Studio M5 Ultra (TensorFold `drama3-mlx` 92c09a8e, the persistent server, played while made), 2.7.7 | Audio | First sound on the page | Through the app, check included | Words heard |
|---|---|---|---|---|
| One speaker, 16 words, first take (loads the server, 5.2 s) | 8.0 s | 5.43 s (engine 0.21 s) | 8.2 s | 16 of 16 |
| Cloned voice from an MP3 clip (transcript left to Whisper on the CPU first) | 6.6 s | 10.7 s (engine 0.52 s) | 13.4 s | 16 of 16 |
| Two speakers, the tab's example, seeds 1, 2, 3 | 10.6, 9.4, 11.1 s | 0.30, 0.29, 0.23 s | 4.1, 3.6, 4.1 s | 20 of 20 each |
| Two speakers, seed 4: short while playing, made again without playing, redrawn once | 9.9 s | 0.28 s | 19.4 s | 20 of 20 |
| A 120-word read, three parts (two tails trimmed) | 46.4 s | 0.30 s | 16.8 s | 120 of 120 |
| Slate Cast voice | 3.2 s | 0.30 s | 1.5 s | 12 of 12 |
| One speaker, 9 words, loaded | 2.5 s | 0.24 s | 1.5 s | 9 of 9 |

First sound on the page is the first byte of `/api/voice/takes/{id}/live` after the take was asked for. Peak memory
48.5 to 49.9 GB (55.7 GB with a redraw); the server unloaded after 300 idle seconds and before a video job, and gave
the GPU lock back both times.

| Measured, Mac Studio M5 Ultra (TensorFold `drama3-mlx` 4a8a982f, 8-bit experts, played while made), 2.7.6 | Audio | Engine time | Through the app, check included | Words heard |
|---|---|---|---|---|
| One speaker, 16 words | 8.0 s | 2.7 s (71.9 frames/s, first sound 0.22 s; 5.3 s after the click) | 8.7 s | 16 of 16 |
| Cloned voice from an MP3 clip (3.9 s, transcript left to Whisper on the CPU first) | 6.6 s | 2.4 s | 18.4 s | 16 of 16 |
| Two speakers, three lines, seed 1: short while playing (7 of 20), made again and redrawn once | 12.5 s | 11.8 s the second run | 30.5 s | 20 of 20 |
| Two speakers, seed 2: short while playing, the last line still missing after three draws | 22.8 s | | 38.8 s | 16 of 20 |
| Slate Cast voice | 3.2 s | 1.3 s | 7.2 s | 12 of 12 |
| A 120-word read, three parts (two tails trimmed) | 46.4 s | 15.9 s (68.1 frames/s) | 21.9 s | 120 of 120 |

Peak memory 48.7 to 49.9 GB played while made, 55.7 GB with a redraw. Frames are drawn at about 70 a second while
playing (the codec decodes in windows) and 83 when not. The first sound comes 5.3 s after the click because each
take starts the engine's process and loads the model (3.7 s); the engine's own clock says 0.22 s.

| Measured, Mac Studio M5 Ultra (TensorFold `drama3-mlx` f623fb1a, 8-bit experts), 2.7.4 | Audio | Engine time | Through the app, word check included | Whisper |
|---|---|---|---|---|
| One speaker, 18 words | 8.3 s | 3.3 s (63.3 frames/s) | 16.7 s | 17 of 18 ("Phish") |
| Cloned voice, with `[sighs]` and `[laughs]` | 7.0 s | 2.8 s | 16.1 s | 12 of 12 |
| Cloned voice, transcript left to Whisper | 4.3 s | 1.8 s | 24.8 s | 15 of 15 |
| Two speakers, three lines | 11.3 s | 4.4 s | 17.7 s | 20 of 20 |
| Slate Cast voice (a sheet's description as direction) | 7.5 s | 3.0 s | 16.2 s | 15 of 15 |

Engine time is Drama 3's own (prefill, frames and codec); the process also loads the model (about 3 s) and the
word check loads Whisper on the CPU. Peak memory 48 to 51 GB.

**Mac requirements.** An Apple-silicon Mac with 96 GB of memory or more (64 GB up to 4.3.0; the run peaks at about
52 GB; nothing else of the studio runs beside it), about 40 GB of disk for the converted weights (and 39 GB for Fish's checkpoint while
converting), and TensorFold's Drama 3 port in an environment of its own. The port is on TensorFold's `drama3-mlx`
branch of [drowzeys/TensorFold](https://github.com/drowzeys/TensorFold). `bash oneshot-setup.sh --voice` (or
`bash scripts/setup-voice.sh` alone, `--verify` to check) does all of it: the clone at 8f3d4b33 in
`~/tensorfold-drama3-mlx`, its venv, Fish's checkpoint, the conversion and a test line. By hand:

```bash
git clone -b drama3-mlx https://github.com/drowzeys/TensorFold.git ~/tensorfold-drama3-mlx
python3.11 -m venv ~/drama3-venv && ~/drama3-venv/bin/pip install -e ~/tensorfold-drama3-mlx mlx mlx-lm mlx-whisper soundfile torch
hf download fishaudio/drama-3 --local-dir ~/models/drama-3        # Fish Audio Community License: read it first
~/drama3-venv/bin/python -m tensorfold.families.drama3.convert ~/models/drama-3 ~/models/drama-3-mlx   # once, about a minute
~/drama3-venv/bin/python -m tensorfold.drama3 --text "Hello there." --out hello.wav                  # check
```

From `drama3-mlx` 92c09a8e the app runs the engine as a persistent server (`python -m tensorfold.drama3 --serve`,
the same protocol as the Spark's), as described for the Spark [below](#voice-on-a-dgx-spark-cuda): loaded on the first
take, unloaded after five idle minutes (`DRAMA3_IDLE_S`) and before any other job, under the GPU lock and a memory
guard (vm_stat's free, inactive, speculative and purgeable pages; 8 GB, `DRAMA3_GUARD_GB`). An older engine runs once a
take. The app finds `~/drama3-venv` and `~/models/drama-3-mlx` itself (`DRAMA3_VENV` and `DRAMA3_MODEL` name others) and
says on the tab what is missing. `DRAMA3_BACKEND=mlx|cuda` picks the engine (`mlx` on a Mac, `cuda` on a DGX Spark by
default). The API: `GET /api/voice/state`, `POST /api/voice/takes {text, ref_audio?, ref_text?, consent?, seed?,
temperature?, top_p?, top_k?}` (a job of kind `voice`), `GET /api/voice/takes`, `GET /api/voice/takes/{id}/audio`,
`GET /api/voice/takes/{id}/live` (the take as it is made; `"live": false` in a take's settings: not played, Mac only), `POST /api/voice/sheet {sheet, project}` and
`POST /api/voice/cut {path, seconds}`. Takes are kept in `~/ImaginationStudio/voice/` (the WAV, its settings and
measurements, and the text).

### Voice on a DGX Spark (CUDA)

On a DGX Spark the tab runs **TensorFold's native CUDA port of Drama 3**: one binary, `drama3-generate` (Zig with
CUDA kernels built for the GB10's sm_121: the backbone, the codebook tower and the Fish codec, no Python or PyTorch at
run time), with `libtensorfold_drama3.so` beside it.

- **A persistent server.** The first take starts `drama3-generate --serve 1` (one JSON request a line on its stdin,
  one JSON answer a line on its stderr) and waits for it to load: about 18 s from the checkpoint, 38.6 GB of weights
  on the device, 41.6 GB at the peak. Later takes go straight to it.
- **It gives the GPU back.** While loaded it holds `~/studio-gpu.lock.d` (a script outside the app that takes the
  lock waits for it). It is unloaded, and its memory comes back, after **five minutes without a take**
  (`DRAMA3_IDLE_S`), and **at once when any other job of this machine's queue starts** (a video, a picture, a long
  video): Drama 3 and MiniMax H3 never stand side by side. Before it loads, the studio's ComfyUI server is stopped
  if it holds the memory Drama 3 needs (about 50 GB free is wanted); the next video loads its models again.
- **Its own memory guard**: available memory is read four times a second and the engine is killed by its PID under
  8 GB (`DRAMA3_GUARD_GB`); the take fails with the reason and the lock goes back. The engine also ends with the app.
- **Plays while it is made.** The engine writes the take's samples as it makes them and the tab plays them behind an
  open WAV header (`/api/voice/takes/{id}/live`): the first sound about 0.3 s after a take starts on a loaded engine
  (18.5 s on the first take, which loads it), and the take is made faster than it plays.
- **Long text** goes in parts of about 60 words cut at sentence ends (each part opens on the speaker who was
  speaking), joined with 0.15 s between them. A one-speaker take without a clip clones its first part's voice for the
  others (measured on a 56 s read: 271, 272 and 269 Hz in its three thirds); a clip given is used for every part.
- **A voice to clone** may be any sound file: it is converted to 44.1 kHz mono WAV with ffmpeg first (the engine
  reads WAV).

| Measured, DGX Spark GB10 (TensorFold `drama3-cuda` 81173e6c), through the app | Audio | Engine time | Through the app, word check included | Whisper |
|---|---|---|---|---|
| One speaker, 16 words, first take (loads the engine, 18.3 s) | 8.2 s | 5.9 s (30.9 frames/s) | 37.7 s | 16 of 16, 100 % |
| Cloned voice from an MP3 clip (3.9 s, transcript left to Whisper), with `[sighs]` | 6.1 s | 5.0 s (28.0 frames/s, first sound 0.47 s) | 29.7 s | 16 of 16, 100 % |
| Two speakers, three lines, `[whispering]` and `[sighs]` | 11.6 s | 8.4 s (30.2 frames/s) | 21.1 s | 20 of 20, 100 % |
| Slate Cast voice (a sheet's description as direction) | 4.1 s | 3.3 s (28.3 frames/s) | 14.2 s | 11 of 12, 92 % ("harbor") |
| A 120-word read, three parts | 55.7 s | 40.8 s (30.7 frames/s) | 56.9 s | 118 of 120, 98 % |

The cloned take came out at about 130 Hz against the clip's 125 Hz. Engine time is the requests' own (prefill,
frames, codec); the app's time adds Whisper on the CPU (and, for a clip without its words, Whisper on the clip first).

**Spark requirements.** A DGX Spark (GB10, 128 GB), 39 GB of disk for Fish's checkpoint and 1.6 GB for the converted
codec, and about 50 GB of free memory when a take starts (nothing else of the studio runs beside it). The port is on
the `drama3-cuda` branch of [drowzeys/TensorFold](https://github.com/drowzeys/TensorFold) (81173e6c).

**Install it with one command** (after the Spark's main install, or together with it):

```bash
bash oneshot-setup-spark.sh --voice      # Fish Audio Community License: read it first (https://huggingface.co/fishaudio/drama-3)
```

It runs `scripts/setup-voice.sh`, which on a Spark does, each step skipped when it is done already:

1. **The engine** (`spark/build-drama3.sh`) into `$PREFIX/drama3`: `bin/drama3-generate`, `lib/libtensorfold_drama3.so`,
   `commit` and `BUILD`. It first takes the prebuilt engine from the carrier image (`/payload/spark-drama3/`, used only
   when its `commit` is the pinned one and its `SHA256SUMS` check), else builds it from source: TensorFold at
   81173e6c, Zig 0.17.0 fetched under `$PREFIX`, every CUDA kernel fatbin compiled for sm_121 by the Spark's own nvcc
   (12.9 or newer), then `zig build drama3-generate drama3-lib`. Measured on a GB10 from nothing: 1 min 43 s.
2. **Fish's checkpoint** at `~/models/drama-3` (`DRAMA3_CUDA_MODEL` names another). A copy already on this machine (a
   Hugging Face cache, another folder) is linked there instead (`app/discover.py`); otherwise it is downloaded from
   `fishaudio/drama-3` at a pinned revision (39 GB). Should Fish ever gate the repo, the setup says whether to sign in,
   accept the licence or wait for approval (`app/hf_gate.py`), as `--sound` does for Stable Audio Open.
3. **The codec**, converted once on this machine: Fish's `codec.pth` into `codec-v1.safetensors` (1.6 GB), the layout
   the native codec reads. `spark/drama3_codec.py` runs TensorFold's `tools/drama3/codec_convert.py` with Fish's
   reference codec code (the Fish S2 DAC as packaged in sglang-omni 416a70c0, over descript-audio-codec 1.0.0), every
   file fetched at a pinned commit and checked against its SHA-256, in the Studio's own PyTorch (`$PREFIX/venv`):
   about 5 s. No converted Fish weights are published; each machine converts its own copy (measured: the result is
   byte-identical to the one the engine was validated with).
4. **A test line** (a noir narrator), checked to be a few seconds long and not silent, when 50 GB of memory and the
   GPU lock are free (`--no-render` skips it).

`--verify` only checks the four (nothing is built or downloaded); `--upgrade` moves an installed engine to this
version's commit and fetches no weights. `scripts/setup-voice.sh` takes the same `--verify`, `--upgrade` and
`--no-test` on its own. The Models & weights page's Install runs the same script for a missing engine or codec.

**Building it yourself** (another commit, or no network for the script): `DRAMA3_SOURCE=~/tf-drama3 bash
spark/build-drama3.sh` builds a checkout of the `drama3-cuda` branch you already have, `NVCC` names the compiler, and
`ENGINE_FROM_SOURCE=1` skips the carrier image. By hand it is the same `zig build -Dnvcc=$(which nvcc) -Dsm=121
-Doptimize=ReleaseFast drama3-generate drama3-lib` in the checkout, with `zig-out/bin/drama3-generate` and
`zig-out/lib/libtensorfold_drama3.so` copied into `$PREFIX/drama3/{bin,lib}`, and the codec made with
`$PREFIX/venv/bin/python -I spark/drama3_codec.py ~/models/drama-3 $PREFIX/drama3/codec-v1.safetensors`. To check an
engine by hand:

```bash
LD_LIBRARY_PATH=~/.local/opt/tensorfold-studio/drama3/lib ~/.local/opt/tensorfold-studio/drama3/bin/drama3-generate \
  --model ~/models/drama-3 --codec ~/.local/opt/tensorfold-studio/drama3/codec-v1.safetensors \
  --text "The rain had not stopped in three days, and neither had the phone." --wav line.wav
```

The app looks in `$PREFIX/drama3` (`~/.local/opt/tensorfold-studio/drama3`) and `~/models/drama-3`;
`DRAMA3_CUDA_BIN`, `DRAMA3_CUDA_LIB`, `DRAMA3_CUDA_MODEL` and `DRAMA3_CUDA_CODEC` name others, and the tab says what is
missing. The worker Sparks of the Sparks setting do not make voice takes: a take runs on the Spark whose app you use.

**Licence.** Drama 3's weights and codec are Fish Audio Materials under the **Fish Audio Community License v1.0**:
research, non-commercial and small-company commercial use; a hosted service that lets others generate speech or
clone voices needs a separate licence from Fish Audio; show "Built with Fish Audio" where it is used; never clone a
real person's voice without their consent, impersonate anyone, or train other speech models on the outputs.

## Music (MiniMax Music 3)

**MiniMax-Music3.** The **Music** tab makes a song from lyrics and a style with MiniMax Music 3, on this machine: sung
vocals, up to five minutes (the app offers 10 to 300 s; the model takes up to 360), 44.1 kHz stereo WAV. No cloud is
called and the pack ships no weights.

- **Lyrics.** Section tags on lines of their own, above the lines they hold: `[Intro]`, `[Verse]`, `[Pre-Chorus]`,
  `[Chorus]`, `[Post-Chorus]`, `[Bridge]`, `[Hook]`, `[Instrumental]`, `[Solo]`, `[Break]`, `[Build]`, `[Outro]`.
  `[Instrumental]` is a passage without singing; lines in (parentheses) are backing vocals. Empty lyrics make an
  instrumental. The tab warns about lyrics without tags and tags it does not know (the model may sing them).
- **Style.** One caption with the genre, tempo, key, mood, the voice that sings and the instruments, e.g. "Upbeat
  modern pop, 118 BPM, bright synths, punchy four-on-the-floor kick ... Warm female lead vocal with layered
  harmonies." The *Style helper* writes one from six boxes; you can still edit it.
- **Length** is the longest the song may be: the model may end it sooner (measured: asked for 120 s, the short
  hip-hop lyrics ended at 55.6 s on the Spark and 60.9 s on the Mac). The take says when the model ended it.
- **Settings** are MiniMax's: 30 DiT steps at CFG 1.7, the autoregressive model at CFG 1.5 and top-k 50
  (`steps`, `dit_cfg`, `ar_cfg`, `top_k` in the API).
- **Each song** is a job in the queue, so it never runs beside a video render or a voice take. The model stays loaded
  for five minutes after a song (`MUSIC3_IDLE_S`) and is unloaded at once when any other job starts; Drama 3 is
  unloaded before a song in the same way. While loaded it holds the studio's GPU lock (`~/studio-gpu.lock.d`).
- **The lyrics check.** With faster-whisper installed, Whisper listens to the take and counts the lyrics' words it
  heard, in order, up to the last one it heard (`asr.score`), and how far into the lyrics the song got
  (`asr.reached`). Sung words are harder to hear than spoken ones: treat it as a rough count.
- **Play while it's made** (a Mac on the MLX port). With the box ticked (the default when the engine streams), the song plays in the
  tab while the engine makes it. The engine's stream appends raw 16-bit stereo PCM to a file as it decodes each piece
  (`pcm` in the request, as Drama 3's server does), and `GET /api/music/takes/{id}/live` serves it as one WAV, two
  channels at 44.1 kHz, its length left open. A stream is slightly slower than real time and gets slower as the song
  grows (60 s hip-hop: a 4 s piece every 4.2 s at the start, every 4.7 s near the end). So the app holds the sound
  back until playing will not run dry. It waits for the engine's measured no-stall start (10.3 s from the request for
  60 s, 52.7 s for 300 s, a straight line between and beyond) plus 3 s. If the engine is running slower than that
  measurement, it waits for a larger lead as well. The kept take is the engine's finished WAV, the same samples that
  were played (checked byte for byte on two songs), and the lyrics check and *Use as soundtrack* work on it as before.
  Untick the box to hear the song only when it is done.
  Measured through the app on the Mac Studio M5 Ultra (int8, the hip-hop prompt; first sound is the engine's
  `first_audio_ms`, the start is when the live route let the sound go, both counted from the request; the least
  buffer is the smallest amount of song a player started at that moment had in hand, from the bytes as they
  arrived):

  | Song | First sound | Playing from | Least buffer | Made in |
  |---|---|---|---|---|
  | 60 s (seed 7, engine loaded for it, 1.0 s) | 7.6 s | 13.4 s | 4.0 s, at 49 s | 63.4 s |
  | 60 s, ended by the model at 57.9 s (seed 11) | 6.4 s | 13.4 s | 4.0 s, at 45 s | 61.0 s |
  | 30 s (seed 3) | 6.4 s | 8.1 s | 1.6 s, at 19 s | 30.0 s |

  Before that margin was added, a start at the first piece (10.7 s) ran dry for 0.18 s at 53 s of a 60 s song, which
  is why the margin is there. Limits: it works on a Mac only, because neither route on a DGX Spark streams (the CUDA
  engine and ComfyUI's nodes alike) and the box is not shown there. Only 30 s and 60 s songs were measured through the app; the 300 s figure is the
  engine's own (no-stall 52.7 s). Starting about 13 s in is the price of a stream that runs slower than real time,
  and the wait grows with the length (about 56 s for 300 s). The engine found no seams by measure, but nobody has
  listened for them. A browser's own buffering comes on top.
- **Use as soundtrack.** Under a song, *Use as soundtrack* lays it under a video: a long video (its card in Create has
  *Lay a song under it*), the Editor's export or any clip of the Library. In the Editor, *Song under the export* does
  it for the whole timeline. ffmpeg mixes the song at -10 dB (your choice, -30 to 0) under the clip's own sound; a
  compressor keyed by the clip's speech band (200 Hz to 3.5 kHz) lowers it by up to 12 dB while someone speaks
  (measured in `tests/test_music.py`: 6 to 13.5 dB on a test tone); a limiter at four times the rate keeps every peak
  under -1 dBFS, checked after the AAC encode (under the 48.6 s promo: -1.46 dBFS on the Mac, -2.0 on the Spark). The picture is copied;
  the result is a new file in `exports/`.

**On a Mac with an M5 or later and TensorFold's Metal engine** (since 2.9.2, the default where it is built) the song
runs on `music3-generate --serve 1` built for Metal: Zig and Metal, no Python, MLX or PyTorch (fork branch
`music3-metal-115`, commit 91877e79 since 3.3.1: the code of TensorFold pull request #987 on TensorFold 1.1.5; `$PREFIX/music3/bin`,
`MUSIC3_METAL_BIN` names another). It has the CUDA engine's protocol and lifecycle, int8 weights by default
(`MUSIC3_WEIGHTS=bf16` for the checkpoint's own) and the kill-guard. `bash oneshot-setup.sh --music` builds it
(`scripts/build-music3-metal.sh`: Zig 0.17 and Xcode's Metal compiler, a few minutes) beside the MLX port. Without
it, or on an M1 to M4 (its DiT, prompt rows and decoder need the M5's tensor units), the Mac runs the MLX port below,
and `MUSIC3_BACKEND=mlx` chooses that port anyway. **It does not stream yet**, so *Play while it's made* is not shown;
the MLX port keeps it. Measured on a Mac Studio M5 Ultra against the MLX port int8 (same prompt and seed, 60 s
hip-hop, runs alternated, three each, TensorFold's command line): the autoregressive model 69 frames/s against 65,
the whole song 59.5 to 63.0 s against 62.3 to 63.9 s, peak footprint 17.8 GB against 28.1 GB, byte-identical repeats.
Its DiT and decoder match ComfyUI's reference more closely than MLX's (38.97 dB and 103.9 dB against 34.9 and 50.5).
Through the app: a song the model ended at 33.4 s took 35.2 s, the load (2.9 s) included, with a 16.1 GB peak.

**On an older Mac, or with `MUSIC3_BACKEND=mlx`,** the song runs on TensorFold's MLX port of MiniMax Music 3 (fork branch `music3-mlx`, commit 6844b413: the
autoregressive model with its depth decoder, the DiT and the DAV decoder, in MLX), as a persistent process in its own
environment (`python -m tensorfold.music3 --serve`, Drama 3's protocol), under a memory guard (8 GB of vm_stat's free
pages, `MUSIC3_GUARD_GB`). `bash oneshot-setup.sh --music` (`scripts/setup-music.sh`) clones the branch at that commit
into `$PREFIX/music3-src`, makes `$PREFIX/music3-venv`, downloads the weights to `~/music3-models/MiniMax-Music3-Comfy`
(`MUSIC3_MODEL` names another folder; `MUSIC3_VENV` another environment) and makes a 10 s test song. 96 GB of memory
or more (64 GB up to 4.3.0). Measured on a Mac Studio M5 Ultra 256 GB: the installer's test song, 10 s in 12.1 s,
28.1 GB at the peak; through the app, a 60 s song in 75.8 s (the autoregressive model 37.8 s, the DiT 34.6 s, the decoder 2.0 s), MLX's
peak 27.6 GB. TensorFold's own run of the command line (the same hip-hop lyrics as the Spark's, seed 7): 30 s in
61.2 s, 60 s in 82.5 s, both at 27.6 GB.

**int8 weights by default.** The Studio starts the Mac engine with `--quant int8`: the autoregressive model,
its code-0 head and the depth decoder at 8 bits. On the M5 Ultra the autoregressive model went from 40 to 65 frames a
second and the peak from 27.6 to 20.5 GB, so a 60 s song takes about 1.1 times its length. Against ComfyUI's
reference the code-0 logits keep cosine 0.99994 (bf16 0.99997) with 98.0 % greedy agreement, and Whisper's lyric
scores over six same-seed songs stayed within their noise. Measured through the server as the app starts it: 10 s in
10.1 s, 20.9 GB. `MUSIC3_QUANT=none` runs the bf16 weights.

**On a DGX Spark with TensorFold's CUDA engine** (since 2.9.1, the default where it is built) the song runs on
`music3-generate --serve 1` (fork branch `music3-cuda-115`, commit 5505dbfa: the family on TensorFold 1.1.5 since 3.3.0;
`$PREFIX/music3/bin`, `MUSIC3_CUDA_BIN` names another), the Mac engine's protocol and lifecycle: loaded on the first song
(about 13 to 16 s on the 1.1.5 build, about 37 s before it; int8 weights are made on the
GPU as it loads, `MUSIC3_WEIGHTS=bf16` for the checkpoint's own), holding the GPU lock while loaded, unloaded after
`MUSIC3_IDLE_S` and before any other job, under the kill-guard (`MUSIC3_GUARD_GB`, 8). A studio ComfyUI that holds
models is stopped before it loads. `bash oneshot-setup-spark.sh --music` downloads the weights and installs the engine
(`spark/build-music3.sh`: from the carrier image, or built with nvcc 12.9+ and Zig 0.17). Measured on one GB10, int8,
the same hip-hop lyrics, seed 7 (one process a song, memory above the idle machine):

| Asked for | Made | Whole run (load 36.6 to 37.4 s included) | Autoregressive model | DiT, 30 steps | Decoder | Peak memory taken |
|---|---|---|---|---|---|---|
| 30 s | 30.0 s | 60.3 s | 36.4 s (20.9 frames/s) | 22.0 s | 1.9 s | 16.6 GB |
| 60 s | 60.0 s | 123.9 s | 73.9 s (20.4 frames/s) | 46.4 s | 3.6 s | 17.9 GB |
| 120 s (hip-hop lyrics) | 63.3 s (the model ended it) | 128.2 s | 78.0 s | 46.3 s | 3.9 s | 18.9 GB |
| 120 s (long lyrics, seed 5) | 120.0 s | 252.0 s | 151.7 s (19.9 frames/s) | 93.0 s | 7.3 s | 19.3 GB |
| 60 s, bf16 weights | 60.0 s | 194.3 s | 144.4 s (10.4 frames/s) | 46.3 s | 3.6 s | 25.5 GB |

Through the app on a worker Spark: a 60 s song in 149.2 s (load 37.3 s, then 111.9 s; the model ended it at 55.2 s),
18.7 GB at the peak, Whisper 97 %; the engine unloaded after its idle time and gave the lock back. Not streamed;
GB10 only. Without the binary or the weights the tab uses ComfyUI's nodes, below; `MUSIC3_BACKEND=comfy` forces them.

**On a DGX Spark without it** the song runs in the studio's ComfyUI (`spark_generate.py music`) on ComfyUI's own MiniMax Music 3
nodes, laid out as ComfyUI's blueprint *Text to Music (MiniMax Music 3)*: the text encoder (pruned, bf16, 16.7 GB) runs
the autoregressive model, the DiT (fp16, 4.9 GB) the 30 steps, the DAV decoder works in tiles. A song starts from a
ComfyUI with nothing else loaded (the server is restarted when it held the video or picture models); afterwards its
models are freed with ComfyUI's `/free`, after the idle time or before the next job. The memory guard beside the
server is the one every render has, and the song is checked before it is queued (`memory_guard.music_check`).
`bash oneshot-setup-spark.sh --music` downloads the three files (22 GB) to `~/spark-studio-models/MiniMax-Music3-Comfy`.
Measured on one DGX Spark (GB10, ComfyUI 5a665890, one run each, the models in the page cache; peak memory is
MemTotal - MemAvailable sampled once a second, less the 5.7 GB the machine held before):

| Asked for | Made | Whole run | Autoregressive model | DiT, 30 steps | Decoder | Peak memory taken |
|---|---|---|---|---|---|---|
| 30 s (hip-hop lyrics) | 30.0 s | 131.0 s (ComfyUI started for it); 127.5 s loaded | 84.2 s | 44.2 s | 2.1 s | 28.1 GB |
| 60 s (hip-hop lyrics) | 60.0 s | 260.6 s | 163.8 s | 92.5 s | 4.0 s | 28.7 GB |
| 120 s (hip-hop lyrics) | 55.6 s (the model ended it) | 239.1 s | 152.4 s | 82.8 s | 3.7 s | 28.6 GB |
| 120 s (long pop lyrics) | 120.0 s | 527.6 s | 331.4 s | 188.1 s | 7.7 s | 29.1 GB |
| 300 s (long pop lyrics) | 189.8 s (the model ended it) | 844.7 s | 530.1 s | 301.9 s | 12.3 s | 29.0 GB |

On the Spark the autoregressive stage is ComfyUI's Python loop, about 9 frames a second (25 make a second of song), and
it is the larger part of every song; the Mac's MLX port runs it about four times faster. Through the app, from an
empty page cache with ComfyUI stopped, a 60 s song took 289.0 s and 29.3 GB (the table in [New in 2.9.0](changelog.md#new-in-290)).
Whisper heard 96 % of the words of the 60 s hip-hop song above (162 of 168).

The API: `GET /api/music/state`, `POST /api/music/takes {caption, lyrics, seconds, seed?, steps?, dit_cfg?, ar_cfg?,
top_k?, title?, asr?, live?}` (a job of kind `music`; `live: false` does not stream), `GET /api/music/takes`, `GET
/api/music/takes/{id}/audio`, `GET /api/music/takes/{id}/live` (the song being made, a stereo WAV), `POST
/api/music/soundtrack {take, video, music_db?}` (a job of kind `soundtrack`), and an export job's `params.soundtrack
{take, music_db?}`. Songs are kept in `~/ImaginationStudio/music/` (the WAV and its settings and measurements).

**Licence.** MiniMax-Music3 is under the **MiniMax-Music3 Community License**: commercial use is allowed; a commercial
product or service must display "MiniMax-Music3" in its user interface; a licensee above US$20 million of yearly
revenue needs MiniMax's prior written authorisation; a hosted service must keep safeguards against misuse. Do not
redistribute the weights.

## YuE2 songs

**YuE2.** On Linux with an NVIDIA GPU (a DGX Spark, an RTX Pro 6000) the **Music** tab makes songs with YuE2 by
default where it is installed: from your lyrics and a style it first writes the song's score (melody and chords), then
the song itself, sung vocals and accompaniment, 48 kHz stereo WAV, up to five minutes (10 to 300 s). MiniMax Music 3
stays one click away: the **engine picker** above the form (YuE2 · MiniMax Music 3) chooses for each song, and
Settings → Presets → *Song engine* sets the default (*Best here*: YuE2 where it is installed, MiniMax Music 3 on a Mac,
which YuE2 does not run on). Each engine's licence line shows under the picker.

![The Music tab on a DGX Spark: the engine picker (YuE2 · MiniMax Music 3), YuE2's licence line and the song form](screenshots/music-yue2.webp)

- **Install.** `bash oneshot-setup-spark.sh --yue2` (in `--all`, and ticked in the install checklist): about 14 GB,
  the YuE2-3B model and its YuE2-Vae decoder at pinned revisions with their checksums, in an environment of their own
  under `$PREFIX/yue2` (`spark/setup-yue2.sh`, safe to run again). The studio's own environment is not touched.
- **Lyrics.** The same section tags as the tab's buttons: `[Intro]`, `[Verse]`, `[Pre-Chorus]`, `[Chorus]`,
  `[Bridge]`, `[Interlude]`, `[Outro]`, `[Instrumental]`. Others are read as the nearest of those (`[Verse 2]` as
  `[Verse]`, `[Hook]` and `[Post-Chorus]` as `[Chorus]`, `[Solo]` and `[Break]` as `[Interlude]`).
- **Instrumentals.** Empty lyrics (or tags alone) make an instrumental: YuE2 writes a score from the section tags,
  the melody is given to the instruments, and that score is played with no words to sing. The style gets
  "Instrumental ... no vocals, no singing, no choir, no spoken words" added where it does not say so.
- **Length** is the longest the song may be. YuE2 decides a song's length from its lyrics; a song that would run
  longer is cut at the length you set and faded out over its last 2 seconds (the take says so).
- **The score.** Each YuE2 take keeps its score (`score.abc`, *Download score* on the take) and what it was made from,
  in `~/ImaginationStudio/music/<id>.yue2/`.
- **Each song** is a job in the queue, as with MiniMax Music 3: YuE2 loads on the first song (about 15 s on a DGX
  Spark), stays loaded for five minutes after a song (`YUE2_IDLE_S`), and is unloaded at once when any other job
  starts, MiniMax Music 3 included. While loaded it holds the studio's GPU lock. Whisper's lyrics check works as for
  MiniMax Music 3.
- **Speed.** On a DGX Spark the score and the song's tokens run on TensorFold's CUDA engine for YuE2
  (`libtf_yue2.so`, built by `--yue2`: int8 weights, a scale for each group of 128, one CUDA graph a token), about
  3x the speed of bf16, its next-token choices checked against YuE2's own bf16 ones token by token over whole songs;
  the flow matching and the decoder run on YuE2's own bf16 and fp32 weights. Where the engine is not built (another GPU) the tokens use an int8 copy of the
  model's projections in PyTorch instead. `YUE2_BACKEND=torch` runs that loop on a Spark too; `YUE2_QUANT=bf16` runs
  bf16 only.

Measured on one DGX Spark (GB10), the model loaded, English indie-pop lyrics at seeds 7, 11 and 21 (each song ran
longer than its length and was cut there); Whisper's check on each song; the peak is the server's own memory:

| Setting | 60 s song | 120 s song | Whisper heard (60 s / 120 s) | Ready in | At the peak (120 s) |
|---|---|---|---|---|---|
| TensorFold engine, int8 (default on a DGX Spark) | 35.0 to 40.4 s | 60.4 to 66.9 s | 94 to 97 % / 97 % | 6 to 9 s | 21.1 GB |
| int8 PyTorch token loop (`YUE2_BACKEND=torch`, other GPUs) | 45.7 to 47.6 s | 76.0 to 78.8 s | 68 to 97 % / 79 to 99 % | 21 s | 19.1 GB |
| bf16 (`YUE2_QUANT=bf16 YUE2_BACKEND=torch`) | 94.9 s | 146.7 s | 98 and 97 % (60 s, seeds 7 and 11) | about 15 s | 19.4 GB |

The score comes first, about 1,200 to 1,800 tokens whatever the length (11 to 17 s on the engine, 17 to 19 s on the
PyTorch loop); the song then takes about 0.4 s a second of song (0.5 s on the PyTorch loop). Through the app (a test
studio), a 45 s song on the PyTorch loop, the model loading first, took 76.9 s and Whisper heard 92 % of its words.
Four instrumentals of 65 and 70 s took 47 to 76 s on the PyTorch loop.

**Licence.** YuE2's code is Apache 2.0. Its weights are CC BY-NC 4.0 with the YuE2 authors' additional permission for
individual creators: personal users, content creators and musicians may publish, sell and license the songs they make,
free and with no royalties; a company needs a commercial licence from the authors; the songs must not be used for
anything illegal, harmful or deceptive. Do not redistribute the weights.

## The brain engine (NVIDIA Nemotron 3.5 Bolt)

The brains (Slate's prompt help, the QA look, the Agent room) can run on **NVIDIA Nemotron 3.5 Bolt**, loaded by the
Studio itself on this machine, the way Voice runs Drama 3 and Music runs MiniMax Music 3: no separate server to start.

```bash
bash oneshot-setup-spark.sh --bolt   # DGX Spark: NVIDIA's 24 GB checkpoint and LiLiCorr drafter (~25 GB) and the engine
bash oneshot-setup.sh --bolt         # Mac: the same weights; the engine is TensorFold's Metal port when it lands
```

The weights are NVIDIA's (OpenMDW License Agreement 1.1: commercial use allowed; a copy passed on keeps the licence and
NVIDIA's notices). On a DGX Spark the engine is TensorFold 1.1.5's native server with **Mia's Bolt backend**
(MiaAI-Lab/Nemotron-3.5-Bolt-TensorFold-DGX-Spark, MIT, with ChriZzn's speed work; drowzeys/TensorFold branch
`bolt-lilicorr-115`): `spark/build-bolt.sh` installs the release archive (pinned by sha256), else the carrier's copy,
else builds it from source, into `$PREFIX/bolt`, and keeps the engine it replaces in `$PREFIX/bolt.previous`.

- **Which engine** (`BOLT_BACKEND`): `cuda` on a DGX Spark where it is built; `metal` on a Mac once TensorFold's Metal
  Bolt engine is built into `$PREFIX/bolt-metal`; `llamacpp` on a Mac only when set (llama.cpp's `llama-server` with
  NVIDIA's GGUF: `BOLT_LLAMACPP_BIN`, `BOLT_GGUF`); `external` for a server the Studio does not manage. Unset, the
  Studio takes the built TensorFold engine, else nothing changes.
- **Which address**: the brains use the engine when Settings → Connections leaves the local server's address empty or
  picks **Studio engine · Bolt** (`studio:bolt`). An address you type is your own server, asked as before.
- **When it loads**: on the first brain question or agent answer, on a loopback port of its own (about 40 s on a
  Spark from the page cache). The Brains page shows *NVIDIA Nemotron 3.5 Bolt (Studio engine)*, its state (loaded,
  unloaded, loading, unloaded for a render) and backend, with **Load** and **Unload**; the Machine room and the status
  bar show what it holds.
- **When it unloads**: after `BOLT_IDLE_S` (600) idle seconds (`never` for a head that never renders), and before a job
  of this machine's queue whose measured peak does not fit beside it: on a Spark the memory guard's estimate for the
  job with Bolt resident (clips, songs, sound, Drama 3), on a Mac the peaks measured on an M5 Ultra against what
  vm_stat counts as free. A job without a measured peak unloads it. Clips sent to worker Sparks never unload it.
  Unloaded for a job, it stays unloaded until the queue is empty.
- **Agent turns**: an answer in flight is never cut: a job that needs the memory waits for it, then unloads Bolt.
  An agent's turn that queued a clip waits for it with Bolt unloaded, and Bolt loads again for the turn's next
  answer. A question from a page while Bolt is unloaded for a render is answered with the reason, at once.
- **Memory**: it needs about 43 GB free to load on a Spark and holds about 28 GB (4 streams, 65,536 tokens each,
  2 GiB of prompt cache, pictures on, thinking off: `BOLT_PARALLEL`, `BOLT_CONTEXT`, `BOLT_PROMPT_CACHE_GIB`,
  `BOLT_VISION`, `BOLT_THINKING`). That memory counts as a render's when a clip is checked, since the render unloads
  it. A kill-guard ends it under `BOLT_GUARD_GB` (8) available, and it ends with the app.

## Sound effects and foley (Stable Audio Open, MMAudio)

Sound → **Sound effects** makes two kinds of take, each a job of the queue, on a Mac (`bash scripts/setup-sound.sh`,
or `oneshot-setup.sh --sound`; about 13 GB of weights in `$PREFIX/sound-models` and Hugging Face's cache):

- **Foley for a clip** (MMAudio large_44k_v2): pick a Library clip; MMAudio watches its frames and makes its sound,
  steps on the footfalls. Words are optional. The result is a WAV and the clip with it, laid under the clip's own
  sound at -6 dB (its speech stays on top; peaks under -1 dBFS) or in place of it. Up to 15 s, the studio's longest clip.
- **A sound effect** (Stable Audio Open 1.0): words to 1 to 47 s of 44.1 kHz stereo.

Both land in `~/ImaginationStudio/sound/` with a take file each, and in the Library under **Sound effects** (foley
clips under Clips). `POST /api/jobs {kind: "sfx", params: {prompt, seconds, negative?, seed?, steps?, cfg?}}` or
`{kind: "foley", params: {video, prompt?, negative?, keep_sound?, foley_db?, seed?}}`; `GET /api/sound/state`,
`GET /api/sound/takes`.

**On a DGX Spark** (`bash oneshot-setup-spark.sh --sound`, about 11 GB in `$SPARK_MODELS/Sound-Comfy`) both run through
the studio's ComfyUI server (`spark/comfy.sh`, with its memory guard beside it) as `spark/spark_generate.py sfx` and
`foley`, never as standalone model code:

- **A sound effect** runs on ComfyUI's own Stable Audio nodes (the checkpoint, its T5-base encoder, the seconds as
  conditioning, KSampler with dpmpp_3m_sde_gpu on the exponential schedule), the files from Stability's gated repo
  with your own Hugging Face sign-in.
- **Foley** runs on kijai's ComfyUI-MMAudio (MIT, pinned at `8eaeb72e`) with the Studio's own sampler node
  (`spark/studio_mmaudio`, put beside it by `comfy.sh`): kijai's sampler reads one stream of frames as both CLIP's
  8 a second and Synchformer's 25 a second, so a 24 fps clip would land a little early; the Studio's node takes the
  clip's own rate and picks the frame on screen at each of MMAudio's times. The clip is brought to 384 rows before
  ComfyUI decodes it (a 15 s clip at 1376x768 would be 4.6 GB of frames in memory, at 384 rows 1.1 GB). The weights
  are Kijai/MMAudio_safetensors (fp16, run in bf16) and NVIDIA's BigVGAN v2, fetched by the install so nothing is
  downloaded at the first take.

Each starts from a ComfyUI server with nothing else loaded, as a song does. A kind renders on a Spark only once
`spark/memory_guard.py` has its measured estimate (`SOUND_ESTIMATE`, `python3 spark/memory_guard.py sound`): it is
asked before the job is queued and again before anything loads, and a kind with no measurement is refused with that
reason. The WAV is levelled the way a song is (full level, peaks under -1 dBFS) and foley is laid under the clip by
the same ffmpeg step as on a Mac.

**Licences.** Stable Audio Open 1.0: Stability AI Community License (free under US$1 million of yearly revenue); its
Hugging Face page is gated, so accept the licence there first (the studio's Install guide signs you in to Hugging
Face). MMAudio's weights are **CC-BY-NC 4.0: non-commercial**; its code is MIT. Do not redistribute either's weights.
