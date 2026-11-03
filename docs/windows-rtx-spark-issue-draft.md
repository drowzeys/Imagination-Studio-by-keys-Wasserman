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

---

## Update: 2026-11-03 - measured Windows feature tier

The original posted text above is preserved as the initial assessment. This appended update records later
findings; it does not edit the posted GitHub issue automatically. The maintained
[Windows RTX Spark plan](windows-rtx-spark-plan.md) contains the current support tables and detailed measurements.

### New verified results on the 48 GB machine

- The separate native ARM64 CUDA PyTorch environment passes math/vision/audio operations, and the pinned
  ComfyUI dependency set resolved as native wheels. This does not qualify every optional node/package;
  the CTranslate2 transcription packaging gap remains.
- SD-Turbo storyboard drafts: 512x512, one step, up to six sequential different captions with per-shot seeds.
  Peak GPU tensors were 3.01 GiB; a warm two-caption job took 0.84 s, excluding initial startup.
- Stable Audio Open: five seconds, 100 passes, CFG 7; exact output 5.01551 s, stereo 44.1 kHz PCM WAV,
  available in Sound and Library. Peak GPU tensors were 6.53 GiB; private process commitment about
  11.99 GiB, close to its unchanged 12 GiB cap.
- Native ARM64 FFmpeg/ffprobe: per-user verified installation, probing/frame extraction, and local trim/join
  export bounded to 1-12 clips, ten minutes total, and even output sides up to 1920 pixels.
- Qwen3-4B Q4_K_M: a text-only Slate writing brain with one 4096-token context and 1024-token answer limit.
- Native Copilot CLI: terminal sign-in, explicit Check, scoped This Studio calls and session continuity.
  Its requests use the person's plan; Studio never requests or stores a token. The UI now permits Check
  while sign-in status is unknown, since the CLI has no auth-status command.
- Real browser checks covered Fountain -> two generated boards -> Slate, scene edit/save/reopen, a decoded
  sound player, and a decoded 2.0 s, 1344x768 trim/join export from silent and audio sources. A rapid
  timeline-add race found during the pass was fixed.
- Warm reuse after 314.37 seconds idle, running/queued cancellation with no published successful take,
  and real Ctrl+Break shutdown/restart with zero owned descendants were verified. This is not indefinite
  idle qualification or a listening-quality review.
- NVIDIA driver 621.01 was installed and the machine rebooted; the initial inventory used 617.42.
  An earlier monitoring timeout was not reproduced in the bounded post-reboot check, not proven fixed forever.

Approved weights were downloaded outside Git; none are redistributed. Image/sound jobs retain 12/12/16 GiB
allocator/process/job caps and the 2 GiB GPU reserve. Incompatible image and sound models are drained before
switching kinds. No native TensorFold family engine build/inference, video generation, foley, or 64/128 GB
workflow result is claimed.

### Updated support versus projected fit

Only the named 48 GB routes/settings below are verified. The 64 GB machine is still uninventoried/untested,
and 128 GB hardware remains unavailable; their entries are capacity projections, not measured results.

| Route | 48 GB: current verified scope | 64 GB: projected fit | 128 GB: projected fit |
|---|---|---|---|
| Planning, Slate, script imports and manual boards | Verified model-free flows | Expected fit | Expected fit |
| FFmpeg probing/frame extraction/trim-export | Verified bounded CPU routes | Expected fit at those settings | Expected fit at those settings |
| SD-Turbo storyboards | Verified 512x512, 1-6 sequential drafts | Expected fit at those settings | Expected fit at those settings |
| Stable Audio Open sound effects | Verified five seconds / 100 passes / CFG 7 | Expected fit at those settings | Expected fit at those settings |
| Qwen3-4B Q4_K_M writing | Verified text-only Slate, 4096-token slot | Expected fit at those settings | Expected fit at those settings |
| Copilot CLI Studio tools | Verified local CLI + This Studio | Expected fit | Expected fit |
| Motion graphics, foley, larger image/music routes | Still unqualified | Need route-specific validation | Need route-specific validation |

The heavy-model capacity estimates in the original proposal remain projections, not enabled Windows routes.

## Update: 2026-11-03 - portable film handoff

A CPU-only portable Slate ZIP now carries project data, referenced media, an original screenplay when
available, captioned contact sheets and CSV/Markdown shot lists. It is prepared from Bible or Deliver and
downloaded as a file, without keeping the archive in browser memory.

A disposable two-scene film was exported, its source Studio folder removed, and its ZIP imported through
the UI into a clean Studio folder. Both board images loaded there; the included audio/script were retrievable
and project links were preserved. No GPU jobs were needed. Missing media fails explicitly; the original
screenplay is optional and its absence is reported. The existing ZIP limits/path checks and manifest
size/SHA-256 verification apply. This qualifies the named handoff, not broader media/model support.
## Update: 2026-11-03 - bounded motion graphics and capability cleanup

