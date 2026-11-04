# Windows RTX Spark: preview setup and tier testing

Use a checkout containing the Windows port, native Windows ARM64 PowerShell, and the same model/runtime
pins on each machine. Sections 1-4 cover the bounded 48/64 GB preview; the separate
[128 GB native CUDA tier](rtx-spark-n1x.md#native-windows-the-native-cuda-tier) has its own installer and
measurements. This is not the Mac/DGX installer; do not use WSL as an installation workaround.
The [living plan](windows-rtx-spark-plan.md#current-support) records tested 48/64 GB scopes and a separate
[remaining-work handoff](windows-rtx-spark-plan.md#handoff-remaining-work). A larger memory inventory does
not enable another route by itself.

## 1. Prerequisites and installation locations

- Git and the official [Python install manager](https://www.python.org/downloads/windows/) (`py`) must be
  installed. Install mode now obtains missing Python 3.13 itself, selecting ARM64 on this machine.
  A legacy Python launcher cannot install runtimes; setup reports that explicitly.
- Graphics additionally requires a security-patched native ARM64 Node 22+ with npm and an installed Edge
  or Chromium. Graphics setup detects Node and installs missing Node through `winget` (`OpenJS.NodeJS.LTS`,
  ARM64). If installation is unavailable, it reports how to install Node and rerun setup.
- Stable Audio Open requires local Hugging Face sign-in and acceptance of its gated licence. That human
  account/licence decision is intentional; an agent must not invent consent or put tokens into commands or reports.
- If using a private package registry, check authentication and package availability if setup fails, then retry.

Runtimes, ComfyUI, and default downloaded weights live under
`$env:LOCALAPPDATA\ImaginationStudio`, outside the checkout. Studio media normally lives under
`$env:USERPROFILE\ImaginationStudio` (or `STUDIO_HOME`). Reports below belong in an explicit local folder,
not the repository. No model drive is assumed or searched by this procedure.
Check free disk space on the actual install/output volume before choosing optional downloads.

## 2. Install the UI and CUDA foundation

Run from the repository root in PowerShell:

```powershell
.\scripts\app.ps1 -Install -InstallOnly
.\scripts\setup-windows-gpu.ps1
.\scripts\setup-windows-comfy.ps1
```

If Windows refuses to load a script because script execution is disabled, use the README's process-scoped
invocation, for example `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\app.ps1 -Install -InstallOnly`.
Do not change machine-wide execution policy. The script cannot repair a policy refusal that happens before it
loads.

`-InstallOnly` installs UI dependencies and exits without starting a server; normal `app.ps1 -Install`
still starts the UI afterwards. GPU setup obtains native ARM64 Python 3.13 and the pinned CUDA wheels,
then executes tiny numerical/vision/audio checks. It downloads no model weights.
ComfyUI setup uses its pinned source and dependencies, starts a no-model backend, checks readiness, and
drains its own process tree. Stop the dependent path if any step fails; do not proceed with weight
downloads for an unusable backend. UI, brain and FFmpeg installation are independent of ComfyUI.

To recheck without installing dependencies:

```powershell
.\scripts\setup-windows-gpu.ps1 -Verify
.\scripts\setup-windows-comfy.ps1 -Verify -Guarded -HoldSeconds 30
```

These are live CUDA/lifecycle checks, not offline audits. Keep the existing memory guards, allocator/job
caps and reserves. Never stop unrelated GPU processes or change carveouts/pagefiles to make a test pass.

If dedicated GPU memory is insufficient, the refusal reports available and required GiB and queries the
selected GPU's compute and graphics processes through NVML. Only processes with at least 1 GiB of measured
dedicated usage are shown, largest first; smaller and unknown usage is excluded. When NVML withholds usage,
Windows performance counters supply per-process totals across adapters, explicitly labelled as such.
An empty or partial list does not mean memory is unused. Use Task Manager's **Dedicated GPU memory** column when attribution is
unavailable, unload unused models in their own applications, then retry. Studio never stops these processes.

## 3. Download only the routes being tested

| Route | Setup/download command | Separate check | Scope / licence |
|---|---|---|---|
| SD-Turbo draft images | `.\scripts\setup-windows-storyboard.ps1` | `.\scripts\check-windows-storyboard.ps1 -Count 1`, then `-Count 6` | About 2.6 GB FP16 components; Stability AI Community License; 512x512, one step, sequential stills only |
| Stable Audio Open | `.\scripts\setup-windows-sfx.ps1` | `.\scripts\check-windows-sound.ps1` | About 5.3 GB gated weights; Stability AI Community License; normal queue qualification is five seconds / 100 passes / CFG 7, not foley |
| Qwen3-4B writing | `.\scripts\setup-windows-brain.ps1` | `.\scripts\brain.ps1 -Check` | About 2.5 GB weights + 310 MB runtime archives; Qwen3 Apache-2.0, llama.cpp MIT; one 4096-token slot |
| FFmpeg/ffprobe | `.\scripts\setup-windows-ffmpeg.ps1` | `.\scripts\setup-windows-ffmpeg.ps1 -Verify` | Pinned ARM64 binaries, no model weights; verify actual media through the app afterwards |
| Motion graphics | `.\scripts\setup-windows-graphics.ps1` | Setup runs the six-template qualification | Locked HyperFrames/GSAP packages; uses installed browser, downloads no browser/model; requires Node first |

Storyboard, SFX and brain setup also accept `-Verify` for existing files. Brain `-Check` checks startup,
memory accounting and shutdown, **not text-generation correctness**. `-Check -Cpu` explicitly tests the CPU
backend; it is not an automatic fallback for a refused CUDA check.
Actual writing needs `.\scripts\brain.ps1` (or explicitly `-Cpu`) and `.\scripts\app.ps1 -LocalBrain`
in separate terminals, followed by a Slate First AD request; record the prompt, resulting shots and latency.
Close both foreground scripts normally when done.

For SFX, accept the Stable Audio Open terms on its Hugging Face model page first, then sign in locally:

```powershell
& (Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\hf.exe') auth login
.\scripts\setup-windows-sfx.ps1
```

Use the interactive sign-in, never a token argument or a saved credential in a report.

The older isolated SFX diagnostic is distinct from normal queue qualification:

```powershell
.\scripts\check-windows-sfx.ps1 -Calibrate
.\scripts\check-windows-sfx.ps1 -Calibrate -CancelDuringSampling
```

In the preview, do not enable Qwen Image, FLUX3-K, MOSS, music, foley, H3, Drama 3 or Bolt just by downloading their weights
through Linux scripts. Their current Windows routes/backends and guards require separate implementation
or qualification. Download size is not runtime memory. Existing model licences still apply.

## 4. Capture each machine separately

```powershell
$Reports = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\reports'
New-Item -ItemType Directory -Force $Reports | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
.\scripts\check-windows-capacity.ps1 -Output (Join-Path $Reports "capacity-$Stamp.json")
.\scripts\check-windows-install.ps1 -Components ui,gpu,brain,media
```

The capacity script records installed RAM, Windows-visible/free RAM, dedicated GPU total/free memory,
Windows/driver/runtime versions and CUDA's allocation budget. It runs only tiny CUDA checks, never a model,
and does not record host names, accounts, IP addresses, UUIDs or keys. Existing report files are not overwritten.
**Do not add host RAM to CUDA or dedicated-memory totals: the physical pool overlaps.**

Add `comfy,storyboard,sound,graphics` to the offline component list only when installed, or use `-All` to
find every missing optional component. Offline audit never installs, loads models, opens browsers or writes
qualification receipts. In a PowerShell session, comma-separated component names form an array; when calling
from another shell use `powershell -NoProfile -Command "& .\scripts\check-windows-install.ps1 -Components ui,gpu,brain,media"`,
not `powershell -File ... -Components ui,gpu,brain,media`.

Repeat the small 48 GB baseline before increasing one setting. Record output correctness, cold/warm elapsed
times, peak tensor/reserved/process/job memory, minimum host/GPU headroom, cancellation and owned shutdown.
Storyboard/Sound checks save their qualification/output reports under Studio's `renders\calibration`.
Keep separate reports for each hardware tier; changing render code or memory partitions invalidates receipts.
Use explicit labels: files verified, startup only, inference verified, refused, or not measured. A refusal is not
a render pass. Never replace a failed GPU test with a CPU result without naming the backend.

For UI validation, start `.\scripts\app.ps1`, open `http://127.0.0.1:7870`, import a small Fountain script,
save/reopen the film, and exercise each installed qualified route plus cancellation/unload. Inspect real outputs
(image dimensions, WAV duration/channels, MP4 streams/playback); do not infer correctness from a completed job.
Keep the access key out of reports. The general `studio-check.py` is not a blanket Windows qualification:
unsupported routes stay n/a with the app's reason, never a pass.

Measured hardware and route results are recorded in the
[dated test results](windows-rtx-spark-test-results.md); the [plan](windows-rtx-spark-plan.md#current-support)
remains the current support reference.

## 5. Handoff to a 128 GB tester

1. Select the tier explicitly. For the full native CUDA tier, use the
   [N1X native installer and requirements](rtx-spark-n1x.md#install-one-shot-natively), including its measured
   CUDA-pool gate. To reproduce the smaller preview instead, use sections 1-4 and launch with
   `.\scripts\app.ps1 -Preview`. Keep results from these tiers separate.
2. Reproduce the exact published settings for the selected tier, then verify real outputs, model switching,
   cancellation and owned shutdown. Record API versus actual browser checks. A preview baseline pass does
   not qualify native-tier models; upstream's published native results do not qualify different settings.
3. Use the plan's [work buckets](windows-rtx-spark-plan.md#handoff-remaining-work) before selecting further
   experiments. The native tier already ports some models that the preview does not offer.
   Missing Windows backends, dependencies or qualified guards are software blockers, not
   evidence that a workload ran out of memory. Do not start those routes through Linux installers or raise
   the existing baseline caps to try them.
4. For a route with a working Windows implementation and its own guarded test procedure, measure from the
   smallest supported case. Record exact model/settings, output checks, cold/warm peaks and cleanup.
   Update the current support table only for the scope actually passed; otherwise record the blocking reason.
