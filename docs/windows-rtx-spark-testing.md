# Windows RTX Spark: native setup and feature testing

Use a checkout containing the Windows port, native Windows ARM64 PowerShell, and the same model/runtime
pins on each machine. Sections 1-4 cover the bounded features measured on 48/64 GB; broader native model
[measurements](rtx-spark-n1x.md#native-windows-the-native-cuda-tier) remain scoped to their tested 128 GB machine.
Both use one Windows runtime and per-feature routing. This is not the Mac/DGX installer; do not use WSL as an installation workaround.
The page shows `Loading Studio...` until its initial state arrives; early background-panel updates must not
render a model-dependent room before that state is available.
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

If Windows refuses to load a script because script execution is disabled, use the [Windows guide](windows.md)'s process-scoped
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
| MMAudio foley | `.\scripts\setup-windows-foley.ps1` | **Machine > Feature checks > Sound > MMAudio foley > Check** | About 5.2 GiB weights, CC-BY-NC 4.0; isolated runtime; bounded 1-5 s / 24 fps clips, 25 passes / CFG 4.5 |
| Qwen3-4B writing | `.\scripts\setup-windows-brain.ps1` | `.\scripts\brain.ps1 -Check` | About 2.5 GB weights + 310 MB runtime archives; Qwen3 Apache-2.0, llama.cpp MIT; one 4096-token slot |
| FFmpeg/ffprobe | `.\scripts\setup-windows-ffmpeg.ps1` | `.\scripts\setup-windows-ffmpeg.ps1 -Verify` | Pinned ARM64 binaries, no model weights; verify actual media through the app afterwards |
| Motion graphics | `.\scripts\setup-windows-graphics.ps1` | Setup runs the six-template qualification | Locked HyperFrames/GSAP packages; uses installed browser, downloads no browser/model; requires Node first |
| HDR grade | Existing FFmpeg/ffprobe and UI environment | `.\scripts\check-windows-features.ps1 -Feature hlg` | CPU only, no model download; ten seconds, at most 1280x720, 24 fps |
| Local Finish | `.\scripts\setup-windows-finish.ps1` | `.\scripts\check-windows-features.ps1 -Feature finish` | About 165 MB of tools/fonts; Mirage's Tesseract terms, converter MIT; bounded 720p/24 fps cut |

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

Do not enable Qwen Image, FLUX3-K, MOSS, music, foley, H3, Drama 3 or Bolt just by downloading their weights
through Linux scripts. Their current Windows routes/backends and guards require separate implementation
or qualification. Download size is not runtime memory. Existing model licences still apply.

### Independent HDR and Finish qualification

Neither route needs H3, the GPU environment or `cuda-tier.json`. Install the UI and native media tools first.
For Finish, install the pinned Tesseract release/fonts with the table's command. Tesseract is not open source:
Mirage's terms exclude commercial use by a business with at least USD 1 million yearly revenue without agreement.
Setup verifies pinned archives and leaves its usage-count setting unchanged.

```powershell
.\scripts\check-windows-features.ps1 -Feature all
.\scripts\app.ps1 -Preview
```

Open **Machine > Feature checks** to run either check individually, cancel it, review saved evidence
or download the saved report. This is a separate, collapsible section, closed by default; qualification
badges expand it and focus the relevant card. The page never starts checks automatically. The CLI's `-Feature all`
remains an optional way to run both; the UI's report download only reads saved results.
In **Apps**, a qualification badge opens the exact Machine card without starting a test.
Qualified HDR is available as **keys-HDR LORA** in Apps or under a Library video; Finish is in
**Edit > Finish**, the Timeline's Finish panel, or **Library > Finish these** for selected clips.
Finish's header badge replaces the long qualification paragraph; expand **Qualified settings** for limits.
The **MMAudio foley** app has its own Sound check and opens Foley when its local evidence matches.

Qualification runs real outputs under a 4 GiB owned-job cap and a host-memory watchdog, and drains its own
processes. Receipts live in `%LOCALAPPDATA%\ImaginationStudio\qualification`, not the repository; do not copy
them between machines or edit them. Model/recipe changes, hardware-profile changes, core ABI-family changes
and explicit compatibility-contract changes need requalification. Routine updates retain the original dated
evidence, with a runtime-update note; no render runs automatically. Unsuccessful reruns retain
the last successful evidence and save the new attempt separately; no test is automatically repeated.
`/api/state`, `/api/actions` and `/api/finish` report the applicable readiness. Current headroom is checked
at submission and execution; a qualified feature can still refuse a job while memory is busy.

HDR takes an SDR clip up to ten seconds, 1280x720 and 24 fps; verify both HLG/SDR files and retained sound.
Finish takes local source clips within those bounds, up to 12 cuts totalling ten seconds including its
3.5-second title. Music/narration sources must also be at most ten seconds. Only 720p/24 fps is enabled;
transcription, remote Finish and Premiere/After Effects conversion remain disabled in this profile.
Check actual Library/Timeline submission, playback/download, oversized-input refusal, cancellation and shutdown.
The [dated results](windows-rtx-spark-test-results.md#2026-11-04-independent-features-after-the-native-windows-merge)
include a remaining Tesseract reliability caveat; do not silently retry that error.

### Independent Foley qualification

Run `.\scripts\setup-windows-foley.ps1`, then Check on its Machine card. Setup uses
`%LOCALAPPDATA%\ImaginationStudio\foley-gpu-venv`, sharing the pinned ComfyUI source and weights but not the
baseline GPU environment's packages. `-DependenciesOnly` installs/checks packages without weights or inference;
`-Verify -DependenciesOnly` is read-only. Repeating setup reuses completed core/node dependencies.
Both dependency stages reuse the GPU manifest's NVIDIA wheel source and existing configured feed authentication.
If `soxr` needs a source build, setup detects/reuses the ARM64 compiler or installs the supported Build Tools;
that build does not mean a feed-authentication failure.

The check renders five seconds with source sound retained and one second with replacement. It checks mono
44.1 kHz audio, duration, levels, full MP4 decode and unchanged decoded picture frames. Normal Foley accepts
1-5 s local 24 fps clips up to 1920x1080 whose 360-row analysis width is at most 660; it copies the original
video stream and bounds/pads the generated track to the source duration. Analysis is not an output upscale.
The recipe stays at 25 passes / CFG 4.5 under the unchanged 12/12/16 GiB tensor/process/job caps.
WDDM counters are bound to the selected CUDA UUID/LUID, with OS signals, two-second freshness and the
2 GiB dedicated reserve. They are not another CUDA context's budget. No stale-data success fallback is allowed.
Check queued/running cancellation and that cancelled jobs publish no take. Format/level checks do not
establish listening quality or support for larger/longer inputs.

### Baseline cards and update policy

The qualification switch controls gating and badges, not visibility of Feature checks. Below 128 GiB installed
RAM it is on/locked; at 128 GiB+ it defaults off and may be enabled. Change it while the queue is idle.
Individual implemented checks can run with gating off. Off never disables model/runtime prerequisites,
recipe constraints or memory guards; saved results still mean only their measured recipe.
Native image candidate cards show unknown Windows peak memory until a real measurement exists.
Do not treat model download size or a different platform's measurement as a verified Windows minimum.

Inspect setup dependencies without installing, downloading or probing:

```powershell
.\scripts\setup-windows-cuda.ps1 -Parts image -PlanOnly
```

An image-only install does not build H3 or obtain its compiler/CUDA toolkit. Requested native engines obtain
their own dependencies; CPU Finish alone does not install the GPU runtime. `-Preview` is now a launch alias,
not a selection of another Windows product. H3's own DLL/capacity guard remains unchanged.

The Image, Sound and Motion graphics groups also expose the existing **SD-Turbo storyboard drafts**,
**Stable Audio Open effects**, and **Motion graphics** checks. Their saved receipts are reused, including
legacy receipts whose update time is labelled as the evidence-file time rather than a newly performed check.
Rerun queues six sequential image drafts, the fixed five-second effect, or all six graphics templates.
Setup commands install dependencies only when requested. An unsuccessful/cancelled rerun does not delete a pass.
GPU checks drain Studio's warm image/sound backend before admission; models started outside Studio are not stopped.

Balanced update matching is the default. File hashes, timestamps and dependency versions record the tested
runtime; ordinary changes do not by themselves revoke qualification. GPU Python ABI, Torch/NumPy/CUDA major
families, model identity, recipe, hardware profile and explicit execution-contract boundaries are still checked.
Changed model-file metadata is checksum-verified rather than treated as proof of different weights.
For opt-in diagnostic strictness, set `$env:STUDIO_QUALIFICATION_STRICT = '1'` before launching Studio.
Strict mode requires exact matching and may require a new check, including for older receipts without full
runtime provenance. Remove that variable and restart to return to balanced matching; no saved pass is erased.

If a guarded ComfyUI check fails and its temporary workspace is also locked during cleanup, the original
startup/render error remains the failure; the log records the cleanup error and retained workspace path.
A cleanup failure after an otherwise successful check still fails the check. Neither case relaxes its
startup deadline or memory guards.

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
   experiments. The merged native tier already ports the model-based Library actions, swaps, foley,
   Finish and training environment that the preview does not offer automatically.
   Missing Windows backends, dependencies or qualified guards are software blockers, not
   evidence that a workload ran out of memory. Do not start those routes through Linux installers or raise
   the existing baseline caps to try them.
4. For a route with a working Windows implementation and its own guarded test procedure, measure from the
   smallest supported case. Record exact model/settings, output checks, cold/warm peaks and cleanup.
   Update the current support table only for the scope actually passed; otherwise record the blocking reason.
   An environment install or optimizer step is not a completed training run; an upstream native result is
   not a local smaller-tier result. Follow the [progressive qualification design](windows-rtx-spark-plan.md#remaining-progressive-qualification-design)
   when extending enablement instead of bypassing the full-tier gate.
