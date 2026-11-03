# Native Windows RTX Spark: findings and porting plan

Date: 2026-11-02

Scope: native Windows 11 ARM64 on NVIDIA RTX Spark, not WSL and not a Linux container.

Project framing: a personal, best-effort, AI-driven exploratory hobby project. AI coding agents assist with
research, implementation and testing; generated code and estimates require review and reproducible validation.

## Executive assessment

**Linux is not fundamentally required. A useful Windows port is realistic, but full DGX Spark parity is a substantial engineering project.**

**Research correction, 2026-11-02:** TensorFold already has native Windows platform support. It is present in
the revisions this Studio pins for Music and Bolt. The older H3 and Drama pins still have Linux-specific code.
The work is therefore a combination of reusing and validating existing upstream support, porting specific older
engine paths, and integrating Windows into Studio. It is not a clean-sheet Windows port of all four engines.
See [the source audit](#10-source-level-follow-up-and-revised-confidence) for exact evidence and remaining limits.

**Implementation update, 2026-11-02:** native ARM64 CUDA PyTorch operations now pass on the 48 GB development
machine, and the pinned ComfyUI requirements resolve as wheels without replacing that PyTorch build.
This advances the feasibility spike, not rendering support. See [the first implementation results](#11-first-implementation-results).

There are two separate problems:

1. **Platform support:** Windows ARM64 dependencies, process management, installers, memory guards, and native TensorFold engines.
2. **Capacity:** which workloads fit a particular machine after Windows, CPU staging, GPU allocations, and safety margins.

A 128 GB RTX Spark should be a credible target for most features after porting and measurement. It is not automatically equivalent to a 128 GB DGX Spark: Windows divides the unified pool differently, its GPU budget changes at runtime, and its binaries and drivers are different.

On the current 48 GB machine, the best targets are the existing planning UI, media tools, motion graphics, sound effects, and selected image/music workflows. The current H3 video and Drama 3 voice routes are too large to promise. Making those fit would be a separate optimization project, not just Windows support.

The 48 GB machine remains the primary development box. A 64 GB RTX Spark is also available for testing, providing
a second real hardware tier; its configuration and memory budgets have not yet been inventoried. A 128 GB
RTX Spark remains a prospective larger-model validation target, not currently available test hardware.

**Recommendation:** build a tiered Windows backend, initially using supported PyTorch/ComfyUI paths. Port TensorFold engines later, independently. Enable each feature only after its own dependency, correctness, memory, and lifecycle checks pass.

## 1. Evidence and limits of this assessment

### Verified on the current machine

| Item | Finding |
|---|---|
| OS and CPU architecture | Windows 11 Pro, native ARM64 |
| Physical memory | 48 GiB reported by the physical-memory inventory |
| Windows usable physical RAM | Approximately 20.8 GiB after hardware reservations |
| Available Windows RAM | Approximately 7-8 GiB during the probes; varies with other applications |
| GPU | NVIDIA RTX Spark, 6144-core Blackwell RTX GPU |
| NVIDIA driver | 617.42 |
| CUDA driver API | 13.4; native driver initialization succeeded |
| CUDA architecture | Compute capability 12.1, integrated GPU |
| NVIDIA dedicated-memory report | 26 GiB, with about 24 GiB free in the initial inventory |
| CUDA allocation budget | **35.63 GiB total, 35.44 GiB free** during an isolated driver-context probe |
| Disk | Approximately 699 GB free during the initial inventory |
| UI | Native Windows server and browser navigation work |
| Focused validation | 108 tests passed, 1 POSIX-only test skipped |

The CUDA probe created and released a driver context. It did **not** load weights, allocate model tensors, run inference, or establish safe sustained memory limits.

The test result covers the Windows preview and selected related regressions, not the entire repository or GPU generation. The browser checks covered Story, Library, Nodes, Machine, and Settings, with no JavaScript errors observed during those checks.

### Verified upstream availability

- NVIDIA publishes native Windows ARM64 CUDA-enabled PyTorch wheels.
- The checked stable set is PyTorch `2.14.0+cu134`, TorchVision `0.29.0+cu134`, and TorchAudio `2.11.0+cu134`.
- Matching Python 3.13 PyTorch wheel filenames were present in NVIDIA's index.
- The repository-pinned Comfy Kitchen `0.2.39` and Comfy AIMDO `0.5.6` have Windows ARM64 wheels.
- PyAV `19.0.2` has a compatible Windows ARM64 ABI3 wheel.
- CTranslate2 `4.8.4` had no Windows ARM64 wheel in the checked release.

Wheel availability is not proof that a complete workflow installs or runs. Dependency resolution, imports, GPU execution, and output validation remain necessary.

### Not verified

- The available 64 GB test machine's configuration, Windows/CUDA budgets, or workflow behavior.
- Any native Windows model render. Tiny PyTorch math/vision/audio operations are now verified separately.
- Native Windows TensorFold compilation or inference on this machine. Existing upstream Windows support and
  contributor hardware reports have now been identified, but not reproduced here.
- Windows ARM64 compatibility of every ComfyUI node pack and transitive dependency.
- Memory behavior or performance on a 128 GB RTX Spark.
- Whether a particular 48 GB workflow fits after host-side staging and safety reserves.
- CUDA behavior inside ARM64 WSL on this hardware. WSL is not assumed to be a working shortcut.

## 2. Unified memory: what the numbers mean

The user's memory observation was correct: RTX Spark does not behave like a discrete GPU limited to its dedicated-memory number.

NVIDIA documents three logical regions of the same physical pool:

1. A dedicated GPU carveout, backed by system DRAM.
2. Shared system memory accessible to the CPU and GPU.
3. CPU-only memory.

Ordinary CUDA device allocations first use the dedicated segment and can spill into the shared segment. NVIDIA recommends ordinary CUDA allocation APIs for model weights and intermediate buffers; `cudaMallocManaged` is not a general solution and is discouraged on this platform's compatibility path.

Important reporting differences:

- NVML's dedicated-memory figure is not the whole allocatable CUDA budget.
- CUDA memory queries expose a dedicated-plus-shared budget.
- Windows DXGI local-segment budgeting also covers dedicated plus shared memory on this UMA platform.
- Shared memory used by the GPU competes with CPU memory. **Do not add Windows RAM and the CUDA budget as independent capacities.**

This explains why the current machine can report approximately 26 GiB dedicated, 20.8 GiB Windows-usable RAM, and a 35.6 GiB CUDA budget without having separate pools of those sizes.

### Consequences for a hypothetical 128 GB machine

The exact budget must be queried on that machine. NVIDIA describes the shared portion as the post-carveout capacity minus 16 GB, clamped to 50-80% of the post-carveout capacity. The carveout and runtime budget must not be guessed from this machine's values.

Even with 128 GB physical memory:

- Not all physical memory is available for GPU allocations.
- CPU staging needs room outside the dedicated carveout.
- Windows can reduce the process's budget as other applications use memory.
- Successful GPU allocation does not establish that the overall machine is safe.

Admission must track CPU headroom, CUDA/DXGI headroom, and overlap in the shared region without double-counting it. It must preserve reserves for Windows and the display.

### Treat memory findings as configuration-specific

The current CPU/GPU split and allocation budget are observations of this configuration, not permanent limits
for every machine of the same physical capacity. Record the Windows build, driver, actual memory split and
runtime budgets with each test result. Evaluate GPU allocations and CPU-side model staging together.

The capability matrix is a conservative starting point for the current model routes, not a claim that a memory
tier can never run a feature. Revise it when reproducible measurements justify a different conclusion, preserving
host headroom and safety reserves.

## 3. Dependency inventory

Statuses below describe the environment inspected during this work. "Missing" means not found on PATH or in the isolated UI environment, not an exhaustive search of every folder on the disk.

| Dependency | Current machine | Windows work required |
|---|---|---|
| Python 3.13 ARM64 | Installed | Keep an isolated, reproducible runtime |
| FastAPI, Uvicorn, multipart, NumPy, Pillow | Installed; UI verified | Existing preview baseline |
| Git | Installed | Retain non-interactive, bounded operations |
| NVIDIA ARM64 CUDA driver | Installed; driver calls verified | GPU tensor and sustained-load tests |
| CUDA-enabled PyTorch / TorchVision / TorchAudio | Installed in separate GPU environment; tiny operations verified | Model workflows and capacity remain unverified |
| CUDA toolkit / nvcc | Not found | Required for custom CUDA builds; not necessarily for prebuilt PyTorch inference |
| Native C++ build tools, CMake, Ninja, Zig | Not found | Needed for source extensions and TensorFold ports |
| ComfyUI | Installed; pinned no-model server readiness and owned shutdown verified | Model workflows, optimized allocators and production lifecycle remain unverified |
| Comfy Kitchen / AIMDO | Not installed | Pinned ARM64 wheels exist; execution still untested |
| Transformers, Diffusers, Safetensors, Hugging Face Hub | Not installed in UI environment | Resolve and lock compatible ARM64 dependencies |
| PyAV | Not installed | Compatible wheel exists; validate video read/write |
| FFmpeg / FFprobe | Not found | Install and verify codecs, probing, and subprocess handling |
| Node.js / npm | Not found | Native runtime for graphics and relevant agent tools |
| HyperFrames, GSAP, Playwright browser | Not installed by this setup | Port installer and validate Windows browser rendering |
| OpenCV, ONNX Runtime, MediaPipe | Not installed in UI environment | Audit exact versions and wheels; source builds or explicit partial availability may be necessary |
| Faster Whisper / CTranslate2 | Not installed | CTranslate2 wheel gap; do not silently claim speech QA works |
| Cryptography / Web Push | Not installed in UI environment | Verify current compatible packages; source-build needs may include Rust and OpenSSL |
| TensorFold H3, Music 3, Drama 3, Bolt engines | Not installed | Reuse existing Windows platform code where present; older engine paths and Bolt's custom backend still require targeted work |
| Docker / WSL | Not installed | Not requirements of the proposed native port |

The CUDA installer currently pins PyTorch `2.14.1` and TorchVision `0.29.1` for its existing platforms, while the verified NVIDIA Windows wheel set differs. A Windows dependency lock must document and test this platform-specific difference rather than silently replacing the Linux pins.

Linux ARM64 wheels and Linux engine binaries cannot be loaded by Windows ARM64 Python. Installing Git Bash does not solve binary compatibility.

## 4. How difficult is the port?

### Relative difficulty

| Area | Difficulty | Main reason |
|---|---|---|
| UI, projects, settings, uploads | Low-medium | Most application code is portable; preview already works |
| FFmpeg media operations and exports | Medium | Quoting, executable discovery, cancellation, codecs, and paths |
| PyTorch + ComfyUI backend | Medium-high | ARM64 dependency compatibility and safe backend lifecycle |
| Windows memory admission and watchdog | High, mandatory | Dynamic WDDM budgets, unified-memory overlap, host staging, display safety |
| Individual PyTorch/node-pack features | Medium-high | Per-package native extensions and measurement |
| TensorFold shared Windows runtime | Medium, reproduction required | Native Windows abstractions and source build instructions already exist upstream |
| TensorFold Music 3 | Medium-high, provisional | Its pin contains the shared Windows port and a generation target; generator/runtime behavior still needs a Windows build and test |
| TensorFold H3 | High, confirmed porting work | Older pin gates CUDA builds to Linux; checkpoint mapping and driver/library names are POSIX/Linux-specific |
| TensorFold Drama 3 | High, provisional | Older pin uses the Linux CUDA loader; reuse a newer platform layer without assuming the newer family/CLI is interchangeable |
| TensorFold Bolt with LiLiCorr | High, confirmed custom-build work | Shared server has Windows support, but the specialized libbolt build still emits a Linux library using Linux compiler/link flags |
| Worker orchestration and managed updates | High | Ownership identity, leases, process trees, SSH/rsync assumptions |
| Full tested feature parity | Very high | Combines all of the above; not just a launcher port |

### Rough effort, not a delivery commitment

For one experienced engineer with this hardware, model access, and upstream engine cooperation:

These ranges describe active engineering effort, not calendar deadlines. Hobby availability, hardware access and
unresolved blockers can extend elapsed time indefinitely; scope may be reduced or work stopped.

- Dependency/GPU feasibility spike: **2-5 engineering days**, including reproduction of the existing upstream Windows build path.
- Useful, guarded Windows generation subset: **roughly 2-4 engineering weeks total**, if the pinned ComfyUI stack installs.
- Broader practical feature coverage: **roughly 4-8+ weeks total**.
- Near-parity including native TensorFold engines, installation, lifecycle hardening, and larger-hardware validation:
  **not a defensible narrow estimate yet**. The earlier **2-4+ months** is a contingency envelope, not an issue-ready commitment.

These are planning ranges, not measured implementation estimates. A targeted source audit has now identified
both existing Windows implementations and concrete older-engine blockers. No native engine build has been
attempted here. The next estimates should come from timed compile/import/smoke experiments, not further generic
platform speculation. Missing native dependencies or engine changes can still dominate the schedule.

The 48 GB development box and available 64 GB test machine can validate two capacity tiers, but neither can certify
the large-model behavior of a 128 GB target. Additional hardware improves evidence, not the underlying port's
completion guarantee; maintaining separate environments and test receipts also takes time.

## 5. What could run after porting?

**This table is a projection, not a support announcement.** Unless stated otherwise, capacity evidence comes from existing Linux/DGX measurements, sometimes from a DGX simulation of a discrete card. It is not a Windows benchmark.

The tier columns describe **memory suitability after the necessary Windows software work**, not readiness or
test coverage. For the same workload and comparable configuration, an expected fit carries upward to larger
memory tiers. A larger machine is not less promising merely because it has not been tested.

Legend:

- **Expected fit:** no identified capacity barrier at the stated workload size; still requires validation.
- **Needs measurement:** loading peaks, settings or host/GPU budgets could determine whether it fits safely.
- **Beyond current budget:** the current route's measured/estimated demands exceed the present safe envelope,
  not an immutable limit on future implementations.
- **Route-dependent:** follows the capacity requirements of the underlying model/workflow.

Verification is separate: only the UI preview has been executed locally on 48 GB. No Windows generation result is
verified at any tier. The 64 GB machine has not yet been inventoried; 128 GB hardware is not available.
Software/dependency blockers are noted in the evidence column and apply regardless of the machine's memory.

| Feature | 48 GB: primary dev box | 64 GB: available test tier | 128 GB: prospective tier | Evidence / qualification |
|---|---|---|---|---|
| Planning, Slate, settings, uploads, library | Expected fit; UI verified | Expected fit | Expected fit | No model inference required |
| Prompt compilation, graph editing | Expected fit; UI verified | Expected fit | Expected fit | Graph execution remains disabled |
| FFmpeg inspection, trimming, joining, timeline export | Expected fit | Expected fit | Expected fit | Media dependencies and lifecycle integration required |
| Motion graphics / titles | Expected fit | Expected fit | Expected fit | Node/browser/FFmpeg port; use bounded sizes |
| Finish / external-editor interchange | Expected fit for interchange | Expected fit for interchange | Expected fit for interchange | Proprietary Tesseract binary availability remains a separate unresolved blocker |
| Qwen images, scouts, picture edits | Needs measurement | Expected fit at small test sizes | Expected fit at those sizes | Small-test GPU peaks about 22-25 GB; host staging also matters |
| FLUX3-K Fast/Full images and edits | Needs measurement | Needs measurement | Expected fit at test sizes | Checkpoint, upsampler and staging need separate measurements |
| MOSS-Image | Needs measurement / tight | Needs measurement | Expected fit at test sizes | Small tests about 22-25 GB GPU; model-switch run reached 56.8 GB |
| Stable Audio Open sound effects | Expected fit | Expected fit | Expected fit | Linux small-run peaks about 5-9 GB; first GPU target |
| MMAudio foley | Expected fit for short clips | Expected fit at those sizes | Expected fit at those sizes | Existing Linux peaks about 14-17 GB |
| YuE2 music, PyTorch route | Needs measurement | Expected fit for short songs | Expected fit at those sizes | Linux bf16 peaks about 12-19 GB; cold loading must fit |
| MiniMax Music 3 through ComfyUI | Needs measurement / tight | Needs measurement | Expected fit at test sizes | Linux system peaks about 28-29 GB; Windows partition differs |
| MiniMax Music 3 through TensorFold int8 | Needs measurement | Expected fit for short songs | Expected fit at those sizes | Linux peaks about 17-19 GB; Windows generator untested |
| Fish Audio Drama 3 voice | **Beyond current budget** | Needs measurement | Expected fit | About 41.6 GB peak; backend asks for 50 GB free including reserve |
| FastH3 standard video | **Beyond current budget** | Needs measurement; not initial target | Expected fit at measured sizes | Linux unified peaks about 58-71 GB; small GPU-only peaks about 50-53 GB |
| H3 video, ComfyUI bf16 blocks | **Beyond current budget** | **Beyond current budget** | Expected fit at measured sizes | Linux total route about 75-77 GB before margins |
| Ref2VA picture/video/audio references | **Beyond current budget** | **Beyond current budget at measured sizes** | Expected fit at measured sizes | Small-test GPU peak about 67 GB; reference length/count matter |
| Chained long videos | Beyond current H3 budget | Route-dependent | Expected fit if each segment fits | Chaining does not remove a segment's base-model memory demand |
| Character Swap | **Beyond current safe budget** | Needs measurement with offload | Expected fit at measured sizes | About 49-53 GB GPU; offload about 38-41 GB plus reserves/host constraints |
| Turnaround, Upscale, Remove a person | **Beyond measured heavy-route budget** | **Beyond measured heavy-route budget** | Expected fit at measured sizes | Simulated-card GPU peaks about 67-72 GB |
| Nemotron 3.5 Bolt brain | Needs measurement; not initial target | Needs measurement | Expected fit | Current routes ask for about 40-43 GB free; file size is not runtime peak |
| Smaller local/external brain connections | Expected fit for suitably small models | Expected fit at those sizes | Expected fit at those sizes | A smaller model is a separate choice, not evidence Bolt fits |
| Motion Previs basic layers | Expected fit at small sizes | Expected fit at those sizes | Expected fit at those sizes | Pose/depth require the full native dependency stack |
| Speech transcription / spoken-word QA | Expected fit for modest models | Expected fit at those sizes | Expected fit at those sizes | CTranslate2 packaging gap is independent of RAM size |
| LoRA application | Route-dependent | Route-dependent | Route-dependent | Existing H3 route/adapter limits still apply |
| LoRA training | **Beyond current recipe's budget** | Needs measurement; not initial target | Expected fit at current recipe size | Standard recipe holds about 55 GB; Windows training stack still unverified |
| Remote Studio workers | Expected fit for coordination | Expected fit | Expected fit | Orchestration port required; worker's own memory governs its renders |
| Multiple local GPUs | Not applicable to a single integrated GPU | Same | Same | Separate hardware configuration; the NPU is not a second CUDA GPU |

### Practical expectation for this machine

The realistic goal is **a useful partial studio, not a full H3 video workstation**:

1. Planning and media management.
2. Editing/export and graphics once their executables are available.
3. Sound effects and short foley.
4. Selected images and short music, only after measurements.
5. Optional small brain integrations.
6. Heavy video/voice through a separately supported worker, if desired.

Images are not promised simply because this machine has "48 GB." Windows currently exposes much less host RAM, and model loading can stage a second copy. Unloading other applications may help availability, but does not change the physical-memory partition.

Smaller resolutions reduce activation memory; they do not remove the fixed model/encoder footprint. Fewer steps reduce work, not necessarily peak memory. Larger pagefiles are not a safe replacement for a validated allocation budget.

### Available 64 GB test tier

The 64 GB machine is a testing resource, not a requirement for development or a reason to raise the 48 GB minimum.
Do not extrapolate its CUDA budget by adding 16 GB to this machine's reported budget: carveout configuration,
driver behavior and background workloads must be inventoried independently.

| Area | Intended use of the 64 GB machine | What cannot be assumed |
|---|---|---|
| UI, dependency installation and backend lifecycle | Repeat the same installation, startup, cancellation and shutdown checks | Success on one machine does not establish a reproducible installation on both |
| Images, edits and music | Measure whether additional host staging and GPU/shared headroom permit more routes or settings | All models, resolutions or durations fit |
| Sound effects, foley and media tools | Repeat correctness checks and cold/warm memory measurements | Extra capacity implies higher throughput |
| Drama 3 and Bolt | Conditional later experiments only if ported backends and conservative admission permit them | 64 GB physical memory satisfies their free-memory requirements or Windows GPU budgets |
| H3 video and other heavy workflows | Infrastructure/refusal tests first; any inference trial requires a separate measured safety case | Existing roughly 58-71 GB H3 peaks plus reserves fit a 64 GB Windows system |
| Cross-tier behavior | Confirm jobs safely refused on 48 GB can be reported accurately, even if later admitted on 64 GB | A 64 GB pass qualifies the 48 GB tier, or certifies 128 GB parity |

Keep builds, model revisions and test settings comparable. Store separate reports for 48 GB and 64 GB, including
actual host/GPU budgets, cold/warm peaks and explicit refusals. Start on 64 GB by repeating the already validated
small workloads before expanding one dimension at a time under an independent watchdog.

## 6. Proposed implementation plan

### Phase 0: establish scope and retain a reproducible baseline

- Keep native Windows ARM64 separate from Linux and macOS platform selection.
- Preserve the UI-only preview until individual features are validated.
- Add a support/capability inventory: installed, missing dependency, unsupported, unmeasured, or available.
- Keep project/media compatibility and existing access controls.
- Record environment versions and test results without personal paths, hostnames, keys, or tokens.

**Exit:** current planning features remain usable; unsupported actions fail explicitly and do not start background work.

### Phase 1: GPU and dependency feasibility spike

- Coordinate with the existing TensorFold Windows work before building replacement abstractions.
- Reproduce its source-only Windows host tests and a tiny CUDA smoke test without weights. Investigate Music 3's
  existing build target in parallel with the ComfyUI dependency spike; this need not wait for Phase 5.
- Create a separate GPU environment, not the working UI environment.
- Install the matched NVIDIA ARM64 PyTorch wheel set and record exact sources/versions.
- Verify imports, GPU selection, a tiny tensor operation, bf16/fp16 operations, synchronization, and clean exit.
- Install the pinned ComfyUI revision and audit transitive dependencies before any model download.
- Test Comfy Kitchen/AIMDO imports and the operations needed by the selected first workflow.
- Do not require the entire CUDA toolkit or a full compiler stack unless a dependency actually needs compilation.
- Document blockers rather than silently unpinning or replacing the existing implementations.

**Exit:** reproducible dependency lock, successful tiny GPU operation, and ComfyUI startup/health/shutdown without weights.

**Stop condition:** if essential dependencies require a major unsupported ARM64 source port, reassess scope before committing to broad generation support.

### Phase 2: safe Windows runtime and admission

- Launch backend processes directly with argument arrays, without a Bash compatibility dependency.
- Own and cancel process trees using Windows process handles/job objects and validated launch identity.
- Handle timeouts, exit codes, log capture, cancellation, and application shutdown.
- Query Windows physical-memory headroom and GPU allocation budgets; keep dedicated/shared figures distinct.
- Add a Windows memory watchdog and react to budget pressure; never use Linux `/proc` readings or dedicated NVML memory alone as the admission rule.
- Measure host staging and GPU/shared allocations together. Avoid both double-counting shared memory and overlooking CPU-only constraints.
- Keep one local heavy workload at a time initially; unload incompatible models between kinds.
- Preserve current Linux/Mac measured limits. Add Windows measurements separately.

**Exit:** synthetic bounded tests cover refusal, pressure, cancellation, child cleanup, and unexpected backend death.

### Phase 3: first real GPU feature

**Recommended first feature: a short Stable Audio Open sound effect**, subject to the model's download gate and licence acceptance.

- Use the existing ComfyUI workflow, not a new standalone implementation.
- Start at the smallest useful output.
- Measure cold and warm runs with a separate watchdog.
- Verify an actual playable output using FFprobe and a listening check.
- Test cancellation and switching back to the UI without a resident process leak.
- Publish Windows-specific measured limits and provenance.

Alternative first target: a small Qwen image if its dependency/load path is easier. It has a tighter memory envelope and is not the lowest-risk capacity test.

**Exit:** repeatable output and a safe refusal above the measured envelope; only then enable the corresponding UI action.

### Phase 4: useful 48 GB feature tier

- Add FFmpeg media tools, timeline exports and graphics.
- Validate short foley and YuE2 music where dependencies permit.
- Validate Qwen images/edits with careful cold-load memory measurements.
- Audit FLUX3-K and MOSS independently rather than inferring support from Qwen.
- Add basic Motion Previs layers; report pose/depth/transcript gaps individually.
- Wire readiness consistently across API, UI, jobs, nodes, schedules, and agents.
- Keep day-to-day development and small-workload regression testing on the 48 GB box.
- Inventory the 64 GB test machine, reproduce the same environment and repeat the validated baseline there.
- Expand the 64 GB envelope only with separate measurements and unchanged safety reserves; never promote those
  results into the 48 GB support matrix.

**Exit:** separate per-feature smoke reports for the available 48 GB and 64 GB machines, with real output files,
measured memory, known limits, and explicit unavailable reasons. Unperformed tests remain unverified.

### Phase 5: native TensorFold engines

- Reuse TensorFold's existing `sys` platform layer rather than independently recreating DLL, socket and memory-policy shims.
- For H3/Drama's older pins, ask maintainers whether they prefer a narrow platform backport or a tested engine-pin update.
- Audit remaining family-specific file mapping, timing, stdio and CUDA-library use; general native-server support does not qualify every generator.
- Build Windows ARM64 DLL/EXE artifacts using a native toolchain; Linux carrier binaries cannot be reused.
- Start with one engine. Music 3 may be more useful to this 48 GB machine than H3.
- Validate the binary protocol and numerical/output behavior against the existing implementation.
- Retain adapter merging semantics, int8 saved-weight integrity, model exclusivity, and idle unload.
- Add build provenance and checksums; do not commit built binaries into the repository.

**Exit:** one independently validated engine at a time. No automatic claim of support for the others.

### Phase 6: 128 GB validation and advanced workflows

- Obtain an actual 128 GB RTX Spark and query its real Windows/CUDA budgets.
- Measure H3 from the smallest permitted sizes upward, with independent watchdogs and the existing safety discipline.
- Add Ref2VA and LoRAs only inside measured route-specific limits.
- Validate Drama 3, Bolt, swaps and the heavier library actions separately.
- Test repeated model transitions, warm servers, driver pressure, failed loads and shutdown.
- Port remote-worker coordination, updates and optional training only after core reliability.
- Run a Windows-adapted equivalent of the repository's feature checker and UI checker.

**Exit:** a published Windows support matrix based on that hardware, not extrapolated from DGX measurements.

## 7. Acceptance criteria and non-goals

### Required before calling a feature supported

- Native ARM64 dependency installation is reproducible.
- Startup, readiness and missing-dependency reporting are correct.
- A real output has the required format, dimensions/rate/duration, and is readable/playable.
- Cold and warm memory peaks are measured with safety margins.
- Cancellation, failure and shutdown do not leave owned work running.
- The model lifecycle cannot overlap incompatible heavy workloads.
- API, UI and automation surfaces agree on availability.
- Existing access controls, licence notices and credits remain intact.
- Existing supported platforms are not changed unintentionally.

### Not included in the initial port

- Making every DGX model fit 48 GB through new quantization, disk streaming or changed model architectures.
- Treating swap/pagefile capacity as GPU memory.
- Lowering existing guards to make a demo pass.
- Promising DGX performance from matching capacity or compute capability.
- Using Linux Docker images as native Windows engines.
- Declaring WSL a supported fallback without testing its ARM64 GPU stack.

## 8. Current repository state

The current change provides a **Windows UI-only preview**, not a GPU backend:

- [PowerShell launcher](../scripts/app.ps1).
- [Windows UI dependencies](../app/requirements-windows.txt).
- [Windows preview tests](../tests/test_windows_ui.py).
- Windows platform labelling and explicit refusal of unverified actions.
- Native memory/disk telemetry and browser-compatible media paths.
- Protected Windows access-key file permissions.
- Planning, settings, uploads and Motion Previs pack import support.

No model weights were downloaded during the preview or inventory work. PyTorch/ComfyUI inference is still untested.

## 9. Sources

### Official platform documentation

1. [NVIDIA: RTX Spark development setup and Windows ARM64 PyTorch](https://forums.developer.nvidia.com/t/rtx-spark-getting-started-with-development/385976).
2. [NVIDIA: PyTorch Windows build/wheel repository](https://github.com/NVIDIA/pytorch-windows-ci).
3. [NVIDIA: RTX Spark unified-memory architecture](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/uma/index.html).
4. [NVIDIA: CUDA allocations, memory reporting and Windows budgeting](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/uma/umacuda.html).
5. [NVIDIA: RTX Spark hardware overview, up to 128 GB](https://docs.nvidia.com/rtx-spark/rtx-spark-porting-guide/latest/overview.html).
6. [NVIDIA: developer-preview caveats](https://forums.developer.nvidia.com/t/rtx-spark-developer-preview/377106).
7. Wheel inventories: [NVIDIA PyTorch](https://pypi.nvidia.com/nvtorch_oot/torch/), [Comfy Kitchen 0.2.39](https://pypi.org/pypi/comfy-kitchen/0.2.39/json), [Comfy AIMDO 0.5.6](https://pypi.org/pypi/comfy-aimdo/0.5.6/json), [PyAV](https://pypi.org/pypi/av/json), [CTranslate2](https://pypi.org/pypi/ctranslate2/json).

### Repository evidence

- [CUDA installer and pinned ComfyUI/PyTorch dependencies](../oneshot-setup-cuda.sh).
- [Memory estimates, measurements and route limits](../spark/memory_guard.py).
- [96 GB simulated-card measurements and limitations](./rtx-pro-6000.md#the-simulation).
- [32 GB RTX 5090 route: requires substantially more host RAM](./rtx-5090.md).
- [ComfyUI lifecycle and Linux-specific guard launch](../spark/comfy.sh).
- [H3 native engine build](../spark/build-engine.sh).
- [Music 3 native engine build](../spark/build-music3.sh).
- [Drama 3 native engine build](../spark/build-drama3.sh).
- [Voice memory and lifecycle](../app/voice.py).
- [Bolt memory and lifecycle](../app/bolt.py).
- [Training recipe memory](../app/train.py).
- [Graphics runtime/install assumptions](../scripts/setup-graphics.sh).

All theoretical capability and effort estimates above are engineering projections. Existing measurements retain their original platform and scope.

## 10. Source-level follow-up and revised confidence

This follow-up inspected public upstream files at the exact Studio engine pins. It did not modify those sources,
install a toolchain, compile an engine, or render a model.

### Existing Windows work: important coordination targets

- [ashhart/TensorFold#836](https://github.com/ashhart/TensorFold/pull/836) is **merged**. It introduced substantial
  Windows platform work, with contributor Windows test reports. Its original branch was in
  [BobClawblaw/TensorFold](https://github.com/BobClawblaw/TensorFold).
- [ashhart/TensorFold#1010](https://github.com/ashhart/TensorFold/pull/1010) is **closed, not directly merged**.
  The maintainer states its RTX Spark/ARM64 increment was carried into 1.1.0 in
  [commit 5b1f1081271b](https://github.com/ashhart/TensorFold/commit/5b1f1081271b976453628706a22343af8c9abc87).
  Its contributor branch was in [mweinbach/TensorFold](https://github.com/mweinbach/TensorFold).
- The [maintainer's integration comment](https://github.com/ashhart/TensorFold/pull/1010#issuecomment-6248980626)
  explicitly distinguishes source integration and Linux host/compile checks from a final Windows hardware run.
- The [Windows guide at the Studio's Music pin](https://github.com/drowzeys/TensorFold/blob/8cd08c8350c02cf3d7d8eee5f1c1d9dfa22fa703/docs/WINDOWS.md)
  reports contributor `aarch64-windows` / RTX Spark `sm_121` runs, but says those device results have not been
  reproduced on the maintained tree. These reports concern native model-serving support, not all Studio generators.

This is substantially stronger evidence than "Windows might work." It is still not a verified Windows Studio
release or a receipt for H3/Music/Drama/Bolt on this machine.

### Engine-by-engine source findings

| Studio pin | Confirmed source evidence | Implication |
|---|---|---|
| H3 `1f7ecc75` | Root build routes CUDA only on Linux. Driver opens `libcuda.so.1`; cuBLASLt loader searches Linux `.so` names. H3 directly calls `mmap`, `munmap`, `pread` and `clock_gettime`. | Real, bounded platform changes are needed before a Windows build can be claimed. Kernel algorithms are not thereby proven incompatible. |
| Drama `81173e6c` | Uses the same older Linux-only driver loader; generator/family code is from the older `fish_drama3` layout. | Reuse newer shared platform work, but verify compatibility of family code and the Studio's protocol before changing the pin. |
| Music `8cd08c83` | Root build permits Windows. Driver uses `@import("sys")`; platform layer selects `nvcuda.dll`, Win32 loading and Windows memory policy. `music3-generate` target exists. | No clean-sheet shared-runtime port is needed. Actual Music compilation, stdio, loading peaks and generation still require reproduction. |
| Bolt `7d124f36` | Shared Windows platform support is present, but `zig/build/bolt.zig` invokes nvcc with `-fPIC`, `-lcuda` and an output named `libbolt.so`. | Native-server Windows support does not complete the specialized Bolt/LiLiCorr backend. Port and test that library separately. |

Immutable evidence:

- H3: [Linux build selection](https://github.com/drowzeys/TensorFold/blob/1f7ecc75ded5df6da151d4d5eeee87b35161e3c2/build.zig#L143),
  [driver loader](https://github.com/drowzeys/TensorFold/blob/1f7ecc75ded5df6da151d4d5eeee87b35161e3c2/zig/src/cuda/driver.zig#L13),
  [H3 OS calls](https://github.com/drowzeys/TensorFold/blob/1f7ecc75ded5df6da151d4d5eeee87b35161e3c2/zig/src/families/h3/cuda.zig#L27).
- Music: [Windows build selection](https://github.com/drowzeys/TensorFold/blob/8cd08c8350c02cf3d7d8eee5f1c1d9dfa22fa703/build.zig#L145),
  [platform implementation](https://github.com/drowzeys/TensorFold/blob/8cd08c8350c02cf3d7d8eee5f1c1d9dfa22fa703/zig/src/platform/sys.zig),
  [Windows host tests](https://github.com/drowzeys/TensorFold/blob/8cd08c8350c02cf3d7d8eee5f1c1d9dfa22fa703/zig/build/windows_host.zig).
- Drama: [pinned driver loader](https://github.com/drowzeys/TensorFold/blob/81173e6c612739a3427844fd8131cf4905e0a9c4/zig/src/cuda/driver.zig).
- Bolt: [specialized library build](https://github.com/drowzeys/TensorFold/blob/7d124f36df08a5836d3a5e3e85c19b6d919c70dc/zig/build/bolt.zig).

### NVIDIA driver-entrypoint check on this machine

The function names in the pinned TensorFold CUDA ABI tables were checked against exports of the installed
native ARM64 `nvcuda.dll`:

| ABI table inspected | Function entries | Missing exports |
|---|---:|---:|
| H3 pin, also representative of the inspected older Drama driver | 68 | 0 |
| Music pin | 76 | 0 |
| Bolt pin | 76 | 0 |

This removes one concrete uncertainty: no missing exported driver entrypoint was found in these tables.
It does **not** validate signatures, all calls, kernel compilation, CUDA libraries such as cuBLASLt, or numerical behavior.
No missing NVIDIA feature has been established as a blocker. Install/use the official ARM64 toolkit when a compile
requires it; report a minimal reproduction to NVIDIA only if a real driver/library/toolchain failure is found.

### More precise dependency preflight

PyPI wheel tags were checked against this actual Python 3.13 ARM64 interpreter, without installing packages:

- Compatible wheels: pinned Comfy Kitchen/AIMDO, PyAV, Tokenizers, SentencePiece, Safetensors, SciPy,
  ONNX Runtime and MediaPipe.
- No matching wheel in the checked releases: OpenCV Python `5.0.0.93`, Cryptography `50.0.2`,
  Tiktoken `0.14.0`, CTranslate2 `4.8.4`.
- ComfyUI's three pinned frontend/template/doc packages have pure-Python wheels. Other inspected direct
  requirements, including TorchSDE, Einops, Transformers, aiohttp, yarl, PyYAML, psutil, SQLAlchemy, BLAKE3,
  Spandrel and Comfy Angle, have matching native or pure-Python distributions.

Only the explicitly pinned packages were checked at their pinned versions; the other checks used the then-current
release. This is **availability preflight**, not a dependency-solver result or an import test. Native package
dependencies and optional extras may still fail. In particular, MediaPipe/ONNX wheel presence does not make
the entire Motion Previs stack available when its OpenCV dependency still needs work.

### Bounded experiments that would make estimates materially better

Do these before promising full feature dates. Ranges are proposed timeboxes, not predicted completion times.

| Experiment | Suggested timebox | Deliverable / decision |
|---|---|---|
| Resolve the pinned ComfyUI stack with a tested Windows-specific PyTorch constraint | 0.5-1 engineering day | Exact successful lock or first concrete dependency conflict; avoid modifying the working UI environment |
| Reproduce TensorFold's existing Windows host tests and CUDA smoke tests at an agreed revision | 1-3 days after toolchain provisioning | Logs, artifact architecture, driver/kernel results; no large weights required |
| Compile Music 3 and exercise startup/stdio protocol without model loading | 1-2 days after shared smoke passes | Confirm a real family generator uses the existing port, or enumerate remaining compile/link failures |
| Prototype Windows admission, pressure response and owned process-tree cancellation | 3-5 days | Bounded synthetic tests and explicit CPU/GPU budget semantics; not a model performance claim |
| Port H3's checkpoint/clock/loader seam and compile a tiny library/kernel test | 3-5 day discovery timebox | Actual residual change list; a 48 GB host can test infrastructure, not qualify full H3 inference |
| Render one licensed, low-memory workflow under an independent watchdog | 1-3 days after runtime/dependency gates | Output probe, cold/warm peaks, cancellation and repeated-run result |

These are dependent tasks where indicated, not a calendar sum or parallelism promise. A failed gate should
produce a narrowed blocker and a revised estimate instead of consuming an open-ended porting budget.

**Revised confidence:** high that a native Windows path exists and can be reused; moderate that a useful small-model
Studio subset is practical; low on exact family completion dates until compile/smoke receipts exist. There is still
no basis to promise current H3/Drama routes on 48 GB or complete 128 GB parity without that hardware.

### Publication readiness

The experimental fork is
[ivberg/Imagination-Studio-by-keys-Wasserman](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman).
The initial plan and Windows UI preview are committed and published on
[port/windows-rtx-spark](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/tree/port/windows-rtx-spark).
The local checkout's `origin` fetch and push URLs point at this fork.

The [issue draft](./windows-rtx-spark-issue-draft.md) includes full links to the published branch,
[plan](https://github.com/ivberg/Imagination-Studio-by-keys-Wasserman/blob/port/windows-rtx-spark/docs/windows-rtx-spark-plan.md),
launcher and tests. The exploratory discussion is now posted upstream as
[issue 195](https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/issues/195); no automated progress
comment has been posted.

## 11. First implementation results

The exploratory issue is now posted as
[drowzeys/Imagination-Studio-by-keys-Wasserman#195](https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman/issues/195).
The following results are local development checks, not a feature-support announcement.

### Isolated GPU baseline

- Added [Windows GPU requirements](../app/requirements-windows-gpu.txt), using NVIDIA's official ARM64 index.
- Added [setup/verify script](../scripts/setup-windows-gpu.ps1) and [tiny-operation checker](../scripts/windows-gpu-check.py).
- Uses a separate `%LOCALAPPDATA%\ImaginationStudio\gpu-venv`; the UI environment remains separate.
- Verified Python 3.13 ARM64, PyTorch `2.14.0+cu134`, TorchVision `0.29.0+cu134`, TorchAudio `2.11.0+cu134`,
  NumPy `2.4.6` and Pillow `12.3.0`. `pip check` passes.
- Passed fp32/fp16/bf16 addition, matrix products and scaled dot-product attention against known references.
- Passed TorchVision's native CUDA NMS and TorchAudio's CUDA resampling checks.
- Peak tensor allocation reported by PyTorch: **33,561,600 bytes**, about 32 MiB. This is not total process/driver
  memory, and is not a model memory estimate.
- CUDA budget query: **38,260,967,424 bytes total** (35.63 GiB). No large allocation or spill-limit test was performed.
- Setup and subsequent `-Verify` execution both passed. The existing loopback UI still responds as UI-only.

### Pinned ComfyUI dependency resolution

[Windows ComfyUI requirements](../app/requirements-windows-comfy.txt) retain revision
`b9b290e43f1eb379946dfeb013faf9bbefa1a618` and constrain the already verified Windows GPU packages.

A real `pip install --dry-run --only-binary=:all:` resolution succeeded:

- 72 additional distributions resolved.
- All resolved distributions were wheels; no source builds were selected.
- No PyTorch replacement was selected.
- Includes pinned Comfy Kitchen `0.2.39`, AIMDO `0.5.6` and the existing frontend/template/doc package pins.

This is stronger evidence than the earlier wheel-availability audit, but is still **not** an installation,
import check, server startup, native-kernel test for those packages, or model render. The transitive dependency
set is not yet a tested lockfile. Optional node-pack dependencies were not included.

### Validation and next gate

42 focused tests passed across the new diagnostic, existing Windows UI, credit and privacy checks. Hardware-free
diagnostic tests include wrong/nonfinite results, missing CUDA, invalid device selection and nonzero error exits.
No model weights were downloaded, no render routes were enabled, and no existing memory limits were lowered.

The next gate at this point was installation and no-model startup/shutdown; its results are recorded below.

## 12. No-model ComfyUI lifecycle results

The resolved dependencies are installed in the separate GPU environment; `pip check` passes. The source checkout
is at `b9b290e43f1eb379946dfeb013faf9bbefa1a618`, reporting ComfyUI `0.42.0` and PyTorch `2.14.0+cu134`.

Added [ComfyUI setup/verify tooling](../scripts/setup-windows-comfy.ps1) and a
[bounded lifecycle checker](../scripts/windows-comfy-check.py):

- Source revision and clean-checkout checks; no automatic reset of existing files.
- Temporary empty model/state folders and a loopback-only instance.
- Custom/API nodes and dynamic VRAM disabled; PyTorch attention selected.
- Readiness verified through `/system_stats`, `/object_info` and `/queue`.
- The returned argument vector must match this check's unique temporary directory, so an unrelated server
  answering the same port is not treated as success.
- A CUDA device and the required core loader/text/decoder nodes were reported; **679 nodes registered**.
- The queue stayed empty; no workflow was submitted and no model weights were downloaded.
- Cleanup uses the original launched process handle and a private Windows job object, not a port/PID search.
  The job is terminated/drained before temporary-file cleanup, including descendants.

The first cold check exposed a Windows file-release race at cleanup. The harness now waits for the owned job's
active-process count to reach zero before deleting its temporary state. Subsequent real lifecycle checks passed.
The console-stop exit code was `0xC000013A`; this is recorded as console termination, **not** claimed as graceful
draining of a running render. No running-render cancellation has been tested.

[Version constraints](../app/requirements-windows-comfy-constraints.txt) capture the 86 installed distributions
used for this baseline. These are version pins, not a hash-locked supply-chain manifest, and do not qualify
unexercised node packs, kernels or model routes.

Hardware-free protocol tests and a native Windows parent/child job-drain test cover instance identity, CPU-only
responses, incomplete core readiness, unexpected queue contents and source-pin refusal. The job-drain test uses
ordinary sleeping processes, not GPU/model work.

The combined lifecycle, GPU diagnostic, UI, credit and privacy checks passed. A real deliberately short
startup-deadline run also exited nonzero rather than claiming readiness, drained its owned process job, and left
the existing UI available.

Next: establish conservative Windows host/GPU admission and a model-job memory watchdog before enabling any
render path. Optimized ComfyUI allocators, optional nodes, model loading and inference remain unqualified.

## 13. Preliminary Windows admission and watchdog foundation

This section records the first prototype. Its inherited automatic host reserve was superseded by the
Windows-specific policy and measurements in [section 14](#14-os-aware-sustained-windows-diagnostic).

[Windows memory helpers](../app/windows_memory.py) now provide native installed-physical/host memory readings,
validated separate host/CUDA snapshots, reserve/pressure decisions and a measured-profile admission function.
The existing UI telemetry reuses the host-memory reader; it does not start a GPU sampler in the UI environment.

The preliminary policy:

- Host reserve: at least **8,000 MiB**, or 10% of installed physical RAM if larger. This retains the repository
  guard's current minimum rather than lowering it for a smaller machine.
- CUDA reserve: **2,048 MiB**, consistent with the current device margin.
- Larger `SPARK_MEMORY_FLOOR_MB` overrides are honored; invalid values fail explicitly and smaller values cannot
  lower the base reserve.
- Unknown/unmeasured profiles are refused. A profile must match the measured physical/host split.
- Host peak allowance must include CPU staging and shared-GPU growth. CUDA peak allowance covers
  dedicated/shared allocations. Compare these independently; do not add overlapping free-memory figures.
- Floors and short-term falling-headroom checks are infrastructure safeguards, not calibrated model estimates.

The no-model diagnostic has an opt-in `-Guarded` mode. A separate sampler process emits memory snapshots, while
an independent supervisor rejects pressure, malformed/stale data, sampler exit or lost heartbeats. If a CUDA
query stalls inside the sampler, the supervisor can still stop the private Windows job. Only the diagnostic's
owned sampler/backend and their descendants are terminated; no port/PID search is used to select victims.
The original unguarded no-model diagnostic retains its behavior and does not qualify model safety.

Synthetic tests cover independent budgets, exact reserve boundaries, profile mismatch, unknown routes, invalid
readings, falling headroom, sampler failure/staleness, healthy monitoring and preservation of an unrelated
sleeping process. These are deterministic infrastructure tests, not GPU memory-pressure measurements.

On the actual 48 GB dev box, the guarded run observed **6,146 MiB free host RAM** and refused startup against the
**8,000 MiB** reserve. Its owned sampler was stopped and the existing UI remained available. This is an expected
safe refusal for that observed configuration, not proof the machine can never qualify a smaller workflow.

No Windows model peak profile has been qualified, no render route has been enabled, and no Linux/Mac measurement
or guard has been changed. Next: validate sustained guarded operation with adequate host headroom, then measure
one low-memory route under independent protection before wiring admission into model submission.

## 14. OS-aware sustained Windows diagnostic

The fixed 8,000 MiB host cutoff was inherited from the Linux guard, not established by Windows measurements.
It blocked the no-model diagnostic even when Windows had no low-memory notification and substantial commit
headroom. The Windows diagnostic now uses Windows' pressure signals rather than that automatic cutoff.
Linux/Mac guards are unchanged.

### Current preliminary policy

- Query `CreateMemoryResourceNotification` / `QueryMemoryResourceNotification` for Windows low physical memory.
- Read system commitment, commit limit and kernel nonpaged residency through `GetPerformanceInfo`.
- Honor an explicit positive `SPARK_MEMORY_FLOOR_MB`; there is no automatic 8,000 MiB Windows host floor.
- Retain the 2,048 MiB CUDA margin and failure on invalid/stale readings or loss of monitoring.
- Read NVML dedicated total/free memory for the same GPU UUID CUDA selected, not by assuming NVML/CUDA indexes match.
- Read the actual ComfyUI backend's reported reclaimable CUDA headroom independently after readiness.
- A measured profile must account separately for resident staging/shared growth, CPU/shared commitment and
  target-backend CUDA demand. Pageable demand may exceed currently available physical RAM if commit headroom
  permits it; this is not a guarantee that residency, pinned allocations or subsequent allocation calls will succeed.
- No model profile or submission route is qualified by this change.

### Cross-process visibility check

An owned child allocated a **16 MiB tensor**, without a model. Its CUDA free-budget reading decreased by
**56 MiB** including allocation overhead, while the sampler process's CUDA reading did not change. NVML's global
dedicated-free figure decreased by **174 MiB**, including the child context.

Therefore the sampler's CUDA free figure must not be used as another process's remaining GPU budget.
The sustained diagnostic separately polls ComfyUI's `/system_stats` with a bounded timeout and checks the
responding instance's identity. Backend pressure or loss of its budget reporting stops the owned job even when
the sampler's own CUDA budget remains high. A synthetic regression explicitly exercises that distinction.

### Sustained no-model result on the 48 GB dev box

Command:

```powershell
.\scripts\setup-windows-comfy.ps1 -Verify -Guarded -HoldSeconds 30
```

The final run held the empty-queue backend for **30.03 seconds**, collecting **167 sampler snapshots** and
**57 backend-budget snapshots**, then stopped and drained the owned job.

| Observation | Minimum / result |
|---|---|
| Windows available OS memory | 4,420 MiB |
| Windows commit headroom | 26,352 MiB |
| Windows low-memory notification | False throughout accepted samples |
| Global dedicated GPU free memory | 24,395 MiB |
| ComfyUI process's reported reclaimable CUDA headroom | 36,288 MiB |
| Model/workflow submissions | None |

These figures describe different accounting domains and must not be summed. Dedicated memory is reserved
from the installed pool; shared GPU use competes with OS memory. Commit headroom includes virtual-memory backing,
not additional physical/GPU RAM. Background activity can affect these global minima; they are not model peaks
or measurements of paging performance.

The first backend-monitoring attempt exposed a startup-order error: polling began before readiness. That was
corrected, with a regression requiring backend monitoring to start only after core readiness. Subsequent sustained
checks passed.

This qualifies only bounded no-model monitoring on the observed configuration. Optional nodes, model load peaks,
allocation failure recovery and render-time safety still require independent measurements and validation.

## 15. First explicit Windows sound-effect calibration

After the model terms were accepted and Hugging Face login was completed locally, setup downloaded only the
Stable Audio Open checkpoint, T5 encoder and license. The two weight files total **5,292,414,880 bytes** and passed
their published SHA-256 checks at revision `f21265c1e2710b3bd2386596943f0007f55f802e`. They remain outside the repo.
No credentials are included in model parameters or reports.

The calibration reuses `spark_generate.sfx_workflow`: 5 seconds, seed 7, 100 passes, CFG 7, exponential schedule
and `dpmpp_3m_sde_gpu`, including the existing -12 dB output adjustment. It is an explicit diagnostic exception,
not normal job admission or an enabled Studio route.

### Loading experiment and failures

- The first attempt tripped an inherited linear forecast of falling available host RAM, despite no Windows
  low-memory signal. The default Windows policy now relies on OS pressure/commitment instead; host-RAM trend
  protection still applies if the user explicitly configures a host floor.
- The unchanged CPU-first loader then reached Windows' actual low-physical-memory signal and was stopped.
  This was a genuine guarded refusal; that protection was not disabled.
- Source inspection found host pinning already disabled for integrated GPUs. Disabling pinning would not fix
  this case.
- With explicit approval, a calibration-only GPU-first loader now calls ComfyUI's existing load helpers,
  initially loading checkpoint tensors to the selected CUDA device. It avoids retaining the same full
  CPU-side checkpoint staging and preserves the model/sampler settings. The ComfyUI source remains untouched.
- The first GPU-first run produced valid audio but exposed a shutdown-monitor race: an intentional connection
  close was treated as unexpected failure. Backend polling now distinguishes planned shutdown; independent OS
  monitoring stays active through cleanup. Both planned and unexpected disconnect cases have regression tests.

### Bounds and results

The same bounds were retained throughout:

- Native PyTorch allocation cap: **12 GiB**. A separate 1 MiB-cap / 16 MiB-allocation test demonstrated that the
  allocator refuses allocations above its configured limit.
- Windows process commit cap: **12 GiB**; aggregate owned-job commit cap: **16 GiB**. Applied values are queried
  back from the job object before the workflow is submitted.
- Runtime OS/backend monitoring remains active. No failed run raises limits automatically.
- One whitelisted calibration package contains the GPU-first loader and an in-process allocation receipt,
  catching peaks that periodic budget samples can miss.

Two subsequent GPU-first runs completed through validated output and owned shutdown. The final reproducible
PowerShell command also saved a `.calibration.json` report with tool-content hashes:

```powershell
.\scripts\setup-windows-sfx.ps1 -Verify
.\scripts\check-windows-sfx.ps1 -Calibrate
```

| Final calibration observation | Result |
|---|---|
| Audio | 44.1 kHz stereo FLAC, 221,184 samples/channel, **5.0155 s** |
| Peak audio level | **-14.746 dBFS** |
| Request to validated file | **10.73 s**, excluding setup/hash checks/server startup |
| PyTorch peak tensor allocation | 7,009,132,544 bytes, about **6.53 GiB** |
| PyTorch peak reserved allocation | 7,142,899,712 bytes, about **6.65 GiB** |
| Peak process commitment | 12,880,076,800 bytes, very close to the 12 GiB cap |
| Peak aggregate owned-job commitment | 14,701,903,872 bytes, below the 16 GiB cap |
| Minimum available OS memory | 1,241 MiB, with no low-memory signal in accepted samples |
| Minimum global dedicated GPU free memory | 17,101 MiB |

Job commitment is Windows accounting, not a measurement of CPU resident RAM. Global OS/dedicated minima can be
affected by background activity. PyTorch's cap and counters do not cover every driver/library allocation.
The process-commit peak was close to its cap: this is not evidence that broader prompts, lengths or workflows
have a comfortable margin.

The output decoded successfully with PyAV; duration, stereo layout, finite/non-silent samples, audio headroom,
allocator type and the actually applied GPU fraction were verified. Audio quality still needs a listening check.
No Windows SFX profile was automatically promoted, no UI render route was enabled, and no other model was loaded.

Next: review the loading/commit footprint, collect route-specific margins and additional controlled cases before
considering normal queue/UI integration. Keep OS and GPU budgets separate and retain explicit unavailable reasons.

## 16. Backend memory accounting and targeted cancellation

The accounting check now reports actual backend process memory, rather than inferring physical usage from
Windows job commitment. The initial `Popen` handle reading measured the virtualenv's small launcher shim.
The private job still contains the interpreter child and applies its caps to the whole tree; attribution now
comes from inside ComfyUI. Launcher figures are labelled separately, and process-handle attribution also checks
membership in the owned job.

A tiny controlled test found that a warmed 16 MiB GPU allocation changed working set by about 0.004 MiB and
private commitment by 0 MiB, whereas a 16 MiB CPU buffer increased working set by about 16.004 MiB and commitment
by 16.039 MiB. This is a small allocation observation, not a complete explanation of the model's commitment.

The subsequent normal five-second calibration completed with unchanged caps:

| Backend observation | Result |
|---|---|
| Before-workflow working set | About 0.97 GiB |
| Peak working set | **6,163,251,200 bytes, about 5.74 GiB** |
| After-workflow working set | About 2.63 GiB |
| Peak private commitment | About 12 GiB, near the process cap |
| Peak GPU tensor allocation | About 6.53 GiB |
| Audio | Same validated 5.0155-second stereo result |

Working-set peaks include mapped/shared pages and need not coincide with commitment peaks. Page-fault counts
include soft faults; they are not a measurement of disk paging. Do not describe the commitment figure as 12 GiB
of resident OS RAM, or add it to the GPU allocation figure as a physical-memory estimate.

### Cancellation test

```powershell
.\scripts\check-windows-sfx.ps1 -Calibrate -CancelDuringSampling
```

The test subscribes to the owned prompt's progress, waits for at least one completed step in node 12 (KSampler),
and calls the pinned server's atomic targeted job-cancel API. It does not issue a global interrupt.

The actual test confirmed:

- Cancellation accepted for the owned prompt after **step 1 of 100**.
- History recorded `execution_interrupted` for that same prompt in **KSampler**, not a later save node.
- The queue drained; **no audio was copied or saved as a successful result**.
- Observed cancellation-request-to-terminal-confirmation time: **about 0.51 seconds**.
- In-process allocation counters remained within the same GPU cap; job caps were unchanged.
- Owned backend/sampler processes were stopped and drained afterward.
- A normal complete calibration subsequently passed, checking that cancellation did not leave stale work behind.

The reported latency includes control-plane confirmation; it is not a GPU preemption benchmark. Cancellation
reports are explicitly labelled as tests, not completed generation or route qualification.

Next: qualify a small case matrix under unchanged bounds. Separately, prioritize low-memory script imports,
scene/shot planning and manual storyboard/reference-image flows; do not make those depend on a large brain or
image-generation model.
