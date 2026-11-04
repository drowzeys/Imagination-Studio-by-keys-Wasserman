# Imagination Studio on an NVIDIA RTX Spark N1X laptop

The RTX Spark N1X (Windows 11 on Arm) has the same GPU architecture as the DGX Spark's GB10: Blackwell, sm_121,
6144 CUDA cores. The Studio runs the **DGX Spark recipe unchanged inside the laptop's WSL2 Ubuntu**, so it is the
whole Studio (every part `oneshot-setup-spark.sh --all` installs), and a clip comes out **byte for byte the DGX Spark's**.
What differs is the laptop's memory split and its power budget.

## Install: one shot

In an administrator PowerShell on the laptop:

```powershell
irm https://raw.githubusercontent.com/drowzeys/Imagination-Studio-by-keys-Wasserman/main/oneshot-setup-rtx-spark-n1x.ps1 -OutFile $env:TEMP\n1x.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\n1x.ps1
```

`oneshot-setup-rtx-spark-n1x.ps1`:

1. checks the machine (ARM64, an N1X) and WSL2 with Ubuntu (it installs them when missing; a first Ubuntu asks for its
   Linux user, then run the script again), turns systemd on, and sets `.wslconfig` so WSL can use the RAM Windows has;
2. clones the Studio into Ubuntu (`~/src/Imagination-Studio-by-keys-Wasserman`) and runs `oneshot-setup-rtx-spark-n1x.sh
   --yes --all` there: the DGX Spark recipe with `STUDIO_N1X=1`. Run again, it updates (`--upgrade`). `-Parts "--sound"`
   installs less. Gated models (FLUX3-K, Stable Audio Open) need `hf auth login` inside Ubuntu first;
3. makes **Imagination Studio** (start) and **Stop Imagination Studio** with the Studio's icon, on the Desktop and in Start,
   and **pins Imagination Studio to the taskbar** (Windows 11's taskbar layout, appended to your own pins; it shows after
   Explorer restarts, or at the next sign-in);
4. keeps the laptop awake for renders: no sleep or hibernate plugged in, the lid does nothing, Windows on Best
   performance, the Wi-Fi adapter never powered down (`-NeverSleepOnBattery` for battery too, `-NoPower` to skip);
5. optional: `-StartAtLogon` (starts the Studio at sign-in), `-EnableSsh -AuthorizedKey "ssh-ed25519 ..."` (another
   machine reaches the laptop over SSH on the home network).

## Start and stop

- **Start:** click Imagination Studio on the taskbar. A small window opens (minimized) and the Studio opens in the
  browser at `http://127.0.0.1:7870` when it is ready. Clicked while it runs, it only opens the page.
- **Stop:** close that window, or run Stop Imagination Studio (it stops the Studio and its ComfyUI, giving the GPU's
  memory back).
- Why a window: WSL shuts Ubuntu down shortly after its last Windows window closes, and everything in it with it. The
  launcher's window is what keeps the Studio alive; a Studio started over SSH alone stops when the session ends.

## Memory

The firmware gives the GPU a **fixed carve-out**: 96 of 128 GB on an HP OmniBook X 14, leaving Windows and WSL about
30 GB. The laptop's BIOS has no setting for that split. The models live in the carve-out, so it suits the Studio. With
`STUDIO_N1X=1` (`spark/memory_guard.py`):

- the card's whole carve-out is the pool: WSL's nvidia-smi names no process's memory, so the Studio's own loaded models
  are counted as the job's (a clip, or a long take's part 2, no longer refuses because the models are resident);
- WSL's ~30 GB of RAM does not hold a job back;
- the checks are relaxed: a job the estimate says may not fit is **flagged** (`[tensorfold] memory warning: ...`) and
  tried, and fails with the error if the memory really runs out (`STUDIO_MEMORY_GUARD=warn`, the N1X default; `strict`
  refuses as on a DGX Spark, where running out can take the machine down).

## Speed: performance mode against the DGX Spark

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

**1376x768, 192 frames (8 s), a Studio video job from a first frame, from a cold start (models loaded first):**

| Machine and mode | The whole job | Of it, the render | GPU while rendering |
|---|---|---|---|
| DGX Spark (GB10) | 625.6 s | 410.1 s | — |
| N1X, out of Performance (Smart Sense had switched it) | 736.1 s | 658.8 s | ~1400 MHz, ~31 W |

- In HP's Performance mode the N1X is within about 6% of the DGX Spark a pass; in its default mode about 36% slower.
- The limit is the laptop's power budget, not heat or WSL: the GPU is 96% busy, under 75 °C, and nvidia-smi reports
  software power capping; the fan made no difference. The GPU's own power limit is not settable (N/A).
- HP's Smart Sense can leave Performance by itself: check myHP before a long render, keep it plugged in and the lid
  open (many laptops cut power with the lid shut).
- The whole-job times include loading the models and the QA gate's look at the take, which differ between runs.
