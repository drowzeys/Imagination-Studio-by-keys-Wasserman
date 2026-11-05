# Windows setup and qualification

[Back to the README](../README.md#windows)

## Native Windows

Native Windows is the first Windows path: one Studio with per-feature backends, not separate basic/full
editions or a global GPU capability tier. Planning, imports, settings and browsing work without generation
models. Images, Voice, Music, effects, Foley and CPU tools have their own installation and qualification status.
H3 video retains its own engine and pool prerequisites; it does not gate unrelated features.

Native CUDA setup currently targets **Windows 11 on Arm / RTX Spark N1X**. The UI environment can run without
the CUDA engine installation; that does not make an arbitrary Windows GPU supported.

### Start with the app

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
- **Existing files:** retained and reused; nothing is deleted to force a fresh download.

The UI withholds an incompatible missing model's setup command until **download anyway** is acknowledged.
The copied command then includes `-AllowInsufficientModel`. Unknown native image installs have a separate
`-AllowUnverifiedImageModel` acknowledgment. Neither changes runtime guards.

The shared [model catalog](../app/model_catalog.json) holds file lists, pinned checksums, published download
sizes and runtime policies. Setup and the engines read the same admission values. Sizes are model payload,
not RAM requirements or a total disk/dependency-install estimate. Metadata refresh:
`python scripts\refresh-model-sizes.py` (repository metadata only, no weights).
New models on an existing supported layout can extend the catalog; a new renderer still needs implementation.

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

### Memory and evidence

Installed RAM, available host RAM, dedicated GPU pool and Windows commit backing are different limits; do
not add overlapping pools together. A 64 GB N1X may expose about 30.5 GiB to its GPU: that cannot meet Voice's
current 50 GiB free-pool admission policy. A 96 GB machine still needs a suitable pool and live headroom.
The number is an engine admission policy, not a measured minimum of installed Windows RAM.

Balanced update matching retains dated evidence across compatible source/tool updates, with a runtime-updated
note. Model, recipe, hardware, ABI or execution-contract changes can require a new check.
`STUDIO_QUALIFICATION_STRICT=1` opts into exact provenance matching. Changing the policy never fabricates a pass.
Model timestamps trigger identity verification, not automatic re-rendering.

## Start, stop and switch backends

Register the current Native checkout without downloading anything:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-desktop.ps1 -Backend native
```

Open **Imagination Studio** from Desktop/Start. The notification tray offers Open, Start/Stop, backend selection
and a default for future launches. When stopped it says **Start Native Windows**; it does not offer switching to
the already running backend. Changing the default does not stop the current app.

Switching requires confirmation and an idle queue. The controller authenticates its own launch, drains owned
model backends and waits for shutdown before starting the other runtime. It does not kill processes by name,
shut down WSL distributions or adopt an app started manually. Such an app is labelled **External Studio**:
stop it from its original launcher first. **Exit tray** leaves Studio running.

Both one-shot installers register with the same tray. Registration preserves the other install and the existing
default; `-MakeDefault` changes the default explicitly. Relaunch the tray after updating its script; refreshing
the browser only updates the web UI.

## WSL2

WSL2 is a separate Linux Studio installation, not an automatic fallback for a native job.
On an N1X, run the one-shot installer **without `-Native`** from an administrator PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/drowzeys/Imagination-Studio-by-keys-Wasserman/main/oneshot-setup-rtx-spark-n1x.ps1 -OutFile "$env:TEMP\n1x.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\n1x.ps1"
```

Review the broad default component selection and disk requirements first. This installs WSL2/Ubuntu and the
Linux runtime; its model memory policy is not the Native Windows qualification table above.
N1X's Linux guard has an explicitly configured warning policy; the DGX Spark strict policy is not relaxed.

To register an existing **updated** WSL Studio from the Windows checkout:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-desktop.ps1 -Backend wsl -Distro Ubuntu -WslUser studio-user -WslRepo /home/studio-user/Imagination-Studio
```

Replace the example distro/user/path with that installation's values. Both checkouts need desktop-control
support. Registration/start checks only the specified distro and path, with a timeout. The tray keeps the
Windows WSL process alive; a Studio launched over SSH alone may end with the SSH session.
Native and WSL models, Python environments and project folders stay separate. No migration or concurrent
GPU-backend launch is implied by switching.

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
[N1X native/WSL implementation and measurements](rtx-spark-n1x.md) ·
[Windows testing runbook](windows-rtx-spark-testing.md) ·
[Measured results](windows-rtx-spark-test-results.md).
The older [Windows implementation plan](windows-rtx-spark-plan.md) and
[posted issue draft](windows-rtx-spark-issue.md) contain historical milestones, not the current install overview.
