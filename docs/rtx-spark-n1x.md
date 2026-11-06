# Imagination Studio on an NVIDIA RTX Spark N1X laptop

The RTX Spark N1X (Windows 11 on Arm) has the same GPU architecture as the DGX Spark's GB10: Blackwell, sm_121,
6144 CUDA cores. The Studio runs on it **natively on Windows**: the DGX Spark's render path (ComfyUI, the Studio's
graphs and TensorFold's engines) on Windows' own NVIDIA driver. There is no WSL version: the WSL2 install was removed
on 2026-11-06. The dated measurements below that compare with WSL are kept as they were taken.
What differs from a DGX Spark is the laptop's memory split and its power budget.

<a id="install-one-shot-natively"></a>

## Install: one shot

In an administrator PowerShell on the laptop:

```powershell
irm https://raw.githubusercontent.com/drowzeys/Imagination-Studio-by-keys-Wasserman/main/oneshot-setup-rtx-spark-n1x.ps1 -OutFile $env:TEMP\n1x.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\n1x.ps1
```

`oneshot-setup-rtx-spark-n1x.ps1` checks the machine (ARM64, an N1X), clones the Studio on Windows (`-StudioDir`,
default `%USERPROFILE%\Imagination-Studio`), asks once for a **Hugging Face read token**
(https://huggingface.co/settings/tokens; accept the FLUX3-K and Stable Audio Open licences with that account; saved in
`%USERPROFILE%\.cache\huggingface\token`, `-HfToken hf_...` passes it, `-NoHfToken` skips the gated models) and runs
`scripts\setup-windows-cuda.ps1`. It then makes **Imagination Studio** (start) and **Stop Imagination Studio** with the
Studio's icon on the Desktop and in Start, **pins Imagination Studio to the taskbar** (Windows 11's taskbar layout,
appended to your own pins; it shows after Explorer restarts, or at the next sign-in), and keeps the laptop awake for
renders: no sleep or hibernate plugged in, the lid does nothing, Windows on Best performance, the Wi-Fi adapter never
powered down (`-NeverSleepOnBattery` for battery too, `-NoPower` to skip). Optional: `-StartAtLogon` (starts the Studio
at sign-in), `-EnableSsh -AuthorizedKey "ssh-ed25519 ..."` (another machine reaches the laptop over SSH on the home
network). `-Native` is accepted for older command lines and changes nothing.

Before any component install/download, native setup prints a runtime-fit plan. The default broad selection
skips missing models whose existing runtime admission requirement exceeds this machine's GPU pool (and host
pool where applicable), not merely its current free memory. For example, Drama 3 needs 50 GiB free under its
current policy, so a fresh install with a 30.5 GiB N1X GPU pool skips Voice automatically. A machine with 96 GiB
installed RAM still needs a suitable GPU carve-out and live headroom; it is not automatically qualified.
Passing `-NativeParts` selects an explicit set instead. An explicit capacity mismatch requires
`-AllowInsufficientModel` to download anyway. Cached files remain untouched; temporary contention and unknown
requirements are not labelled permanently incompatible. `-PlanOnly` is still probe-free. See
[Windows setup and qualification](windows.md) for policy sources, limitations, and the separate unknown-fit
image acknowledgment.

`scripts\setup-windows-cuda.ps1` installs:

1. the build tools where missing: Python 3.13 (ARM64), Git, Zig 0.17 and the Visual Studio 2022 Build Tools with the
   ARM64 compiler (winget), and NVIDIA's CUDA 13.4 toolkit for Windows on Arm from its redistributable archives (nvcc,
   the runtime, cuBLAS, NVRTC, nvJitLink, CCCL, NVVM; each archive checked against NVIDIA's sha256) into `C:\cuda-13.4`;
   Zig is detected before invoking it. Missing or older versions trigger the pinned winget install, a terminal
   PATH refresh and a version recheck. If Zig is still unavailable, setup stops with instructions to open a new
   PowerShell window or correct PATH order, rather than continuing to an engine build with missing tools.
