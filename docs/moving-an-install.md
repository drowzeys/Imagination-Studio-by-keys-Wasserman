# Moving an install or reusing downloads

[Back to the README](../README.md) · [Windows setup](windows.md) · [Mac and Linux setup](installation.md)

**You can copy models and preserve installed, compiled Python packages.** A copy is not a portable installer,
though: whether its runtimes work depends on the destination's OS, CPU architecture, Python installation,
GPU and drivers. There is no automatic migration/relocation tool or verified end-to-end clone procedure here.
Keep the source installation intact until the destination works.

## What can travel?

| Item | Similar Windows machine | Different architecture or OS |
|---|---|---|
| Model files, tokenizers, configs and adapters | Reuse the complete expected files | Reuse only the exact format/revision the destination backend expects |
| Installed Python environments, including compiled extensions | Preserve and try them; paths and external dependencies may need repair | Recreate with destination packages |
| Cached `.whl` packages | Reusable when their Python/ABI/platform tags and runtime dependencies match | Only compatible wheels; Windows ARM64 wheels do not work on macOS ARM64 or Windows x64 |
| Native engines, DLLs and executables | Potentially reusable with matching architecture, engine version and GPU/runtime support | Install/build the destination engines |
| Projects, uploaded media and renders | Copy separately; review external paths and private settings | Copy separately; review paths and feature availability |
| Qualification receipts and process/ownership state | Do not carry forward as destination verification or ownership | Do not reuse |

Having the same amount of RAM, or both CPUs being ARM64, is not sufficient compatibility.
Native Windows CUDA currently targets Windows on Arm / RTX Spark N1X; this guide does not make an arbitrary
x64 Windows GPU a supported target.

## Before copying

1. Stop Studio and its owned model backends through their normal launchers. Wait for active work to finish.
   Do not copy a live environment during installation, model download or rendering.
2. Record the Studio revision, selected features, model path overrides and Python versions. Start with the same
   Studio revision on the destination so migration is not also an upgrade.
3. Use a trusted private backup. A complete copy may include credentials, access keys, settings and private
   project media. Do not publish it or hand it to another person as a redistributable bundle.
4. Allow room for the copy, external files and any repairs/conversions. NTFS is preferable for Windows runtime
   copies; exFAT is useful for transporting ordinary model files between Windows and Mac, but does not preserve
   all permissions or links. FAT32 cannot hold files over 4 GB.
5. Account for junctions, symbolic links and hard links. Copy their required data or recreate valid destination
   links; a link to the source computer is not a backup. Materializing shared files can increase disk usage.
6. Compare file sizes and SHA-256 hashes after transfer, especially large weights. An installer's
   "already present" check is not always a full integrity check. Copy only trusted code and model files.

Model licences still apply to private copies and use on the destination. Do not redistribute restricted weights
or copy account tokens to bypass gated-model access. See [licences](licensing.md).

## Similar Windows machine: preserve the installed packages

The native runtime root is `%LOCALAPPDATA%\ImaginationStudio`. It is worth copying the **whole folder**, not
just `models`, when avoiding source builds is the goal. Important contents include:

- `gpu-venv`: shared GPU/ComfyUI Python packages.
- `foley-gpu-venv`: Foley's isolated packages, including its native dependencies.
- `ui-venv`: the Studio app's Python packages.
- `train` and other installed feature directories, where present.
- `ComfyUI`, its custom nodes, engine directories and `models`.

Installed modules are normally under each environment's `Lib\site-packages`; compiled modules can be `.pyd`
files with companion DLLs, not `.whl` archives. Preserve the complete environments and feature source folders,
not just individual extension files. Editable installs can refer to source outside the copied folder.

### Transfer and repair

1. Before installing large components on the destination, copy the runtime root to its own
   `%LOCALAPPDATA%\ImaginationStudio`. Do not merge it blindly into an existing installation; back that up first.
2. Install the matching base Python version/architecture and required system dependencies on the destination.
   The venv may reference the source Python's absolute installation path in `pyvenv.cfg`. Matching paths,
   including the user-profile path, improve the chance of reuse but do not guarantee it.
   NVIDIA drivers, external CUDA/toolchain files, Git and registered applications are not all inside this folder.
3. Put the matching Studio checkout at its intended permanent destination. The runtime-root copy does not
   necessarily include the checkout, shortcuts or projects.
4. Exclude the verification and live-process state described below. Regenerate machine-specific configuration:
   `cuda-tier.json`, ComfyUI's `extra_model_paths.yaml`, model overrides and launcher paths can name the source
   machine. The CUDA record also contains capacity measured on that machine.
5. Try the copied Python environments before reinstalling packages. From PowerShell, this example checks paths
   and dependency metadata without loading models:

   ```powershell
   $Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\gpu-venv\Scripts\python.exe'
   & $Python -c "import sys, platform; print(sys.version); print(platform.machine()); print(sys.prefix); print(sys.base_prefix)"
   & $Python -m pip check
   ```

   Repeat for `ui-venv` and `foley-gpu-venv` when installed. Success does not verify DLL loading, GPU execution or
   model memory fit. If Python cannot start, fix its base-Python/path dependency first.
6. Repair only the affected environment or feature if needed, retaining the original copy for recovery.
   There is no supported blanket find-and-replace for venv relocation: executable launchers, editable paths and
   external DLL dependencies can all need different repairs. A fresh environment may ultimately be necessary,
   but that does not require deleting or downloading the model files again.
