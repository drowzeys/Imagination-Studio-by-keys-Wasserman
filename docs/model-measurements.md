# Updating models and shared measurements

## What goes where

- [`app/model_catalog.json`](../app/model_catalog.json): model descriptors, download manifests, published byte
  sizes, checksum pins and declared admission policies. Unknown runtime requirements stay `null`.
- [`app/model_measurements.json`](../app/model_measurements.json): portable observations from actual recipe
  checks, with explicit hardware pools, model identities, recipe/contract identity, outcomes and readings.
- Local qualification receipts: per-machine evidence outside Git. Shared results never create local passes.

`app/shared_measurements.py` is the platform-neutral schema, identity, validation, reference and summary
layer. It imports no Windows runtime or qualification modules. `app/windows_measurements.py` owns the Windows
recipe registry, genuine local receipt readers, legacy-receipt normalization and export; the Windows campaign
runner uses that adapter directly. Local receipt formats and qualification rules are unchanged.

## Portable schema and migration

The portable runtime format is schema **1**. It is still being developed on this branch;
the internal refactor does not introduce a schema revision or a legacy-format reader.
All 17 original checked-in profiles were migrated in place. Every reading, outcome, timestamp, runtime
version, weight hash/size, recipe and contract was retained. `provenance.source_profile_id`,
`source_model`, `source_identity_sha256` and `source_provenance_complete` retain the original identifiers
and claims. Tests reconstruct every original profile and verify its original content hash.

Each profile separates canonical `model` from `feature`: SD-Turbo is model `sd-turbo`, feature `scout`;
Stable Audio Open is model `sound`, feature `sfx`. Library actions name their model dependency separately.
The portable identity's `feature` names the checked recipe family, not a second canonical model ID.
`execution` records OS (`windows`, `macos`, `linux`), backend, engine, CPU architecture and memory topology
(unified/discrete/host, participating device count, public device family and model). These are closed,
public identifiers, not a device UUID, host name or arbitrary GPU-name string.

Missing historical architecture/device/topology is explicitly `unknown`/`null`, not inferred from capacity.
The 18 actual observations collected on this branch have a known source configuration: Windows ARM64 RTX
Spark (N1X), one unified-memory device. Their execution metadata was annotated from that known test machine,
not inferred from pool sizes. Recorded measurements, dates and legacy content provenance remain unchanged.
Missing historical per-result `case` is `null`; new exports preserve allowlisted production case labels
or numeric recipe descriptors. Historical weight/engine file counts were not retained, so
`identity.weights_expected` and `engine_expected` are `null`, completeness is false, and the original
completeness claim remains only in provenance. New identities require hashes for **every** expected weight
and engine file plus the recipe/ABI/contract to claim complete provenance. A partial set is not complete.

Validation checks every nested profile, contract, hash, case, numeric reading and allowed field, not just
the root. Identity/profile hashes are checked. Local paths, raw errors and arbitrary nested strings are
rejected. Unknown schema versions are rejected rather than silently adapted.

Memory readings retain distinct semantics: `gpu_pool_mb` minus `dedicated_gpu_free_mb` is adapter-wide
device usage; `host_pool_mb` minus `host_available_mb` is system-wide host usage; `unified_pool_mb` minus
`unified_available_mb` is system-wide unified-pool usage. Owned job/process commitment and commit headroom
are separate backing metrics. Pools are **never added**, and a missing device reading never becomes an
inferred GPU measurement from host or unified RAM. All quantities ending in `_mb` use MiB.

`app/model_metadata.py` explicitly maps Mac/Spark inventory needs to canonical models and their known
backend/layout. Components (for example a text encoder or codec) are not guessed to be whole models.
Only same-OS/backend/engine references are exposed. A multi-route weight bundle has an unknown route and
gets no route-specific references. Mac MLX folders do not inherit Windows Comfy download sizes or Windows
fit policies; existing platform install descriptions and admission checks remain authoritative. There are
currently no checked-in Mac/Spark measurements, so their shared-reference summaries are empty.

Library and Character Swap inventory badges describe installed components, not complete workflow readiness.
Find a person uses SAM without H3; generative Library actions additionally require H3 models and their
action-specific dependencies. Each action's Feature checks remains the source of qualification status.