2. the app's venv, FFmpeg, the GPU venv (NVIDIA's PyTorch 2.14 for CUDA 13.4) and the pinned ComfyUI (PR #200's scripts);
3. TensorFold's engines **built from source** (no prebuilt release yet): drowzeys/TensorFold `studio-1.2.2` at
   `1c8bdbfd`: `zig build tf-h3-int8 -Dsm=121` (`tf_h3.dll`), `zig build native bolt -Dsm=121 -Doptimize=ReleaseFast`
   (`tensorfold-native.exe` + `bolt.dll`), `zig build fish-drama3-generate fish-drama3-lib -Dsm=121`
   (`drama3-generate.exe` + `tensorfold_drama3.dll`) and, with the music part, `zig build music3-generate -Dsm=121
   -Doptimize=ReleaseFast` (`music3-generate.exe`), in an ARM64 developer prompt, into
   `%LOCALAPPDATA%\ImaginationStudio\tf-h3`, `...\bolt\native\bin`, `...\drama3\bin` and `...\music3\bin`, each with
   the commit it was built from (a run at the same commit keeps them);
4. the models (`scripts\windows-cuda-setup.py models`: the DGX Spark's files from the same repos, with the Spark
   scripts' own SHA-256s where they pin one; `-NativeParts`, default video, image, music, FLUX3-K, sound effects, swap
   (Ref2VA int8, the Character Swap and Turbo LoRAs, SAM 3.1: 25 GB), workflows (the Library's actions: three node packs,
   SAM 3.1 and the latent upscaler), foley (kijai's ComfyUI-MMAudio and MMAudio's 5.6 GB) and finish (Tesseract), plus
   NVIDIA's Bolt checkpoints and Fish Audio's Drama 3; `-NoBolt` and `-NoVoice` leave those out; `train` adds
   musubi-tuner's venv and its 46 GB of files: MiniMax H3's FL2VA transformer in bf16, its fp16 video encoder and ostris's
   adapter);
5. ComfyUI's model folders and `cuda-tier.json`.

Run again, it resumes and updates. `-ToolchainOnly` installs step 1 alone; `-SkipToolchain -Parts <part>` adds one part
to an install.

## Start and stop

- **Start:** click Imagination Studio on the taskbar or Desktop. Its notification-area tray controller starts the
  Studio and opens `http://127.0.0.1:7870`. If an app is already running, it only opens the page.
- **Stop:** choose Stop Studio in the tray, or Stop Imagination Studio in Start. Confirm the action; queued or
  running jobs prevent stopping. A tray-owned app gracefully unloads its models before exiting.
- **Exit tray** leaves Studio running; a separate supervisor owns the app, so no visible command window is needed.
  It never kills by name.
- An app started manually, over SSH, or by an older launcher is labelled **External Studio**. The tray can open
  it but will not adopt or stop it. Stop it through its original launcher before starting a tray-owned session.

An existing checkout can register without running model setup. Use an updated checkout and a Windows Python 3.13
runtime (`py` or the native UI environment); the tray controller itself uses only the standard library:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-desktop.ps1
```

Registration creates the Start/Stop shortcuts and keeps a local copy of the tray controller. A WSL2 registration left
by an older install is ignored and dropped at the next registration. Settings and private launch identity live outside
the repository in `%LOCALAPPDATA%\ImaginationStudio`; credentials never appear in URLs, public app state or logs.

## Memory

The firmware gives the GPU a **fixed carve-out**: on an HP OmniBook X 14 with 128 GB, the driver reports 98,304 MiB for
the GPU and Windows sees about 30 GB. Nothing on Windows changes that split: the laptop's BIOS (F.05) has no graphics or
memory-size setting among its 128, and the driver's carve-out is fixed. The CUDA pool Windows offers is about 113 GB
(the dedicated 98 GB plus shared system memory). The models live in the GPU's part, so the split suits the Studio;
what has to fit in Windows' 30 GB is loading them.

ComfyUI loads a model with its dynamic VRAM loader: the file is mapped, and the weights are streamed into GPU memory as
they are first used. The pages it reads stay in ComfyUI's working set after they are copied, so a big load (FastH3's
42 GB, after the 26 GB text encoder) runs Windows' available memory down to under 1 GB, with commit and the GPU fine;
Windows then raises its low-memory signal. `spark/comfy_win.py`'s memory guard answers that signal by trimming
ComfyUI's working set first (`EmptyWorkingSet`: clean file pages go back to Windows' standby list, as Linux's page cache
gives them back on a Spark), and stops the render only if host memory is still low 2 seconds after the trim. Commit and
GPU floors are never relaxed this way.

Measured 2026-11-06 on that laptop: FastH3, 864x480, 90 frames, 4 passes, from a stopped Studio: **133 s** in all,
4.7 s a pass, of which 71.6 s was TensorFold's one-time int8 conversion of the checkpoint (saved for the next start).
The trim fired once during the load; Windows' available memory stayed between 1 and 6 GB until the model was
in GPU memory, then went back to about 20 GB. Without the trim the
same render was stopped by the guard at the load, each time. ComfyUI's classic loader (`--highvram`) is no way out:
it builds the 42 GB model in Windows' RAM before moving it and crashed reading the file.

<a id="native-windows-the-native-cuda-tier"></a>

## How the native Studio works

On Windows the Studio is the same app as on a DGX Spark (`app/windows_runtime.py`, `app/windows_cuda.py`), with
TensorFold's Windows engines and a CUDA pool of about 113 GB:

- each feature uses its installed backend. The shared GPU Python/ComfyUI core does not depend on H3;
  H3 alone retains its DLL and installer-measured pool requirement of at least 90,000 MiB.
  Bounded recipes measured on [48/64 GB](windows-rtx-spark-plan.md) remain available alongside native alternatives.
  The legacy file `cuda-tier.json` records paths/capacity rather than selecting a different app; `-Preview` is a launch alias;
- qualification gating and badges are required below 128 GiB installed RAM and optional/off by default above it.
  Diagnostics remain visible and individual checks can run either way. Prerequisites and live memory guards always
  apply; the threshold does not guarantee model eligibility or qualify unmeasured Windows image recipes;
- the Spark's jobs build the same commands; the bash scripts they run are replaced by their Python ports
  (`spark/windows_scripts.py`: scripts/video.sh, studio.sh, scout.sh, image.sh, edit.sh, with the same checks and words)
  and spark/comfy.sh by `spark/comfy_win.py`: TensorFold's H3 node copied into ComfyUI with `TF_H3_LIB` at
  `tf_h3.dll`, a lower priority, the server owned by a job object and its recorded identity (never by name or port), the
  Windows memory guard beside it (`app/windows_memory.py`: Windows' commit and low-memory signals and the CUDA pool,
  four times a second; it stops the server and the render says why), and the server left warm after a render;
- the Bolt brain is TensorFold 1.2.2's `tensorfold-native.exe` with `bolt.dll` (Mia's recipe on the Windows build), run by
  the Studio as on a DGX Spark (loaded on the first question, unloaded when idle or when a render needs the memory),
  in a job object that ends with the Studio;
- every job kind of the DGX Spark runs natively once its part is installed; a part that is not installed says which
  command installs it (`scripts\setup-windows-cuda.ps1 -SkipToolchain -Parts <part>`). Only
  DGX Spark workers (handing clips to other Sparks) are not offered: this machine renders alone;
- the Library's actions (turnaround sheet, upscale, find a person, remove a person) run their authors' own ComfyUI node
  packs at the Spark's pinned commits (MATLOWAI's H3 Contact Sheet, dntpi's H3 Video Upsampler, Akatz Labs' H3 Relay,
  SAM 3.1 on ComfyUI's nodes) through `spark/spark_workflows.py`, and `scripts/turnaround.sh` has its port
  (`windows_scripts.py turnaround`); Character Swap runs `spark/spark_swap.py` as on a Spark. Their frames and LoRAs are
  hard links where a Spark makes symlinks (a symlink needs a privilege a person may not have), and which ComfyUI serves
  is read from `comfy_win.py`'s record instead of `pgrep`;
- MMAudio foley runs kijai's ComfyUI-MMAudio with the Studio's sampler node, as on a Spark; the installer adds the pack's
  requirements only where they add packages (librosa, numba and the rest have Windows ARM64 wheels);
- Looks -> Train a LoRA runs kohya-ss's musubi-tuner in a venv of its own (`%LOCALAPPDATA%\ImaginationStudio\train`,
  NVIDIA's PyTorch for CUDA 13.4, musubi-tuner's own Pillow fallback where opencv has no ARM64 wheel; bitsandbytes'
  8-bit AdamW runs a step on the N1X), each step in a job object with the Windows memory guard beside it;
- Finish runs Tesseract and Tesseract Converter from their windows-x86_64 releases (the only Windows builds Mirage
  publishes) through the x64 emulation of Windows 11 on Arm (`scripts\setup-windows-finish.ps1`: pinned SHA-256s, the
  bundles' own checksum lists; Tesseract's own install.ps1 refuses an ARM64 processor, so it is not used);
- Fish Audio Drama 3 runs on its native Windows engine (`drama3-generate.exe` with `tensorfold_drama3.dll`) and MiniMax
  Music 3 on its own (`music3-generate.exe`, in place of ComfyUI's nodes), both TensorFold `studio-1.2.2` `1c8bdbfd`, the
  same server protocol as on a DGX Spark; where their files are missing the Voice and Music tabs say so (songs then run on
  ComfyUI's MiniMax Music 3 nodes).

## Measurements from the WSL2 install (2026-11-03 and 04)

Taken while the Studio also had a WSL2 version on the laptop (removed 2026-11-06); kept as measured.

### Speed: performance mode against the DGX Spark

Measured 2026-11-03, HP OmniBook X 14 (RTX Spark N1X, 128 GB, BIOS F.05), plugged in, Windows on Best performance,
against a DGX Spark (GB10). Same Studio (`main`), same TensorFold int8 engine (`1f7ecc75`), same ComfyUI, same seed:
the clips are identical byte for byte on both machines. GPU clock and power are nvidia-smi's, sampled every 2 s while
the passes ran.

**864x480, 124 frames, 8 passes (models loaded):**

| Machine and mode | A pass | The clip | GPU while rendering |
|---|---|---|---|
| DGX Spark (GB10) | **6.6 s** | — | — |
| N1X, HP **Performance** | **7.0 s** | 72.7 s | ~1957 MHz, ~58 W, up to 75 °C |
| N1X, HP default (Smart Sense / Balanced) | 9.0 s | 93.8 s | ~1555-1600 MHz, ~35-38 W, ~70 °C |
| N1X, HP default, Fan Always On (BIOS) | 9.0 s | 94.2 s | ~1555 MHz, ~35 W |
| N1X, Windows **Best power efficiency** (power saving) | 13.5 s | 138.5 s | ~1507 MHz, ~30 W, up to 67 °C |

**1376x768, 192 frames (8 s), a Studio video job from a first frame, from a cold start (models loaded first):**

| Machine and mode | The whole job | Of it, the render | GPU while rendering |
|---|---|---|---|
| DGX Spark (GB10) | 625.6 s | 410.1 s | — |
| N1X, HP **Performance** | **619.0 s** | 585.1 s | ~1896 MHz, ~51 W, up to 74 °C |
| N1X, out of Performance (Smart Sense had switched it) | 736.1 s | 658.8 s | ~1400 MHz, ~31 W |

- In HP's Performance mode the N1X is within about 6% of the DGX Spark a pass at 864x480, and a whole 1376x768 job
  finished as fast as the DGX Spark's (619 s against 626 s; its render took 585 s against 410 s, its QA gate less);
  in its default mode a pass is about 36% slower, in Windows' power-saving mode about twice as slow (13.5 s).
- The limit is the laptop's power budget, not heat or WSL: the GPU is 96% busy, under 75 °C, and nvidia-smi reports
  software power capping; the fan made no difference. The GPU's own power limit is not settable (N/A).
- HP's Smart Sense can leave Performance by itself: check myHP before a long render, keep it plugged in and the lid
  open (many laptops cut power with the lid shut).
- The whole-job times include loading the models and the QA gate's look at the take, which differ between runs.

### An LLM on the N1X: Performance against power saving

Not the Studio's own workload, measured the same way for anyone serving a language model on the laptop beside it.
Measured 2026-11-04 on the same HP OmniBook X 14, plugged in, in WSL2 Ubuntu: vcruz305's
[Qwen3.8-Flash-Next EXL3 recipe](https://github.com/vcruz305/Qwen3.8-Flash-Next-EXL3-DGX-Spark-recipe) (exllamav3
`bccada04` and TabbyAPI `880e6b8`, built for sm_121 with the CUDA 13.4 toolkit) serving
[CYBER-FROST-3.8-EXL3-SAGE-3.87bpw](https://huggingface.co/vcruz305/CYBER-FROST-3.8-EXL3-SAGE-3.87bpw) with MTP drafts and
the 39 GB n-gram table streamed from disk (WSL has ~29 GB of RAM to cache it), about 69 GB of the GPU's 96. The
recipe's own clients: `bench_v1.py` (one warmup, three 400-token replies a class) and `concurrency.py` (3,000-token
prompts, 400 tokens, three rounds, totals include prefill). "Performance" is HP Performance with Windows on Best
performance; "power saving" is Windows' Best power efficiency. GPU clock and power: nvidia-smi every 2 s while busy.

| | Performance | Power saving | DGX Spark (the recipe's own) |
|---|---:|---:|---:|
| Decode, code (tok/s) | **41.0** | 25.6 | 45.8 |
| Decode, DevOps (tok/s) | **36.3** | 21.6 | 44.3 |
| Decode, prose (tok/s) | **29.0** | 16.4 | 34.4 |
| First token, short prompt (s) | 0.74-0.80 | 1.33-1.44 | — |
| 17,841-token prompt: prefill (tok/s) | **950** | 460 | — |
| 17,841-token prompt: first token (s) | 19.1 | 39.6 | — |
| Decode after that prompt (tok/s) | 29.3 | 18.3 | — |
| 1 / 2 / 4 requests at once, total (tok/s) | 30.6 / 33.6 / 36.2 | 17.6 / 18.3 / 19.6 | — |
| GPU while busy | 2166 MHz, 48 W, up to 72 °C | 1375 MHz, 15 W, up to 62 °C | — |

- Power saving roughly halves everything (decode 0.56-0.62x, prefill 0.48x) at under a third of the GPU's power.
- In Performance the laptop decodes at 82-90% of the DGX Spark's figures (the recipe's, on a GB10).
- More requests at once add little (+18% total at four): every token reads the n-gram table, which WSL's small RAM
  cannot hold, so the disk is shared.
- vcruz305's DSV4.1-Flash-1017 EXL3 recipe holds about 107 GiB of device memory on a GB10 and does not fit the N1X's
  96 GB carve-out.

### TensorFold on the N1X: in WSL and native on Windows

The same model family on [TensorFold](https://github.com/ashhart/TensorFold) 1.2.2's Flash-Next CUDA engine, serving
[Blackfrost-AI/CYBER-FROST-3.8-FP4-Local](https://huggingface.co/Blackfrost-AI/CYBER-FROST-3.8-FP4-Local) (ModelOpt
mixed FP4, 72.7 GiB on the GPU, n-gram tables read from the files, MTP drafts), `--context 32768 --parallel 4
--no-thinking`, measured 2026-11-04 with the same clients and settings as the EXL3 runs above, HP Performance mode:

- **In WSL**: TensorFold's Linux build, rebuilt with one fix. Its integrated-GPU admission reads `/proc/meminfo` on
  Linux, which under WSL is the ~30 GB of RAM, not the GPU's pool, so it refused the checkpoint; under WSL it now reads
  the CUDA pool as on native Windows (sent upstream).
- **Native on Windows**: `tensorfold-native.exe` built from source for `aarch64-windows`, `sm_121` (Zig 0.17, the CUDA
  13.4 Windows-on-Arm toolkit from NVIDIA's redistributable archives, MSVC ARM64 build tools); its GPU suites pass
  (`glue`: 1473 of 1473 byte-equal). Natively the WDDM pool is 113 GB (WSL sees 96).

Client end-to-end tokens/s (prefill included; TensorFold's server reports no separate decode rate):

| | DGX Spark, EXL3¹ | N1X EXL3, WSL | N1X EXL3, WSL, power saving | N1X TensorFold, WSL | N1X TensorFold, native Windows |
|---|---:|---:|---:|---:|---:|
| Code | 45.8 | 38.6 | 25.6² | 43.8 | **50.4** |
| DevOps | 44.3 | 34.5 | 21.6² | **42.2** | 40.2 |
| Prose | 34.4 | 27.8 | 16.4² | 35.7 | **37.3** |
| First token, short prompt (s) | — | 0.74-0.80 | 1.33-1.44 | **0.21-0.24** | 0.22-0.28³ |
| First token, ~17,850-token prompt (s) | — | 19.1 | 39.6 | **10.2** | 10.9³ |
| 1 / 2 / 4 requests at once, total | — | 30.6 / 33.6 / 36.2 | 17.6 / 18.3 / 19.6 | 36.4 / 50.2 / 65.6 | **36.6 / 54.4 / 70.0** |
| GPU while busy | — | 2166 MHz, 48 W | 1375 MHz, 15 W | 2120 MHz, 49 W | 2139 MHz, 42 W |
| GPU memory left for streams | — | — | — | 15.4 GiB | **19.0 GiB** |

¹ [vcruz305's recipe](https://github.com/vcruz305/Qwen3.8-Flash-Next-EXL3-DGX-Spark-recipe), its own GB10 figures for
the Cyber Frost 3.87 bpw pack (server decode, n-gram table on disk, corrected template); not measured here.
² Server decode rate (the power-saving run's client figure was not kept). ³ Includes ~6 ms of network round trip: the
clients ran on another machine through an SSH tunnel.

- TensorFold against EXL3 on the same laptop: +13-28% a stream, first token 3.4x sooner, a long prompt in about half
  the time, and 1.8-1.9x the total at four requests.
- Native Windows against WSL: within about ±15% (three samples a class), and more memory left for streams.
- On TensorFold the N1X matches or passes the DGX Spark's EXL3 figures for this model.

## Native measurements (2026-11-04)

### Measured natively

2026-11-04, the HP OmniBook X 14 (RTX Spark N1X, 128 GB), plugged in, Windows' Balanced plan, the Studio on the native tier, every job queued through the Studio's own API (`/api/jobs`,
`/api/voice/takes`, `/api/music/takes`, `/api/brains/test`) as the page queues it. Wall time is the job's, queued to done.

| Job | Native Windows | WSL on the same laptop | DGX Spark |
|---|---:|---:|---:|
| FastH3, 8 passes, 864x480, 124 frames (5 s), text to video, models loaded | **73.8 s** (a pass 7.03 s) | 72.7 s in HP Performance (7.0 s), 93.8 s in HP's default (9.0 s) | 73 s (6.6-7.1 s) |
| The same, first job after a start (ComfyUI started, models loaded) | 114.0 s | — | — |
| The same from a first frame (the still below), models loaded | 92.7 s (a pass 8.54 s) | — | — |
| Text to picture to video (`studio`, Qwen-Image then FastH3), from a cold server | 131.8 s | — | — |
| A still, Qwen-Image-2.1-Turbo 8 steps, 864x480, server started for it | 31.0 s (the graph 14.2 s) | — | — |
| Fish Audio Drama 3, one 7.5-7.8 s sentence, engine loaded for it | 39.1-39.2 s (load 34.0-34.4 s; 4.3-4.7 s to speak it, 1.65-1.74x real time, first sound 223-229 ms) | — | — |
| MiniMax Music 3 on ComfyUI, a 30 s song, Drama 3 unloaded first | 133.8 s | — | — |
| Bolt (Studio-managed), load and first reply | 27.0-30.4 s; a 70-token reply 1.4 s | — | — |
| Bolt, 400 tokens, one stream (prefill included) | prose 58.6-60.8 tok/s, code 115.9-119.2 tok/s | — | — |

- With the GPU to itself a pass is 7.03 s natively and a warm clip 73.8 s: WSL's HP Performance figures (7.0 s, 72.7 s)
  and within 7% of a GB10's pass. Earlier runs the same day, beside another session's Drama 3 engine in WSL, took 7.56-7.65
  s a pass (80.7 s warm, 122.7 s cold); the first-frame row is from those. Keep HP's Performance mode (myHP), plugged in.
- These rows are from engines the installer built from `a545f786` (H3, Bolt, Drama 3 rebuilt in 261 s with the
  toolchain already there); the first runs used builds of the same branch made by hand.
- The memory guard watched every job. On WDDM `cudaMemGetInfo` gives a process its own budget (it read 105.7 of
  113.1 GB free throughout while ComfyUI held 58 GB), so the guard holds NVML's dedicated counter to a floor
  (`DEDICATED_FLOOR_MB`) beside Windows' commit and low-memory signals. With another engine holding about 40 GB of
  WSL's RAM, Windows' low-memory signal stopped a video's load (commit 116.6 of 117.6 GB): run one stack at a time.
  The guard holds commit to a 4 GB floor rather than to `windows_memory.pressure`'s 0.5-second projection, which read
  a model load's single 21 GB commit as commit falling to nothing and stopped a load with 19 GB to spare.

### Every feature natively: what was run (2026-11-04, later the same day)

The same laptop and Studio (`windows-native-features`), the engines rebuilt by the installer from TensorFold
`studio-1.2.2` `1c8bdbfd`, every job queued through the Studio's own API (`/api/jobs`, `/api/voice/takes`,
`/api/music/takes`, `/api/finish/{id}/convert`, `/api/loras/{id}/install`). Wall time is the job's, queued to done. The
clip used by the actions is a FastH3 render of a generated scene (a man in a red jacket crossing a town square, 864x480,
124 frames: 113.9 s from a cold server at 4 passes).

| Job | Native Windows | DGX Spark |
|---|---:|---:|
| Find a person (SAM 3.1 on ComfyUI's nodes, Akatz Labs' green mask), the 5 s clip, ComfyUI restarted for it | **65.7 s** (finding and following him 41.9 s, the clean plate 0.1 s) | 36-40 s for the mask (README) |
| Foley (MMAudio large_44k_v2, 25 passes, kijai's nodes), the 5.17 s clip, ComfyUI restarted for it | **48.7 s** (the graph 31.4 s) | — |
| Finish (Tesseract 0.4.0, x64 emulation), two clips, a title card and a song under them, 11.7 s at 1080p 24 fps | **12.4 s** | — (a Spark finishes on a Mac) |
| Finish, two cuts, 6 s at 720p | 7.6 s | — |
| Finish -> After Effects project (Tesseract Converter 0.3.0) | done, a few seconds | — |
| MiniMax Music 3 on its TensorFold engine, a 30 s song, Drama 3 unloaded first | **80.9 s** (load 20.9 s; the song 57.9 s: words 32.6 s, sound 23.4 s, decode 1.9 s; 1.93x real time; 16.5 GB at the peak) | 60.3 s for the song, load ~16 s |
| The same song on ComfyUI's nodes (before this change) | 133.8 s | — |
| Fish Audio Drama 3 on the rebuilt engine, a 5.3 s sentence, engine loaded for it from a cold disk | 94.4 s (load 90.6 s; 3.45 s to speak it, 1.55x real time, first sound 204 ms) | 29-31 frames/s |
| LoRA hub: Turnaround (matlod, 63 MB) installed | done | — |

- Find a person and foley restart ComfyUI before they load (a server that rendered a clip starts again empty, as on a
  Spark). The node packs of all four actions and foley load in the Windows ComfyUI (its import report lists no failure).
- The Drama 3 load above is from a cold disk (the engines and their page cache had just been rebuilt); a warm load is
  34 s, as in the table before. The Studio no longer rewrites Windows paths for Drama 3: `1c8bdbfd` JSON-escapes the
  path it echoes (`88415e41`), and an engine from `a545f786` would now time out on its first take: rebuild it (the
  installer does, by its commit).
- MiniMax Music 3's own engine is 1.65x faster than ComfyUI's nodes on this laptop, and its song as fast as on a GB10;
  its load is slower (20.9 s against about 16 s).
- Not run here, for want of disk (the laptop's C: had 19.7 GB free, other models fill the rest): the turnaround sheet,
  upscale, remove a person and Character Swap need MiniMax H3 Ref2VA (21 GB in int8) and LoRA training needs 46 GB of
  files (FL2VA in bf16, the fp16 video encoder, ostris's adapter). Their code paths ran as far as the weights: the node
  packs load, SAM 3.1 finds and follows the person, the hub installs their LoRAs, the training venv installs
  (`windows-cuda-setup.py train`, 180 s) and bitsandbytes' 8-bit AdamW runs a step on the GPU; the Studio then refuses
  each with the command that installs the missing part. Measured on a DGX Spark they hold 28-53 GB (remove, swap) and
  about 55 GB (a LoRA run), inside the native pool's 113 GB.
- Tesseract Converter 0.3.0's Windows build makes the After Effects project but refuses the Premiere Pro one ("AEP
  artifact is not a bounded portable local path", with relative paths too): a limit of that build, not of the Studio.
  Resolve's FCPXML and EDL, written by the Studio itself, come with every finish.
- The installer, end to end: `-ToolchainOnly` into an empty folder fetched and checked NVIDIA's eight CUDA 13.4.1
  archives (0.79 GB) in 62.9 s, and that toolchain built `tf_h3.dll` and `music3-generate.exe` from an empty cache in
  78.5 s; a full run with the toolchain present (every part above, the models already there) took 284 s, the four
  engines rebuilt at the new commit; `setup-windows-finish.ps1` took 15.3 s.