7. Use the destination's [component setup commands](windows.md#component-commands) to restore configuration and
   prerequisites where needed, then register its [tray and shortcuts](windows.md#native-windows).
   Setup is not a migration-only operation: it may install/update dependencies or build engines. Review the
   selected parts before running it; do not assume rerunning the full installer is free or offline.
8. Start normally, review diagnostics and run the checks needed for the features you intend to use.

The native CUDA component installer supports `-Models` for an existing model folder and `-NoDownload` to skip
its model-download stage. This is **not** an offline-install switch for Python packages, tools or every optional
feature installer. Keep each feature's configured model paths consistent.

### Pip cache is an extra, not the installed environment

Pip's cache is normally outside Studio, commonly `%LOCALAPPDATA%\pip\Cache`, and may be overridden.
Ask the relevant environment rather than assuming:

```powershell
& $Python -m pip cache dir
& $Python -m pip cache list
```

The `wheels` subdirectory may contain locally built wheels, but need not contain every installed package.
Downloaded wheels can instead be in pip's HTTP cache; caches may also have been cleared or disabled.
Preserve a useful cache separately, but do not assume it can reconstruct the environments offline.

If you already retained compatible built wheels, install those into a fresh environment rather than compiling
again. Installed `site-packages` is not itself a wheel archive, and `pip freeze` records dependencies, not
their binaries. There is no general pip command that losslessly exports an installed environment back to wheels.
Keep Foley's isolated environment separate from the shared GPU environment.

## Qualification, identity and private state

Exclude destination copies of:

- The runtime root's `qualification` directory, or the directory selected by `STUDIO_WINDOWS_FEATURE_DIR`.
  This also resets any saved qualification-mode choice; destination policy then applies.
- Legacy/baseline receipts: `models\SD-Turbo\qualification.json`,
  `models\Sound-Comfy\stable-audio-open-1.0\qualification.json` and `graphics\windows-checked.json`.
  Image/sound overrides can place these elsewhere. Preserve the model files beside the receipts.
- Transient PID, lock, stop and backend-ownership records. Never use copied records to stop or adopt processes
  on the destination; rebuild launcher/backend state through normal destination setup.

Qualification matching checks hardware/runtime compatibility, but is not a universal unique-computer identity.
Some checks compare capacity profiles; two physical machines can match. Do not rely on automatic invalidation
to turn source-machine evidence into a new-machine test. Deleting receipts does not automatically run renders;
use **Machine -> Feature checks** explicitly. Qualification gating defaults depend on destination capacity;
prerequisites, supported recipes and live memory guards apply regardless.

The Studio data folder is separate: normally `%USERPROFILE%\ImaginationStudio` on Windows or
`~/ImaginationStudio` on Mac/Linux, overridden by `STUDIO_HOME` (older installs may use `TensorFoldStudio`).
Copy projects/media separately if wanted. Review settings and external references, and do not clone
`access-key`, account credentials or private worker identities into a new computer's configuration.
Let the destination establish its own access key.

## Different Windows hardware, Windows to Mac, or Windows to Linux

Reuse compatible **data**, not the source runtime. Install the destination's Python environments, engine
binaries, drivers/frameworks and launchers using its own setup instructions. Even Windows ARM64 to macOS ARM64
requires different binaries: CUDA/Windows packages are not Metal/MLX/macOS packages.

A model name or `.safetensors` extension does not prove compatibility. Check repository, revision, tensor
layout/names, quantization, configs and required encoders/decoders against the destination installer. Copy the
complete expected set, including shard indexes, tokenizers, processors and licence files. Do not rename a
converted/quantized checkpoint to make it look like the expected file.

Current examples, checked against the installers:

| Model | What can be reused across Windows and Mac |
|---|---|
| MOSS-Image | Both use Comfy-Org's `MOSS-Image` files: `diffusion_models/moss_image_sft_bf16.safetensors`, `text_encoders/qwen3.5_9b_bf16.safetensors` and `vae/flux2-vae_small.safetensors`. Windows stores them under `models\MOSS-Image-Comfy`; Mac expects the same relative paths under `MAC_COMFY_MODELS` (default `$PREFIX/comfy-models`). |
| MiniMax Music 3 | Both installers use the three Comfy-Org `MiniMax-Music-3` files listed in the [Windows catalog](../app/model_catalog.json) and [Mac setup](../scripts/setup-music.sh). Copy the complete `MiniMax-Music3-Comfy` model folder to the Mac's configured `MUSIC3_MODEL` (default `~/music3-models/MiniMax-Music3-Comfy`). The Mac still needs its separate MLX runtime. |
| Fish Audio Drama 3 | A matching original Fish checkpoint can supply the Mac conversion input (`DRAMA3_CHECKPOINT`, default `~/models/drama-3`). The Mac's [voice setup](../scripts/setup-voice.sh) makes a separate MLX model; the Windows codec/engine files are not a replacement for that conversion. Check revisions before reuse. |
| Qwen-Image, FastH3 and H3 | Windows ComfyUI packed/pruned/int8 files are not drop-in substitutes for the Mac's default original/sharded MLX/TensorFold model directories. Reuse only files explicitly matching the chosen destination route; otherwise obtain or convert the required representation through its supported setup. |

For MOSS-Image, the Mac's [ComfyUI setup](../scripts/setup-comfy.sh) lists the exact shared files and destination
layout. Other models/features may share some original weights without sharing their converted derivatives.
Consult the current destination installer, not just this table, when versions change.

Place compatible files in the destination's expected directories **before its model-download stage**, or set
its documented overrides. Mac defaults include `QWEN_MODEL_DIR`, `H3_MODEL_DIR`, `FASTH3_DIR` and feature-specific
paths; a Windows model-root override is not a universal Mac model-root override.
Rebuild the Mac app launcher after establishing final paths; do not distribute a copied app bundle.

Reusing weights avoids those downloads, not runtime installation, conversion time, qualification or memory
requirements. Start with the smallest intended recipe on the destination and retain the source backup until
the features you need have worked there.