Models & weights shows recorded GPU usage and owned-job commitment for every linked feature with saved
readings. Before installation, applicable shared profiles also show their observed ranges and recorded RAM
capacity instead of a generic unknown-fit message. These are reference observations, not local passes or
guaranteed fit; known admission-policy warnings remain visible.
Local readings include bounded SD-Turbo, SFX and Foley. Installed files alone do not provide observed usage.
Historical observations remain distinct from current qualification and admission.

Do not replace an admission requirement with an observed peak or treat a failed run as a measured minimum.
Adapter-wide GPU usage includes other processes. Commit backing, dedicated memory and host RAM overlap.
Interrupted samples are incomplete lower bounds, not completed peak requirements. A 128 GiB machine's success
does not prove a smaller machine can run that recipe.

## Add/update catalog metadata

For an existing supported backend/layout, add or update the descriptor and file manifest in the catalog.
Preserve upstream revision/SHA-256 pins unless intentionally upgrading them. Record the runtime policy's
basis, route and scope; a download's GB size is not runtime RAM. New renderers still need their own adapter,
recipe and tests. The generic fit/size UI reads the descriptor automatically.

Refresh published file sizes, without downloading weights:

```powershell
py -3.13 -X utf8 scripts\refresh-model-sizes.py
```

Inspect changes to repository revisions, filenames and byte totals. A changed file size is not a new successful
test. Source/UI edits alone do not require new measurement contracts; incompatible execution/safety changes do.
Existing qualification uses balanced compatibility by default; strict matching is an explicit opt-in.

## Run measurements on an installed Windows Studio

Use the real running native Studio on `127.0.0.1:7870`, with the intended models installed. Close unrelated GPU
work and let Studio unload its own warm backends through its normal queue lifecycle. Never start a hidden
second app, lower guards, change GPU carve-outs or automatically install models for a sweep.
The runner uses the normal qualification API and production builders, one job at a time.

Use Studio's UI Python so existing dependencies are available:

```powershell
$Python = "$env:LOCALAPPDATA\ImaginationStudio\ui-venv\Scripts\python.exe"

# Export only existing actual receipts: no GPU/hardware probes or inference.
& $Python -X utf8 scripts\measure-windows-models.py --export-only --output measurements.json

# Default: plan recipes without a complete compatible shared pass.
# Does not render. Hashes installed weights when no matching cached receipt identity is available.
& $Python -X utf8 scripts\measure-windows-models.py --output measurements.json

# Run only the three updated models, using their registered scopes.
& $Python -X utf8 scripts\measure-windows-models.py --models qwen,qwen-full,flux3k-full --run --output measurements.json

# Limit an image campaign to a particular recipe.
& $Python -X utf8 scripts\measure-windows-models.py --models qwen --scopes studio --run --output measurements.json

# Force all installed registered native model recipes, even with matching shared evidence.
& $Python -X utf8 scripts\measure-windows-models.py --force-all --run --output measurements.json
```

Scope: Qwen Turbo/Full, FLUX3-K Fast/Full, MOSS-Image, native Voice/Music and the five native Library actions.
Existing legacy SD-Turbo/SFX/Foley/CPU check adapters are not included in this campaign runner.
Export also includes saved bounded SD-Turbo, SFX and Foley observations. Their legacy receipts lack the full
portable identity, so they remain explicitly incomplete provenance rather than qualifying changed-only skips.
Unavailable prerequisites are reported `not_installed`, not passed. No weights are downloaded.
Changed-only compares content checksums and portable recipe/ABI/contracts on the same fully specified
OS/backend/engine/architecture/device-model/topology and recorded capacity profile; same capacity alone
never matches. Unknown historical metadata, incomplete hashes and failed-only references do not count as a
matching pass. **Current historical receipts lack those public device details, so they conservatively plan
another run rather than being skipped.** Export does not probe a device or fill an old receipt from today's
machine. A checker may record a validated `fingerprint.measurement_profile` for a new check; until it does,
that check's unknown device profile cannot justify changed-only skipping either. Ordinary compatible source
updates alone do not invalidate otherwise complete, compatible evidence.
`--reference other-measurements.json` compares against another campaign. The plan is printed to the console;
the output JSON remains a schema-1 measurement document, separate from the runner's resume state.