The limited Windows tier now includes locally checked titles, lower thirds and short slideshows:
at most 1280x720, 24 fps and ten seconds. All three rendered at that maximum in 17.05-20.55 seconds,
with 887.7-926.6 MiB peak process-tree commitment under a separate 4 GiB cap. The lower-third check
retained source audio. This reuses installed Node and Microsoft Edge, with locked HyperFrames/GSAP
dependencies; no model or browser downloads. Captions/transcription and other graphics templates
remain unqualified.

Home, engine cards, unqualified rooms and the MCP catalog now reflect Windows route capabilities.
The worker overview no longer advertises unsupported planning, and a selected, installed Copilot
is not asked to reconnect merely because its session-only verification reset. Its saved choices and
CLI-owned terminal login remain separate.

Focused capability/graphics/page/MCP/Windows API/credits/privacy checks: 121 passed, two explicit
skips. Existing model guards and Linux/Mac limits are unchanged; no paid Windows CI was provisioned.
The real Graphics UI/queue also produced a browser-decoded title and cancelled a running render without
publishing an output. Disposable test data was removed; existing models and saved demos were preserved.

### Follow-up: browser launch isolation

The first graphics qualification missed HyperFrames' unisolated `msedge.exe --version` probe, which
can open a New Tab in the existing Windows Edge session. That explains unwanted windows from the
repeated checks, despite successful rendered output. The qualification was invalidated.

A checked Windows-only compatibility patch now probes through headless Puppeteer with a temporary
profile and closes that browser in `finally`; unpatched builds are refused. All three maximum-bound
renders passed again with **zero new visible browser windows**. Qualification now checks that explicitly.
No existing user browser windows were closed and no browser or model download was required.

## Update: 2026-11-03 - PR preparation and affordable ARM64 CI

The branch now has an offline installation audit: UI-only by default, selected optional components or
all eight already-installed components. It checks dependency pins, clean ComfyUI source and cached
model/runtime hashes without importing torch, starting a browser/model, downloading files or changing
render qualifications. All eight passed on the current installation; no weights were downloaded again.

A model-free `windows-11-arm` GitHub Actions job is configured alongside the unchanged Ubuntu suite.
GitHub lists this standard runner as free for public repositories; private repositories explicitly skip
the new job. It uses native ARM64 Python/Node and tests CPU/API/page/owned-process behavior, not RTX/CUDA
rendering. A manual workflow trigger allows checking a pushed branch before a PR. Hosted/Linux results
remain pending; local Windows checks must not be presented as a hosted or Linux pass.

The same lightweight CI dependencies were installed into a disposable ARM64 environment using cached
wheels. Related test-fixture/harness regressions were fixed, including keeping native Windows process
integration out of Linux runs while retaining portable validation tests. The PR merge-base diff was
checked separately from newer target-tip changes; those upstream changes were not reverted.

Final clean-environment local result: **433 passed, three explicit skips** (optional real render,
unavailable symlink privilege and a POSIX-only CLI test). Workflow YAML and PowerShell commands parsed
successfully. No hosted Actions run, GPU/render qualification or Linux execution is claimed by that result.

## Update: 2026-11-03 - existing generic CPU features

Enabled the existing NumPy/FFmpeg beat finder for local audio up to ten minutes, plus kinetic type,
music-video edits and explainers in the existing renderer. No new model downloads or algorithms:
six graphics templates now share the same 1280x720/24 fps/ten-second bounds and 4 GiB process-tree cap.
The completed six-template check added zero visible browser windows. The new templates took
10.82-20.83 seconds and peaked at 899.7-934.4 MiB of process-tree commitment.

Graphics can use uploaded audio without a Music/Voice generator. Captions/transcription, automatic rough
cuts, Nodes execution and soundtrack jobs remain disabled. A synthetic 120 BPM click track checked the
existing beat finder, including canonical paths and cache reuse.

Existing FCPXML/EDL/media-ZIP routes were checked and received small fixes for case-colliding filenames
and Windows drive-letter/escaped file URLs. Media and trim timing were verified at endpoint/file level;
an actual Windows Resolve import is not claimed. No cross-platform XML importer was added.
Focused Windows/shared feature, page, export, credits and privacy tests: **325 passed, two explicit skips**.
The six actual graphics renders were checked separately; no hosted CI result is implied.

Pre-PR edge-case follow-up: cached beat results now obey the same Windows ten-minute limit as freshly
decoded audio. Exactly 600 seconds is accepted; longer or invalid cached durations are refused explicitly.
The existing non-Windows cache behavior is unchanged.
The focused cache/shared-page/credits/privacy follow-up passed 52 tests without skips.
