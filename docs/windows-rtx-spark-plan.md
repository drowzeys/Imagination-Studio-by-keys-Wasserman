# Native Windows RTX Spark: support and porting plan

Last updated: 2026-11-04.

Scope: native Windows 11 ARM64 on NVIDIA RTX Spark, not WSL or a Linux container.
This is a best-effort hobby project, with reviewed changes and route-specific validation.

**A bounded Windows preview tier runs on both the 48 GB development machine and the 64 GB test machine.
Full Mac/DGX feature parity is not established.** This tier uses guarded PyTorch/ComfyUI,
a small local writing brain, native media tools and Copilot CLI integration.

Upstream also reports a separate **native Windows CUDA tier on a 128 GB RTX Spark N1X**: ComfyUI and
TensorFold's Windows engines, with measured FastH3, Qwen-Image, Drama 3, Music 3 and Bolt results.
Its installation, restrictions and measurements are in
[N1X: Native Windows](rtx-spark-n1x.md#native-windows-the-native-cuda-tier). It is selected only with the
complete install and a measured CUDA pool of at least 90 GB (`app/windows_cuda.py`); the smaller machines
retain the preview tier. `scripts\app.ps1 -Preview` explicitly selects that tier.

Use the [preview setup/testing runbook](windows-rtx-spark-testing.md) for its installation and checks.
This document records current support and remaining work, not troubleshooting history.

## Hardware handoff at a glance

| Machine | Established result | What remains |
|---|---|---|
| 48 GB | Bounded preview planning, stills, sound, writing, media, graphics and lifecycle checks passed; exact scopes below | No qualification of the other generators on this tier |
| 64 GB | Same bounded feature tier passed; API and browser evidence distinguished below | Full interactive First AD apply/save and playback/download checks |
| 128 GB | Upstream native CUDA measurements are published in the N1X notes; not reproduced on our smaller machines | Reproduce the named native-tier scopes and qualify additional routes separately |

**The bounded baseline is complete, not the whole Windows port.** Untested software routes must not be
handed off as features that merely need 128 GB. The native tier's 90 GB CUDA-pool gate is distinct from
the preview's route-specific memory limits.

## Current support

The table below describes the **48/64 GB preview tier**. Each result applies only to the route and settings
shown. API and browser checks are identified separately; more installed RAM does not enable another route.

| Feature | Supported scope | 48 GB development machine | 64 GB test machine |
|---|---|---|---|
| Planning, Slate and manual boards | Fountain import, scene/shot/cast/dialogue links, references, save/reopen, shot lists | Browser flow verified | Real API flow verified; full interactive flow pending |
| Portable Slate films | Project, referenced media, original screenplay when available, captioned sheets and CSV/Markdown shot lists | Clean-folder browser import verified | Manifest hashes and API round trip verified with original board unavailable |
| SD-Turbo storyboard drafts | 512x512, one step, 1-6 sequential images, distinct captions and seeds | Output and browser workflow verified | One- and six-image outputs verified |
| Stable Audio Open effects | Five seconds, 100 passes, CFG 7; stereo 44.1 kHz PCM WAV | Output, normal queue and browser playback readiness verified | Output and normal queue verified |
| Qwen3-4B Q4_K_M writing | Text-only Slate assistance; one 4096-token slot, up to 1024 answer tokens | CUDA First AD response applied and saved | CUDA startup, title/logline and three-shot First AD response verified; interactive apply/save pending |
| Copilot CLI | Local Connection Check, This Studio tools and session continuity | Verified | Read-only `studio_state` turn and same-thread follow-up verified |
| FFmpeg media operations | Probe, frame extraction, trim/join export; 1-12 clips, ten minutes, even output sides up to 1920 | Mixed silent/audio browser export verified | Real API exports and full decode verified |
| Editor handoff | FCPXML, EDL and media ZIP | API/file checks passed | API/file checks passed; actual Windows Resolve import unverified on either machine |
| Beat detection | Local audio, at most ten minutes; no generation model | 120 BPM fixture and cache verified | 120 BPM fixture and cache verified |
| Motion graphics | Title, lower-third, kinetic, slideshow, music-video and explainer; up to 1280x720, 24 fps, ten seconds | Six templates verified | Six templates verified |
| Owned lifecycle | Warm reuse, model exclusivity, queued/running cancellation, unload and shutdown | Browser and process checks passed | Scoped checks passed, including submission during model switching |

Preview image and sound models run one at a time. Unsupported routes remain unavailable across the UI,
API and Studio tools; downloading their weights does not qualify them.

## Measured hardware

These are configuration-specific snapshots, not permanent capacities or promises for other machines.

| Measurement | 48 GB development machine | 64 GB test machine |
|---|---|---|
| Installed physical memory | 48 GiB | 64 GiB |
| Windows-visible RAM | About 20.8 GiB | 31.70 GiB |
| Dedicated GPU partition | 26 GiB | 30.50 GiB |
| CUDA allocation budget | 35.63 GiB | 45.59 GiB |
| NVIDIA driver used for current checks | 621.01 | 621.01 |
| Native Python / CUDA wheels | Python 3.13 ARM64; pinned Windows wheel set | Python 3.13.17 ARM64; same wheel set |

RTX Spark shares physical DRAM between a GPU carveout, shared GPU/system memory and CPU-only memory.
CUDA can report a larger budget than NVML's dedicated-memory figure, but shared allocations compete with
Windows and CPU staging. **Do not add host RAM, dedicated memory and the CUDA budget together.**
Commit headroom is virtual-memory backing, not additional physical or GPU memory.

Query the actual split and free budgets on each machine with
[`check-windows-capacity.ps1`](../scripts/check-windows-capacity.ps1). Background models can hold GPU memory
while GPU utilization is nearly zero. Admission reports required/free memory and measured GPU users of at
least 1 GiB, largest first; unavailable per-process usage is excluded rather than guessed.

## Testing evidence

The [dated test results](windows-rtx-spark-test-results.md) record our 48/64 GB checks, representative
timings and memory measurements without a troubleshooting diary. Sound's private commitment is close to
its process cap; neither extra capacity nor a passing small case qualifies a longer/larger route.
Output checks establish format and levels, not listening quality; idle checks are bounded observations.
For upstream's separate 128 GB native-tier evidence, use the N1X notes linked above.

## Runtime and safety constraints

These constraints describe the preview; the native CUDA tier has its own lifecycle and guard documented
in the N1X notes and `spark/comfy_win.py`.

- Keep UI and GPU environments separate. Use the Windows dependency locks; Linux wheels and carrier
  binaries are not native Windows artifacts.
- Keep the existing 12 GiB tensor-allocation, 12 GiB process-commit and 16 GiB owned-job caps for image/sound,
  plus the 2 GiB GPU reserve. Graphics has a separate 4 GiB owned-job cap. Do not raise limits after a refusal.
- Use Windows low-memory notifications and commitment signals; honor an explicit positive
  `SPARK_MEMORY_FLOOR_MB`. There is no inherited automatic 8 GB Linux host floor on Windows.
- Monitor the selected GPU's dedicated memory and the actual backend's CUDA headroom independently.
  Another process's CUDA free-memory reading is not the backend's budget.
- Credit only current private commitment and reserved tensors from the identity-validated owned backend,
  never file cache, unrelated processes or historical peaks.
- Launch and drain private process trees through retained handles and Windows job objects. Preserve
  monitoring deadlines, targeted cancellation and model exclusivity; never stop unrelated GPU applications.
- Qualification receipts match weights, rendering code and the physical/host/GPU split. Refresh them after
  relevant changes; an install audit or no-model startup does not qualify inference.
- Reuse installed Edge through the checked headless/private-profile probe. A successful render must not
  open windows in the user's normal browser session.

The implementation and tests are authoritative for these rules:
[`windows_memory.py`](../app/windows_memory.py), [`windows_process.py`](../app/windows_process.py),
[`windows_comfy.py`](../app/windows_comfy.py), [`windows_storyboard.py`](../app/windows_storyboard.py),
[`windows_sound.py`](../app/windows_sound.py) and the [Windows regression tests](../tests).

## Handoff: remaining work

| Work bucket | Routes / checks | Next owner needs to establish |
|---|---|---|
| Close preview validation gaps; not 128 GB work | Interactive First AD apply/save and media playback/download on 64 GB; actual Resolve import on either tested tier | Real actions preserve projects/media and outputs work in the destination application |
| Additional 48/64 GB routes | Qwen images/edits, FLUX3-K, MOSS, music, foley, transcription and pose/depth analysis | Dependencies, guarded output and capacity qualification at smaller-tier settings; some paths now exist in the upstream native tier, not in this preview |
| Reproduce 128 GB native results | The FastH3, Qwen-Image, Drama 3, Music 3 and Bolt scopes measured in the N1X notes | Full native install and actual budgets, then output/lifecycle checks at the published settings; do not infer other routes or sizes pass |
| Additional native-tier routes | Unmeasured Ref2VA/settings, model-based Library actions, swaps and MMAudio foley | Route-specific implementation/readiness where missing, then guarded qualification; these are not unlocked just by RAM |
| Separate advanced work | Worker orchestration, paired GPUs and training | Windows implementation and independent hardware qualification; not covered by the preview or a single 128 GB machine |

For a 128 GB handoff, follow [the native installation instructions](rtx-spark-n1x.md#install-one-shot-natively)
and the [tier-testing procedure](windows-rtx-spark-testing.md#5-handoff-to-a-128-gb-tester), keeping preview and
native results separate. Missing software is **blocked: Windows implementation**, not **insufficient RAM**.

The generic [`studio-check.py`](../scripts/studio-check.py) is not a blanket preview qualification: its
platform check accepts Mac/Spark and some cases attempt unsupported routes. Use the runbook's preview
checks and supported API cases. The official
[`studio-check-ui.cjs`](../scripts/studio-check-ui.cjs) blocks writes intentionally; it checks page rendering
and controls, not successful completion of the actions it blocks. Its 33-tab sweep passed on the 64 GB machine,
with injected blocked-write errors and confirmed export downloads classified separately from real failures.
WSL and Linux containers are not fallbacks for missing native Windows routes.

## Acceptance and references

A route is supported only after reproducible native installation, correct readiness/refusals, verified real
output, measured cold/warm memory and safe cancellation/failure/shutdown. API, UI and automation must agree
on availability. Preserve existing access controls, model licences and credits, and keep Mac/Linux behavior
unchanged. Never weaken a guard or count an unsupported/unperformed check as a pass.

| Information | Canonical location |
|---|---|
| Preview installation and repeatable checks | [Windows setup/testing runbook](windows-rtx-spark-testing.md) |
| Native CUDA installation and measurements | [N1X native tier](rtx-spark-n1x.md#native-windows-the-native-cuda-tier) |
| Dated measurements and published discussion | [Test results](windows-rtx-spark-test-results.md), [issue history and overview](windows-rtx-spark-issue.md) |
| Dependency pins | [UI requirements](../app/requirements-windows.txt), [GPU requirements](../app/requirements-windows-gpu.txt), [ComfyUI constraints](../app/requirements-windows-comfy-constraints.txt), [graphics lock](../scripts/windows-graphics/package-lock.json) |
| Preview model files, recipes and qualification | [Storyboards](../app/windows_storyboard.py), [sound](../app/windows_sound.py), [sound weights](../app/windows_sfx.py), [brain](../app/windows_brain.py) |
| Native media and graphics | [Media](../app/windows_media.py), [graphics setup](../scripts/setup-windows-graphics.ps1), [graphics checks](../scripts/windows-graphics-check.py) |
| Larger-route measurements on other platforms | [Memory guard](../spark/memory_guard.py), [RTX Pro 6000 notes](rtx-pro-6000.md), [RTX 5090 notes](rtx-5090.md) |

Official platform references:
[NVIDIA Windows ARM64 PyTorch](https://github.com/NVIDIA/pytorch-windows-ci),
[RTX Spark unified memory](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/uma/index.html),
[CUDA allocation and budgeting](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/uma/umacuda.html),
[hardware overview](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/overview.html).
