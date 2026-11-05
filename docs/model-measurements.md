# Updating models and shared measurements

## What goes where

- [`app/model_catalog.json`](../app/model_catalog.json): model descriptors, download manifests, published byte
  sizes, checksum pins and declared admission policies. Unknown runtime requirements stay `null`.
- [`app/model_measurements.json`](../app/model_measurements.json): portable observations from actual recipe
  checks, with explicit hardware pools, model identities, recipe/contract identity, outcomes and readings.
- Local qualification receipts: per-machine evidence outside Git. Shared results never create local passes.

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

# Default: plan recipes whose weights/recipe/ABI/execution contract or hardware differ from shared passes.
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
Unavailable prerequisites are reported `not_installed`, not passed. No weights are downloaded.
Changed-only compares content checksums and portable recipe/ABI/contracts on the same recorded memory-pool
profile; ordinary compatible source updates do not trigger inference. Failed-only references do not count as a
matching pass. `--reference other-measurements.json` compares against another campaign.

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
4. Merge reviewed `profiles` into `app/model_measurements.json`, deduplicating by profile `id`; retain distinct
   hardware/weight/recipe observations. Do not copy the full raw local report into the repository.
5. Run the tests below, then review/stage/commit yourself. The tool never stages, commits or pushes.

Shared rows are references in Models & weights, not installer guarantees. Setup's existing known-capacity
policy remains authoritative; observations do not fabricate predictive minimums, extend recipe bounds or
turn qualification off. A reference may guide a person before downloading, but automatic profile-based model
selection still needs conservative, validated envelope rules beyond observed peaks.

## Tests

```powershell
py -3.13 -X utf8 -m pytest -q tests\test_shared_measurements.py tests\test_model_catalog.py tests\test_windows_setup_fit.py
```

Tests use synthetic data for identity changes, privacy exclusions, interrupted outcomes and changed-only plans.
They are not physical measurements. Actual new engine/model evidence requires the explicit guarded campaign
on the stated hardware; never relabel simulations as measured results.
