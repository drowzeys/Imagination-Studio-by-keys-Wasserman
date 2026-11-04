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
   installs less. It first asks once for a **Hugging Face read token** (https://huggingface.co/settings/tokens;
   accept the FLUX3-K and Stable Audio Open licences with that account) and saves it in Ubuntu, so the gated models
   download in the same run (`-HfToken hf_...` passes it, `-NoHfToken` skips them);
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

## An LLM on the N1X: Performance against power saving

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

## TensorFold on the N1X: in WSL and native on Windows

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