Each recipe's actual result is persisted by the normal checker before the next starts. The portable export
is refreshed after each job. A sibling `.resume.json` tracks the submitted job and completed identities.
Keep that file to resume after interruption; use a new output filename to start a fresh forced campaign.
Ctrl+C cancels the submitted job and exports available receipts. The runner stops on any failed/cancelled
recipe so you can inspect memory/backend errors before retrying; completed local passes remain intact.
Each recipe has the normal checker timeout plus a bounded queue/drain margin; the invocation also has a
24-hour deadline (`--timeout-hours` overrides it). Deadline expiry requests cancellation, never kills unrelated processes.
After a crash/network loss, rerunning reconnects to its recorded job rather than blindly submitting another.
Do not remove the resume file while its recorded job is still active.

## Review and check in results

The exported JSON allowlists numeric readings and normalized identities. It excludes local paths, command
lines, raw errors, device UUID/LUID, hashed device identity, account/host names, job ids and credentials.
Model filenames are replaced by content hash/size pairs. No network upload or Git operation happens automatically.
Hardware fields describe capacity/pools, not a person's machine identity.

1. Review the JSON and outcomes; compare to the real report in Feature checks.
2. Keep failures and interrupted readings labelled as such. Old failed attempts may lack full provenance;
   those rows explicitly say `provenance_complete: false` and must not drive automatic fit decisions.
3. Use `--export-only` after finishing/cancelling to capture the final saved attempt state.
4. Merge reviewed schema-1 `profiles` into `app/model_measurements.json`, deduplicating by profile `id`; retain distinct
   hardware/weight/recipe observations. Do not copy the full raw local report into the repository.
5. Run the tests below, then review/stage/commit yourself. The tool never stages, commits or pushes.

Installers use shared observations as a conservative download-risk gate, not a fit guarantee:

The UI uses the same advice to gate copying a missing model's setup command. It shows observed memory
instead of shared attempt counts. Failure evidence applies to equal or lower RAM/dedicated capacities on
the same execution, device-family and memory-topology route; successful evidence is not extrapolated down.
Different discrete/unified pool layouts remain reference-only rather than inheriting another layout's block.

- Below 128 GiB installed RAM, failed recipe attempts with no passes on matching Windows backend/engine, architecture, public device family/model
  and physical/dedicated capacities require an explicit override before missing weights are downloaded.
  Automatic broad setup skips the affected component. Mixed pass/fail results allow downloading with a
  partial-usefulness warning: passing presets remain useful, but not every recipe has passed.
  At 128 GiB or more shared failures warn without blocking downloads. This helps smaller machines avoid
  huge, likely-unusable downloads by default; it does not change declared admission policies or runtime guards.
- `setup-windows-cuda.ps1 -AllowInsufficientModel` overrides that gate (and the existing declared-capacity gate).
  `setup-windows-images.ps1 -AllowUnverifiedImageModel` overrides shared image failures only.
- No shared metadata, unknown hardware identity or different capacities log a warning without blocking.
  A successful reference is not local qualification. Cancelled or provenance-changed attempts are not
  treated as model execution failures.
- Complete cached weights and `-NoDownload` remain reusable; `-PlanOnly` stays probe-free.

Shared rows are references in Models & weights, not installer guarantees. Setup's existing known-capacity
policy remains authoritative; observations do not fabricate predictive minimums, extend recipe bounds or
turn qualification off. A reference may guide a person before downloading, but automatic profile-based model
selection still needs conservative, validated envelope rules beyond observed peaks.

## Tests

```powershell
py -3.13 -X utf8 -m pytest -q tests\test_shared_measurements.py tests\test_model_catalog.py tests\test_windows_setup_fit.py
```

Tests also cover lossless migration round trips, neutral imports without Windows, unified pools, complete hash
sets, canonical model/feature IDs, platform/layout reference filtering and incompatible same-capacity hardware.
Tests use synthetic data for identity changes, privacy exclusions, interrupted outcomes and changed-only plans.
They are not physical measurements. Actual new engine/model evidence requires the explicit guarded campaign
on the stated hardware; never relabel simulations as measured results.
