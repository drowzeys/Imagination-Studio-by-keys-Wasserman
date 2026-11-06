# Windows setup and qualification

[Back to the README](../README.md#windows)

Moving to another computer? See [Moving an install](moving-an-install.md) for copying installed environments
and models, resetting machine-specific state, and reusing compatible weights on Mac or Linux.

## Native Windows

Studio runs natively on Windows; there is no WSL version (it was removed on 2026-11-06). It is one Studio with
per-feature backends, not separate basic/full editions or a global GPU capability tier. Planning, imports, settings and browsing work without generation
models. Images, Voice, Music, effects, Foley and CPU tools have their own installation and qualification status.
H3 video retains its own engine and pool prerequisites; it does not gate unrelated features.

Native CUDA setup currently targets **Windows 11 on Arm / RTX Spark N1X**. The UI environment can run without
the CUDA engine installation; that does not make an arbitrary Windows GPU supported.

### Install from the web

For a new install, run in an administrator PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/drowzeys/Imagination-Studio-by-keys-Wasserman/main/oneshot-setup-rtx-spark-n1x.ps1 -OutFile "$env:TEMP\studio-setup.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\studio-setup.ps1"
```

This clones Studio, installs selected components and registers Desktop/Start shortcuts and the tray.
Review model licences, download sizes and the runtime-fit plan before proceeding. Open **Imagination Studio**
from Desktop or Start when setup finishes.

### Start with the app only (local checkout)

Install the official Python install manager (`py`) and Git, clone this repository, then use PowerShell in the
checkout:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\app.ps1 -Install
```

Open <http://127.0.0.1:7870>. This installs UI dependencies, not model weights.
For setup without starting the server, add `-InstallOnly`.

Later, `powershell -ExecutionPolicy Bypass -File scripts\app.ps1` starts the app; Ctrl+C stops the foreground
launcher. The tray below is the background alternative.

### Adaptive component setup

Open **Settings → Models & weights**. Each row shows installed components at the configured Windows path,
download size, runtime-fit information and a native PowerShell command. **In place** means files are installed,
not that every recipe has passed a render check.

- **Known insufficient capacity:** automatic selection skips the missing model and its requested engine build.
  An explicit install stops before tooling/downloads unless you acknowledge the mismatch.
- **Temporary contention:** current free memory is lower than required, but the pool could meet the policy.
  This is not a permanent unsupported-machine result.
- **Unknown fit:** there is no declared envelope for that route. It is not labelled a success or a capacity failure.
- **Below 128 GiB RAM, matching shared failures with no passes:** failed tests on the same recorded OS/backend, device family/model,
  architecture and physical/dedicated capacities block missing-model downloads before setup. Automatic setup
  skips the affected component. This is a risk acknowledgment, not a claim that every recipe is impossible.
  At 128 GiB or more, shared failures warn only. This download-risk gate is separate from declared
  admission policies and live safety guards, which remain unchanged.
- **Mixed shared results:** some tested presets passed, so download is allowed with a warning that other
  presets failed. Local qualification and runtime guards still apply.
- Models & weights leads with observed memory and a short download recommendation, not shared attempt counts.
  Below 128 GiB, failure-only evidence also discourages downloads on lower RAM/GPU capacities in the same
  execution and memory-topology family. The copy-command button requires acknowledgment and includes the
  override flag. Passing recipes are not extrapolated to smaller machines. Unified-memory Spark commit
  failures are not applied as hard blocks to discrete NVIDIA cards.
- **No matching shared metadata:** setup logs a warning and continues; missing evidence alone never blocks.
- **Existing files:** retained and reused; nothing is deleted to force a fresh download.

The UI withholds an incompatible missing model's setup command until **download anyway** is acknowledged.
The copied command then includes `-AllowInsufficientModel`. This flag also explicitly overrides matching
shared-test failures. For the image-only installer use `-AllowUnverifiedImageModel` to accept matching failed
image recipes. Unknown-image metadata warns without requiring either flag. Neither changes runtime guards.
`-NoDownload` and complete cached weights remain reusable without a new download acknowledgment; `-PlanOnly`
performs no probes or installation.

The shared [model catalog](../app/model_catalog.json) holds file lists, pinned checksums, published download
sizes and runtime policies. Setup and the engines read the same admission values. Sizes are model payload,
not RAM requirements or a total disk/dependency-install estimate. Metadata refresh:
`python scripts\refresh-model-sizes.py` (repository metadata only, no weights).
New models on an existing supported layout can extend the catalog; a new renderer still needs implementation.

Models & weights sections can be collapsed; headings summarize installed, missing, found and conversion states.
An installed model with a Feature checks link omits the generic unknown-fit message, while known capacity
warnings remain visible. Installation status is separate from qualification.
Where local recipe evidence exists, model information summarizes passed/total and failed recipes instead:
some recipes can be verified while others fail or remain untested. Failed checks are not permanent capacity
limits. Observed recipe peak ranges are shown separately for adapter-wide GPU usage and owned job commitment.
The GPU figure includes other applications; neither range is a model's minimum memory requirement.
A `+` marks a range including a memory-limited run stopped before completion, whose full-run peak may be higher.
The limiting pool is named when the saved error identifies it. GPU usage and commitment overlap; do not add them.

Installed catalog weights offer **Remove model weights → Copy uninstall command**. This only copies a command;
it never deletes through the browser. Stop Studio and its engines, then run the command in PowerShell to preview
exclusive weight files and confirm by typing the model id. `-PlanOnly` previews without prompting or deleting.
Shared files are preserved even if the other catalog model is not installed. Engines, environments, configuration,
projects, media, cached downloads and qualification receipts stay intact; reinstalling is separate.
Linked/junction/hard-linked weights and external model overrides require manual review and are refused by this
helper. Some components may have no exclusive weights; engine-only rows and SD-Turbo do not offer this action.

### Component commands

Use the command shown by the app so the model and backend are explicit. Examples:

```powershell
# Pure plan: no installation, filesystem or hardware probes.
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-cuda.ps1 -Parts image -PlanOnly

# Conservative automatic image setup (currently SD-Turbo, not predictive native-model matching).
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-images.ps1 -Model auto

# Deliberate native Qwen Turbo install.
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-images.ps1 -Model qwen -AllowUnverifiedImageModel

# Optional audio: fit is checked before tooling or model downloads.
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-cuda.ps1 -Parts voice -Voice
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-cuda.ps1 -Parts music
```

Other individual setup scripts cover FFmpeg, graphics, Tesseract Finish, SFX and isolated Foley dependencies.
CPU HDR/Finish do not depend on H3. Image-only setup does not build H3 or obtain its compiler/toolkit.
Voice and Music native engines may require Zig, MSVC ARM64 and the CUDA toolkit; setup currently builds those
engines from pinned TensorFold source. Repeated setup reuses completed parts.

For the broad N1X installer, use the instructions under **Native Windows** in the
[N1X reference](rtx-spark-n1x.md#install-one-shot-natively). It uses automatic selection by default; explicitly
supplied `-NativeParts` select an explicit set. Review disk usage and licences before running it.

FLUX3-K and Stable Audio Open require Hugging Face access approval. Accept the repository's terms with the
same account used by your local Hugging Face login/token. Missing access is checked before model downloads;
`HF_TOKEN`, if set, takes precedence over the saved login.

## Qualification

Open **Machine → Feature checks** to run a check. Checks use the normal one-at-a-time queue; opening the page,
starting Studio or downloading a model never runs inference automatically.

| Installed physical RAM | Default policy |
|---|---|
| Below 128 GiB, or unknown | Qualification gating and badges are required |
| **128 GiB or more** | Gating and badges are **off by default**, optionally enabled |

At 128 GiB+, installed supported features do not require a qualification render just to become usable.
Individual checks and diagnostics remain available. **All machines keep installation prerequisites,
backend-specific policies, supported recipe bounds and live memory admission/kill guards.** This is not a
blanket 128 GiB minimum or a guarantee that a model fits.

### What a pass means

- Each model/backend has independent results. Turbo's pass does not qualify Qwen Full or FLUX3-K.
- Image recipes check exact canvases and sequential counts. Passed larger dimensions do not silently certify
  other orientations or arbitrary pixel areas. The page/API refuse unchecked settings when gating is on.
- Groups run unchecked recipes in order and save each success independently. Cancel or a larger failure keeps
  previous passes. Memory/backend/timeout/drain failures stop a group; ordinary output errors may continue.
- Green checkmarks mean complete, yellow progress means partial, and a red cross means an unchecked recipe
  failed. Category totals give each feature one point, shared equally among its recipes.
- Failed attempts keep available measurements and error details. A guard refusal is not an OOM measurement
  or proof of a minimum RAM requirement.

**Initial native audio checks:** Voice covers short plain speech (up to 20 words / 120 characters), default
sampling, no cloning, directions, streaming or transcription. Music covers a ten-second MiniMax-Music3
instrumental on its CUDA int8 backend at default sampling. Longer speech/songs, reference voices and other
engines are not qualified by those checks. They validate runtime/output, not artistic quality or word accuracy.

Existing bounded SFX, Foley, graphics, HDR and Finish checks retain their own scopes.
See the [Windows testing runbook](windows-rtx-spark-testing.md) for detailed recipes and measured limits.

### Library workflows and Character Swap

The Library bundle is not one qualification: **Turnaround**, **Upscale**, **Find a person**, **Remove a person**
and **Character Swap** have independent cards and saved results. Models & weights links to each check.
An installed bundle means those component files are present, not that every shared H3 file, node pack or
action LoRA is installed. Each check lists missing prerequisites before it can run.
`-Parts workflows` installs the node packs and auxiliary models, `-Parts h3-shared` the shared text
encoder/decoders, and `-Parts library-h3` the bf16 Ref2VA transformer used by generative actions. These
component-only parts do not build H3's DLL or download FastH3's unrelated transformer. Install the action's
Turnaround/Upscaler/Person Remover LoRA separately through the LoRA Hub. Swap uses `swap,h3-shared`.

Checks consistently use the repository's generated baker still and five-second clip, with SHA-256 identities
included in their recipes. No personal media, downloaded fixture or preliminary model generation is required.
CPU-only preparation creates isolated resized/trimmed copies; originals are unchanged.

| Action | Initial tested recipe |
|---|---|
| Turnaround | Input at most 512×288, five 512×512 views, strength 1, 28 production passes |
| Upscale | 512×288 silent input, 22 frames at 24 fps; 1 MP output, denoise 0.1 |
| Find a person | Same 22-frame silent input; SAM mask, matte and reconstructed plate |
| Remove a person | Same input; 22-frame windows, 12 passes, automatic clean frame, silent output |
| Character Swap | Five-second 864×480/24 fps shot, 512×288 character reference, quality 480, anchor off |

These are real production graphs/builders, not replacement standalone model implementations. Find uses SAM
and does not require the H3 DLL, H3 transformer or an action LoRA. Turnaround/Upscale/Remove on Windows require
the bf16 Ref2VA transformer and their own LoRAs: runtime merging into the int8 file is refused rather than
silently corrupting output. Swap uses its production weight-route selection and memory preflight; its first
pass does not qualify the optional image-edit anchor, high resolution, longer/multiple windows or visual likeness.

Qualification validates that all required media outputs exist and have bounded dimensions/frame timing;
Find also rejects an empty or full-frame matte. It is not a semantic/creative-quality certificate: inspect
the actual mask, views, removed subject or swapped result. Larger inputs/settings and unrelated actions are
not enabled by a pass. With gating off, existing native prerequisites and live guards still apply.
All runtime checks keep native Windows memory floors, owned cancellation/deadlines and drain before saving
a success; failures retain observations and never remove an earlier pass. No checks run just by opening the UI.

### Memory and evidence

Installed RAM, available host RAM, dedicated GPU pool and Windows commit backing are different limits; do
not add overlapping pools together. A 64 GB N1X may expose about 30.5 GiB to its GPU: that cannot meet Voice's
current 50 GiB free-pool admission policy. A 96 GB machine still needs a suitable pool and live headroom.
The number is an engine admission policy, not a measured minimum of installed Windows RAM.

Balanced update matching retains dated evidence across compatible source/tool updates, with a runtime-updated
note. Model, recipe, hardware, ABI or execution-contract changes can require a new check.
`STUDIO_QUALIFICATION_STRICT=1` opts into exact provenance matching. Changing the policy never fabricates a pass.
Model timestamps trigger identity verification, not automatic re-rendering.

## Start and stop

Register the current checkout without downloading anything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-desktop.ps1
```

Open **Imagination Studio** from Desktop/Start. The notification tray offers Open, **Start Native Windows** and
Stop Studio. Stopping requires confirmation and an idle queue: the controller authenticates its own launch, drains
owned model backends and waits for shutdown. It does not kill processes by name or adopt an app started manually.
Such an app is labelled **External Studio**: stop it from its original launcher first. **Exit tray** leaves Studio
running.

A WSL2 registration left by an older install is ignored, and dropped the next time the native Studio registers.
Relaunch the tray after updating its script; refreshing the browser only updates the web UI.

## Files, maintenance and troubleshooting

- **Projects/media:** `%USERPROFILE%\ImaginationStudio` or `STUDIO_HOME`.
- **Native environments/models:** `%LOCALAPPDATA%\ImaginationStudio`, unless configured otherwise.
- **Read-only audit:** `scripts\check-windows-install.ps1`; add `-All` only when every optional component is
  expected to be installed. It does not download, render or replace first-run qualification.
- **Updates:** save work, stop the app, pull the chosen Git branch and rerun the relevant PowerShell component
  setup if dependencies/engines changed. Source updates and weights are separate. Do not run bash setup in
  Native Windows.
- **Remote devices:** localhost needs no browser key. Other devices use Settings → Access key; retain the
  default loopback binding unless remote access is intended. See [remote access](user-guide.md#using-it-on-your-phone).

More detail:
[N1X implementation, memory and measurements](rtx-spark-n1x.md) ·
[Windows testing runbook](windows-rtx-spark-testing.md) ·
[Measured results](windows-rtx-spark-test-results.md).
The older [Windows implementation plan](windows-rtx-spark-plan.md) and
[posted issue draft](windows-rtx-spark-issue.md) contain historical milestones, not the current install overview.
