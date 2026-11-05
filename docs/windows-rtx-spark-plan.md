# Native Windows RTX Spark: support and porting plan

Last updated: 2026-11-04.

Scope: native Windows 11 ARM64 on NVIDIA RTX Spark, not WSL or a Linux container.
This is a best-effort hobby project, with reviewed changes and route-specific validation.

**Native Windows runs bounded, individually qualified features on the 48 GB development machine and
the 64 GB test machine. Full Mac/DGX feature parity is not established.** It uses guarded PyTorch/ComfyUI,
a small local writing brain, native media tools and Copilot CLI integration.

Upstream also reports **native Windows CUDA results on a 128 GB RTX Spark N1X**: ComfyUI and
TensorFold's Windows engines, with measured FastH3, Qwen-Image, Drama 3, Music 3, Bolt, person-mask,
foley and Finish results.
Its installation, restrictions and measurements are in
[N1X: Native Windows](rtx-spark-n1x.md#native-windows-the-native-cuda-tier). There is now one Windows runtime:
installed backends are selected per feature/model rather than by a global H3-based tier decision.
`scripts\app.ps1 -Preview` is a compatibility alias, not a different product. The common CUDA/ComfyUI core
does not require H3; H3 keeps its own engine and 90,000 MiB recorded CUDA-pool safety policy.
Availability depends on prerequisites, the selected recipe and live admission, not a blanket 128 GB label.
The H3 gate is unchanged. Independent, locally qualified CPU routes now enable HDR grading and
Tesseract Finish without H3, a CUDA-tier record or 128 GB hardware.
MMAudio foley is also independently qualified on the 64 GB machine within the limits below.

Use the [native Windows setup/testing runbook](windows-rtx-spark-testing.md) for installation and checks.
This document records current support and remaining work, not troubleshooting history.

Qualification gating/badges are on and locked below 128 GiB installed RAM, optional/off by default above that
boundary. Feature checks remain visible and individual implemented checks run in either mode. This is a
verification-policy default, not a claim that every model fits 128 GiB. Informational Qwen/FLUX3-K/MOSS cards
explain missing installation, verification and unknown Windows memory requirements; no new model was qualified
on 64 GiB by this routing refactor. The actual model and draft/full-model recipe are shown rather than silently
substituting a fallback for an explicit full-model request.

## Hardware handoff at a glance

| Machine | Established result | What remains |
|---|---|---|
| 48 GB | Bounded preview planning, stills, sound, writing, media, graphics and lifecycle checks passed; exact scopes below | Live qualification of the new HDR/Finish scopes; other generators remain unqualified |
| 64 GB | Baseline plus independently qualified HDR/Finish and bounded MMAudio foley; API and browser evidence distinguished below | Full interactive First AD apply/save and remaining baseline playback/download checks; Finish playback/download now passed |
| 128 GB | Upstream native CUDA measurements, including mask/foley/Finish, are published in the N1X notes; not reproduced on our smaller machines | Reproduce named scopes; qualify unrendered actions/training separately, not reimplement their existing ports |

**The bounded baseline is complete, not the whole Windows port.** Untested software routes must not be
handed off as features that merely need 128 GB. The native tier's 90 GB CUDA-pool gate is distinct from
the preview's route-specific memory limits.

## What the upstream merge already provides

These are shared native Windows implementations, not remaining porting tasks. The evidence below is
upstream's named native-tier scope, not verification on this 64 GB machine or proof of a minimum RAM size.

| Routes | Existing implementation / evidence | Remaining distinction |
|---|---|---|
| FastH3, Qwen-Image, Drama 3 and Bolt | Native engines/ComfyUI routes and published Windows runs | Smaller-tier availability and settings require independent admission/qualification |
| MiniMax Music 3 | Native TensorFold engine rendered a 30-second song; ComfyUI route also measured | No local 48/64 GB qualification |
| Find a person and MMAudio foley | Native ComfyUI graphs rendered upstream | Bounded foley now also passes on 64 GB; person masking still needs local qualification |
| Finish and editor conversion | Native Tesseract rendered; After Effects conversion passed upstream; FCPXML/EDL emitted | Our bounded local Finish passes are below; upstream Premiere conversion remains blocked by its converter |
| Turnaround, upscale, remove a person and Character Swap | Native ports/node packs exist; upstream reached missing-weight checks, not completed renders | Install missing parts and verify real outputs/capacity; these are not known to require 128 GB |
| LoRA training | Native environment installed and an 8-bit AdamW step passed upstream | No completed native Windows LoRA training run was established |

## Current support

The table below describes the **bounded native Windows features on 48/64 GB**. Each result applies only to the route and settings
shown. API and browser checks are identified separately; more installed RAM does not enable another route.

| Feature | Supported scope | 48 GB development machine | 64 GB test machine |
|---|---|---|---|
| Planning, Slate and manual boards | Fountain import, scene/shot/cast/dialogue links, references, save/reopen, shot lists | Browser flow verified | Real API flow verified; full interactive flow pending |
| Portable Slate films | Project, referenced media, original screenplay when available, captioned sheets and CSV/Markdown shot lists | Clean-folder browser import verified | Manifest hashes and API round trip verified with original board unavailable |
| SD-Turbo storyboard drafts | 512x512, one step, 1-6 sequential images, distinct captions and seeds | Output and browser workflow verified | One- and six-image outputs verified |
| Stable Audio Open effects | Five seconds, 100 passes, CFG 7; stereo 44.1 kHz PCM WAV | Output, normal queue and browser playback readiness verified | Output and normal queue verified |
| MMAudio foley | Local 1-5 s, 24 fps clips, up to 1920x1080 within the analysis aspect limit; 360-row analysis, 25 passes, CFG 4.5; original picture copied | Not qualified | 1/5 s qualification, 720p normal queue, fractional-length replacement, mixing and queued/synthesis cancellation verified |
| Qwen3-4B Q4_K_M writing | Text-only Slate assistance; one 4096-token slot, up to 1024 answer tokens | CUDA First AD response applied and saved | CUDA startup, title/logline and three-shot First AD response verified; interactive apply/save pending |
| Copilot CLI | Local Connection Check, This Studio tools and session continuity | Verified | Read-only `studio_state` turn and same-thread follow-up verified |
| FFmpeg media operations | Probe, frame extraction, trim/join export; 1-12 clips, ten minutes, even output sides up to 1920 | Mixed silent/audio browser export verified | Real API exports and full decode verified |
| Editor handoff | FCPXML, EDL and media ZIP | API/file checks passed | API/file checks passed; actual Windows Resolve import unverified on either machine |
| Beat detection | Local audio, at most ten minutes; no generation model | 120 BPM fixture and cache verified | 120 BPM fixture and cache verified |
| Motion graphics | Title, lower-third, kinetic, slideshow, music-video and explainer; up to 1280x720, 24 fps, ten seconds | Six templates verified | Six templates verified |
| HDR grading | CPU-only SDR-to-HLG grade plus optional SDR copy; at most 1280x720, 24 fps, ten seconds; no recovered detail | Not yet qualified | Sound/silent outputs, preserved audio packets, full decode, API/UI readiness and cancellation verified |
| Tesseract Finish | Local 720p/24 fps cut, up to 12 clips and ten seconds including title; three mixes, optional music/narration; no transcription or editor conversion | Not yet qualified | Guarded renders, four output files, real browser submission/playback/download and shutdown verified; reliability caveat below |
| Owned lifecycle | Warm reuse, model exclusivity, queued/running cancellation, unload and shutdown | Browser and process checks passed | Scoped checks passed, including submission during model switching |

Bounded image and sound models run one at a time. Unsupported routes remain unavailable across the UI,
API and Studio tools; downloading their weights does not qualify them.

### Independent feature qualification

`app/windows_features.py` separates installed prerequisites, local qualification and current admission.
The UI, API and internal queue use the same qualification; settings and memory headroom are checked again
before execution. A successful local check writes a receipt bound to hardware, runtime and rendering code;
compatibility breaks require requalification. Balanced matching is the default: routine tool, dependency
or source updates keep the dated pass and show an update note, not another compulsory render. Fingerprints
remain provenance; they are not rewritten to imply a fresh test. Recipe/model changes, hardware-profile changes,
core ABI-family changes and explicit compatibility-contract bumps still require a check. Model metadata changes
can require a checksum comparison, not another render. `STUDIO_QUALIFICATION_STRICT=1` opts into exact matching.
A failed, cancelled or temporarily refused rerun keeps the
last successful evidence; only a completed pass replaces it. Current admission remains a separate decision.
HDR/Finish retain a 4 GiB owned-job commitment cap, OS low-memory/commit supervision, explicit host floors,
bounded deadlines and targeted cancellation. The full native tier and its model guards are not relaxed.

The [runbook](windows-rtx-spark-testing.md#independent-hdr-and-finish-qualification) gives setup/check commands.
These are the first independent routes, not a completed capability migration for every native generator.
One repeated app check hit Tesseract 0.4.0's `source .tsrct file changed after it was opened` error;
subsequent runs passed. It remains an explicit failed-job/reliability caveat, with no automatic retry.

### Progressive qualification UI

All six checks are available under **Machine > Feature checks**: storyboard drafts, Stable Audio Open effects,
MMAudio foley, the six motion-graphics templates, HDR grading and local Finish.
Cards show current headroom, the conservative 4 GiB admission requirement/cap, the lowest tested hardware
and saved live evidence. The actual minimum installed RAM is explicitly unknown, not inferred from that cap.
Check/Rerun submits an ordinary cancellable queue job; opening the page only reads status. Success and
latest-attempt records persist separately, and **Download saved report** exports existing results without
starting tests. Setup is currently a copyable command, not an in-app installer.
Apps and the HDR/Finish panels use compact qualification badges; an unqualified badge opens and focuses
the feature's Machine card. Foley has its own setup/check and separate host/device/commit guidance;
its actual minimum installed RAM remains unknown. Qualified cards link directly to their working UI.
The outer section and each room group are independently collapsible and closed by default.

The API/queue/browser path for Finish has passed a real check-button run and saved-report download.
Other model check cards and automatic validated-profile matching are not implemented yet.
The image/sound/graphics adapters reuse native readiness and saved receipts, and rerun the existing real checks
through ordinary queue jobs. The current UI lists the registered features only. Groups follow the Studio's top-level rooms and their sidebar order
(Image, Sound, Motion graphics, Edit, with Video/Story and others added as checks exist). A check covers
a named feature/model recipe, not every button in that room. Badge links still focus the exact feature.

### Remaining progressive qualification design

Base workflows remain usable without a qualification sweep. Matching validated hardware/runtime profiles
can enable installed, tested recipes without another test render; advertised RAM alone is insufficient.
Additional feature cards offer individual installation/qualification checks with persisted results.
An optional full sweep reuses those same checks to produce a report; it never gates startup or normal use.
Keep results across restarts rather than repeating expensive renders automatically. Each check must also
have an explicit rerun action, including after another application releases GPU memory. Preserve valid
prior evidence when a new attempt is refused before execution because resources are temporarily busy;
record the latest attempt and its reason separately. Verification requires real execution, measured memory,
valid outputs and lifecycle evidence, not an estimated fit. Reused evidence must be identified as prior
evidence with its hardware/runtime/settings scope, not as a fresh live check on this machine.

Before each check, show its expected minimum memory for the named model/settings, whether that requirement
is measured or estimated, and the machine's current available headroom. Distinguish host RAM, dedicated
GPU memory, CUDA allocation budget, commit backing and the safety reserve where applicable; never add
overlapping pools. If a minimum is unknown, say so rather than inventing a number. Report the lowest tested
hardware separately from a claimed minimum, and an enforced allocation cap separately from measured usage.
The same information belongs in individual check results and the optional full report. Per-job admission
and runtime guards remain mandatory even for features enabled by a validated profile.

### Shared architecture; Windows adapter only for now

`app/feature_checks.py` defines platform-neutral check descriptions, distinct host/device/unified/commit
memory requirements and owner-scoped pass/attempt storage. It assumes no RAM tier, default install path,
platform probe or renderer. `app/windows_checks.py` supplies the current Windows recipes, probes, setup
commands and queue runners. `/api/feature-checks` is the shared UI contract; the older Windows URLs remain
aliases. The UI discovers supported checks through `/api/state`, rather than treating a Windows mode or
128 GB label as eligibility.

Mac and DGX Spark adapters/check implementations are not part of this change. They can provide the same
contract with their own measured recipes, memory pools and fingerprints; existing platform guards stay
authoritative. Smaller DGX Spark configurations and variable-memory Macs must be measured, not assigned
the largest model set from a platform name.

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
  Bounded image/sound jobs use UUID/LUID-bound WDDM adapter-wide dedicated usage, separate OS signals,
  a 2 GiB dedicated reserve and the unchanged hard allocator/process/job caps. This is not a reading of
  the backend's CUDA allocator budget. No model-load HTTP or NVML polling is needed; the static dedicated
  capacity is read before loading. Missing/invalid counters or samples older than two seconds refuse the job.
  The separate no-model diagnostic still checks the backend's own CUDA budget.
- Foley dependencies live in `foley-gpu-venv`, never the baseline GPU or UI environments; source and weights
  are shared. Its cold owned backend drains before another model starts. Setup/repeat/verify use the same
  GPU manifest and configured package feeds; `soxr` is built natively only when an ARM64 wheel is unavailable.
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

Work on progressive qualification and smaller-machine routes first. Keep 128 GB reproduction as a separate
hardware handoff; do not describe already merged native ports as missing implementations.

| Work bucket | Routes / checks | Next owner needs to establish |
|---|---|---|
| Extend progressive qualification UI and profiles | HDR/Finish Check/Rerun, memory/evidence cards, saved attempts and report download now work; other feature cards, in-app installation, validated-profile matching and a broader optional sweep remain | Reuse shared checks and consistent API/UI/tools decisions; no mandatory full sweep, estimated passes or blanket RAM unlock |
| Complete independent CPU feature qualification | Run HDR/Finish on 48 GB; investigate the intermittent Tesseract project-write failure; refresh receipts after relevant code changes | Preserve measured bounds and make reliability limits explicit; cached evidence is not a fresh run |
| Broaden Foley evidence | Isolated setup and bounded shared-graph qualification/normal jobs now pass on 64 GB, including mixing, replacement and cancellation | Qualify other machines and any larger recipe separately; no 48 GB pass or listening-quality claim |
| Additional 48/64 GB routes | Person masking, picture generation/edits, music, transcription and pose/depth analysis | Reuse existing Windows routes where present; establish dependencies, guarded output and measured envelopes one recipe at a time. Expected fit is not a pass |
| Close remaining baseline workflow gaps | Interactive First AD apply/save and remaining baseline media playback/download on 64 GB; actual Resolve import on either tested tier | Real actions preserve projects/media and outputs work in the destination application; Finish playback/download already passed |
| Reproduce 128 GB native results | Published FastH3, Qwen-Image, Drama 3, Music 3, Bolt, mask, foley, Finish and After Effects scopes | Full native install and actual budgets, then output/lifecycle checks at published settings; do not infer other routes or sizes pass |
| Unrendered native routes/settings | Turnaround, upscale, remove, swap, unmeasured Ref2VA/settings and Premiere conversion | Ports already exist; missing weights, output verification and the upstream converter limitation remain separate from memory requirements |
| Separate advanced work | Worker orchestration, paired GPUs and actual LoRA training | Native training environment/optimizer checks are not a completed training run; worker/paired-GPU work needs separate implementation and hardware qualification |

For a 128 GB handoff, follow [the native installation instructions](rtx-spark-n1x.md#install-one-shot-natively)
and the [tier-testing procedure](windows-rtx-spark-testing.md#5-handoff-to-a-128-gb-tester), keeping preview and
native results separate. Label **missing dependency**, **unqualified**, **temporary memory refusal** and
**missing implementation** separately; none is a render pass or proof that 128 GB is required.

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
