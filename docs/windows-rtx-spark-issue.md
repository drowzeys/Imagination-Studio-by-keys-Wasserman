# Windows RTX Spark: published issue and updates

Published discussion: [drowzeys/Imagination-Studio-by-keys-Wasserman#195](https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/issues/195).
The proposal and substantive posted update below are preserved from that discussion.
Use the [current plan](windows-rtx-spark-plan.md) for support status and the
[dated test results](windows-rtx-spark-test-results.md) for concise testing evidence.

## Overview - 2026-11-04 (local, not yet posted)

- The bounded Windows tier is now tested on both 48 GB and 64 GB RTX Spark machines: planning/portable
  projects, SD-Turbo drafts, five-second Stable Audio Open effects, Qwen3 writing, Copilot tools, native
  media exports, beat detection and six motion-graphics templates.
- The 64 GB checks cover real outputs, API workflows, model switching, cancellation and clean shutdown,
  plus the 33-tab read-only browser sweep. Full interactive First AD apply/save and playback/download
  remain unverified; actual Windows Resolve import remains unverified on either tier.
- Our local results cover 48/64 GB. Upstream now reports a separate
  [128 GB native CUDA tier](rtx-spark-n1x.md#native-windows-the-native-cuda-tier), including measured
  video, image, voice, music and Bolt jobs. The [handoff](windows-rtx-spark-plan.md#handoff-remaining-work)
  separates those scopes from unfinished smaller-tier work; untested preview routes are not features
  known to require 128 GB.
- Existing guards, model exclusivity, licences and access controls remain unchanged. This is a bounded
  feature tier, not full Mac/DGX parity. The linked plan and runbook are the current implementation references.

<details>
<summary>Published proposal - 2026-11-02 (historical, not current support)</summary>

# Exploratory hobby project: native Windows ARM64 / RTX Spark support

## Proposal

I'm exploring a native Windows ARM64 port for NVIDIA RTX Spark as a personal, best-effort, AI-driven hobby
project, starting with a 48 GB unified-memory RTX Spark machine and a limited feature set.

The 48 GB machine will remain my primary development box. I also have access to a 64 GB RTX Spark for testing,
so I'd like to validate both tiers rather than make the larger machine a development requirement.

I'm using AI coding agents for research, implementation and testing. Findings below distinguish executed checks
from source inspection, upstream reports and projections; AI-generated code and estimates aren't evidence of
working support on their own. I'll review the work and share reproducible results and known limitations.

This is not a request for maintainers to promise Windows support, and I'm not proposing to lower existing
memory guards or change working Mac/Linux behavior. A 128 GB RTX Spark would be a useful future validation
target, but I don't have results from one.

- Experimental fork: [ivberg/Imagination-Studio-by-keys-Wasserman](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman)
- Working branch: [port/windows-rtx-spark](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/tree/port/windows-rtx-spark)
- Findings and phased plan: [Windows RTX Spark plan](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/port/windows-rtx-spark/docs/windows-rtx-spark-plan.md)
- UI preview launcher: [scripts/app.ps1](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/port/windows-rtx-spark/scripts/app.ps1)
- Windows preview tests: [tests/test_windows_ui.py](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/port/windows-rtx-spark/tests/test_windows_ui.py)
- Scope: native Windows; WSL is not assumed to be a supported fallback.

Comments, existing work, test hardware and advice on the best integration seams would be welcome.

## What I've verified so far

- Native Windows ARM64 UI preview: project planning/saving, settings, uploads and media browsing.
- Focused local regression tests: 108 passed, 1 POSIX-only test skipped. Not the whole suite.
- Browser checks of Story, Library, Nodes, Machine and Settings.
- RTX Spark CUDA driver initialization, compute capability 12.1, and a driver-context memory query.
- CUDA reported about 35.6 GiB total allocation budget on this 48 GB machine. Dedicated GPU memory is a carveout
  of the unified pool; host RAM and CUDA memory must not be added as independent capacities.
- NVIDIA publishes native ARM64 CUDA-enabled PyTorch wheels for the Python version used.
- The pinned Comfy Kitchen/AIMDO packages have compatible ARM64 wheels.
- Export-name checks found all 68/76 function entries in the inspected pinned TensorFold CUDA ABI tables in the
  installed native driver. This is not a kernel, signature or inference validation.

No model generation, native engine build or 128 GB Windows memory/performance result has been verified here yet.
The 64 GB machine has not yet been inventoried or tested for this effort. No weights have been downloaded for
this work.

## Existing upstream work I'd like to reuse

I found that this is not a clean-sheet TensorFold Windows port:

- [ashhart/TensorFold#836](https://github.com/ashhart/TensorFold/pull/836) is merged and contains the existing
  Windows platform work.
- [ashhart/TensorFold#1010](https://github.com/ashhart/TensorFold/pull/1010) added ARM64/RTX Spark support.
  It was closed rather than directly merged; the maintainer says its increment was integrated in
  [5b1f1081271b](https://github.com/ashhart/TensorFold/commit/5b1f1081271b976453628706a22343af8c9abc87).
- TensorFold's [Windows guide at this Studio's Music pin](https://github.com/drowzeys/TensorFold/blob/8cd08c8350c02cf3d7d8eee5f1c1d9dfa22fa703/docs/WINDOWS.md)
  describes source builds and contributor RTX Spark runs, while explicitly noting the maintained-tree device results
  have not been reproduced.

Credit belongs to that upstream work and its contributors. Those native-server reports do not establish that
all Studio family generators already work on Windows.

## Concrete gaps identified

| Area | Finding |
|---|---|
| Music pin | Shared Windows runtime and build selection already present; generator still needs build/protocol/inference validation |
| H3 pin | Older Linux-only build selection, Linux driver/library names, direct POSIX mapping/clock calls |
| Drama pin | Older Linux CUDA loader and older family layout; needs a compatible platform backport or agreed pin update |
| Bolt | Shared server has Windows support, but the specialized LiLiCorr `libbolt` build uses Linux library output/flags |
| Studio runtime | Launch/cancel/shutdown, installers and readiness need native Windows integration |
| Memory guards | Need Windows host and WDDM/CUDA budget handling without double-counting shared memory |
| Optional packages | Checked OpenCV, CTranslate2, Cryptography and Tiktoken releases lack matching Python ARM64 wheels; isolate these gaps rather than blocking all features |

I haven't found a missing NVIDIA driver API that prevents starting the port. Actual CUDA toolkit/library and
kernel tests remain to be done.

## Experiments I'd like to try

1. Get advice on a suitable TensorFold revision and reuse its existing Windows abstractions.
2. Resolve/lock a separate Windows GPU environment and run tiny PyTorch operations.
3. Reproduce TensorFold host tests and CUDA smoke tests without large models.
4. Prove owned process-tree cancellation and conservative Windows memory admission.
5. Enable one low-memory workflow with real output, cold/warm peak and failure-path checks.
6. Grow the 48 GB tier through individually validated audio/image/media tools.
7. Repeat validated workflows on the available 64 GB machine, then cautiously measure additional routes that fit.
8. Seek a 128 GB machine to qualify heavier workflows separately.

These are exploratory goals, not a delivery roadmap. I'll work on them as time permits and share progress,
failures and reproducible findings. The linked plan's effort ranges are tentative estimates of active engineering
work, not calendar deadlines. I may narrow the scope or stop if the dependencies or memory requirements make a
route impractical.

## Questions for maintainers and collaborators

- Is there an existing Studio Windows branch or coordination issue I should join?
- Which TensorFold revision is preferred for Windows work? For older H3/Drama pins, would you prefer a narrow
  platform backport or a separately tested engine update?
- Are there family-specific Windows build/inference receipts for Music 3, Drama 3, H3 or the specialized Bolt
  backend that go beyond the general native-server reports?
- What are the preferred smallest change boundaries for readiness, process ownership and Windows memory admission?
- Would Windows host-only CI be welcome, with hardware tests remaining explicit and opt-in?
- Does anyone have a 128 GB RTX Spark for independent memory and lifecycle validation?

## Capacity expectations

This table estimates **memory suitability after the necessary Windows software work**, not current support.
For the same workload and comparable configuration, an expected fit at a smaller tier remains an expected fit
at larger tiers. More memory provides headroom; it doesn't introduce a new software uncertainty.

- **Expected fit:** no identified memory barrier at the stated workload size; not a verified Windows render.
- **Needs measurement:** loading peaks, settings or host/GPU budgets could determine whether it fits safely.
- **Beyond current budget:** the existing route's measured/estimated demands exceed the present safe envelope;
  not a claim that no future implementation could fit.

Verification is separate: only the 48 GB UI preview has been tested locally. All generation still needs Windows
dependency, correctness, memory and lifecycle validation, regardless of memory tier.

| Feature group | 48 GB: primary dev box | 64 GB: available test machine | 128 GB: hardware sought |
|---|---|---|---|
| Planning, Slate, settings, uploads, media browsing | Expected fit; UI verified | Expected fit | Expected fit |
| FFmpeg editing/export and motion graphics | Expected fit | Expected fit | Expected fit |
| Sound effects and short foley | Expected fit | Expected fit | Expected fit |
| Qwen images/edits | Needs measurement | Expected fit at small test sizes | Expected fit at those sizes |
| FLUX3-K / MOSS-Image | Needs measurement | Needs measurement | Expected fit at test sizes |
| YuE2 / MiniMax Music 3 | Needs measurement by route | Expected fit for short YuE2/int8 Music 3; measure other routes | Expected fit at those sizes |
| Drama 3 voice | Beyond current budget | Needs measurement | Expected fit |
| Bolt brain | Needs measurement; not initial target | Needs measurement | Expected fit |
| FastH3 video | Beyond current budget | Needs measurement; not initial target | Expected fit at measured sizes |
| H3 bf16 / Ref2VA / heavy upscale/remove workflows | Beyond current budget | Beyond current budget at measured sizes | Expected fit at measured sizes |
| Character Swap | Beyond current safe budget | Needs measurement with offload | Expected fit at measured sizes |
| LoRA training | Beyond current recipe's budget | Needs measurement; not initial target | Expected fit at current recipe size |
| Basic motion analysis / modest speech models | Expected fit; software dependencies unresolved | Expected fit; same software work | Expected fit; same software work |

The 64 GB test machine should help establish a useful middle tier, especially for image/music loading and model
switching. It does not automatically make H3 video or Drama voice safe: I'll query its actual Windows/CUDA budgets
and measure each route with reserves intact. Results from it won't be presented as evidence that a workload fits
the 48 GB development box.

The table is a conservative starting point, not a permanent limit for each memory tier. The CPU/GPU memory
split and available budgets are configuration-dependent; I'll record them with each result and revise the
expectations when measurements justify it, keeping host headroom and safety reserves intact.

I'll keep feature claims route- and hardware-specific and retain model licences, credits, access
controls, existing measurement limits, and explicit unavailable reasons.

</details>

<details>
<summary>Posted progress update - 2026-11-03 (historical)</summary>

## Progress update - 2026-11-03

Since the original proposal, this has progressed from feasibility work to a working, bounded native Windows ARM64 tier on the 48 GB NVIDIA RTX Spark.

**Draft implementation PR: #198.** It includes the synced main at `8eef55a`, integrated with a normal merge. This remains experimental, best-effort work, not a claim of Mac/DGX Spark parity.

### What now works

| Area | Verified Windows scope |
|---|---|
| Scripts and planning | Fountain/suite imports, Slate scene/shot/cast/dialogue links, manual boards, save/reopen and shot-list exports. Final Draft still needs export to Fountain first. |
| Portable Slate projects | ZIP with project, referenced media, source screenplay when available, captioned contact sheets and CSV/Markdown shot lists. Clean-folder reimport was verified after removing the source folder. |
| Generated storyboard drafts | SD-Turbo, 512x512, one pass, 1-6 sequential pictures with individual captions/seeds. Measured peak GPU tensors 3.01 GiB and reserved memory 3.31 GiB; a two-caption warm job took 0.84 seconds, excluding startup. |
| Sound effects | Stable Audio Open's fixed five-second recipe: 100 passes, CFG 7, stereo 44.1 kHz WAV. Actual duration is 5.01551 seconds. Process commitment reached 11.99 GiB, close to the 12 GiB cap, so longer effects are not enabled. |
| Writing and agents | Optional Qwen3-4B Q4_K_M text-only Slate assistance, plus optional Copilot CLI with scoped This Studio tools. Copilot uses the person's plan/requests; terminal login remains CLI-owned. |
| CPU media tools | Native ARM64 FFmpeg/ffprobe for probing, frame extraction and bounded MP4 trim/join exports: 1-12 clips, up to ten minutes, even output sides up to 1920 pixels. |
| Motion graphics | Title, lower-third, kinetic type, slideshow, music-video and explainer, using existing text/media. All six rendered at 1280x720, 24 fps and ten seconds under a 4 GiB owned-process commitment cap. |
| Beat detection | Existing NumPy/FFmpeg algorithm, not a generation model, for local audio up to ten minutes. A synthetic 120 BPM track passed the 2 BPM tolerance; cached results now obey the same duration limit. |
| Editor handoff | Existing FCPXML, EDL and media-ZIP routes, with fixes for Windows drive URLs, escaped filenames and case-colliding basenames. API/file structure and trim timing are tested; an actual Windows Resolve import is not yet verified. |

### Runtime and installation safeguards

- Image and sound jobs share one privately owned, guarded ComfyUI backend. Same-kind jobs stay warm; switching kinds drains the old backend before loading the next.
- Running/queued cancellation, explicit unload, shutdown and restart were checked. A 314.37-second idle/recovery interval was observed; this is not an indefinite-idle guarantee.
- Image/sound retain 12/12/16 GiB allocator/process/job caps and a 2 GiB GPU reserve, plus Windows memory-pressure/commit monitoring. Existing Mac/Linux guards were not lowered.
- The graphics browser probe was corrected after an initial test opened unwanted Edge New Tabs. The pinned Windows-only patch uses a headless temporary profile. The completed six-template check added **zero visible browser windows**.
- Per-user setup and an offline installation audit reuse verified caches. All eight installed components passed the audit without downloading weights, starting a model/browser or changing qualifications. No weights or runtime executables are bundled in the repo.
- Credits, licences, access-key checks and loopback-only launch defaults are retained.

### Validation and PR status

The latest post-merge focused local run passed **365 tests**, with one explicit Windows file-symlink privilege skip. Final compile checks covered **63 Python modules and 15 JavaScript files**. These are local results, not a full hosted/Linux/GPU-suite claim.

The existing Ubuntu CI job is retained, and a public-repository-only, model-free `windows-11-arm` job is added. The current [hosted tests run](https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/actions/runs/41729991119) reports **`action_required`**; upstream action/approval is still needed before treating hosted CI as verified.

H3/Ref2VA video generation, larger image/edit routes, foley, Drama 3/Music generators, Windows paired/local/remote GPU workers, automatic model/source installation, Nodes execution, automatic rough cuts, soundtrack jobs and transcription/captions remain unqualified. The 48 GB results do not qualify the 64/128 GB tiers or other GPUs. Upstream's new FLUX3-K/YuE2 Home chips, paired-card/HDR features and credits are preserved, not silently enabled on Windows.

### Evidence

The [committed support matrix and measured findings](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/8dc93c09e9c58844e38a7baa9cc157787b79925d/docs/windows-rtx-spark-plan.md#executive-assessment) substantiate the scope above; sections 20-29 cover the implemented routes, lifecycle, handoff, graphics, offline verification and integration. The [living plan](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/port/windows-rtx-spark/docs/windows-rtx-spark-plan.md) and [Windows README instructions](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/port/windows-rtx-spark/README.md#windows-limited-preview) remain the current implementation references.

Leaving the issue open while the draft PR and remaining platform work are reviewed.

</details>
