# Windows RTX Spark: dated test results

Concise local test evidence, not a progress diary or current support policy.
The [living plan](windows-rtx-spark-plan.md#current-support) defines supported scopes and remaining work;
the [testing runbook](windows-rtx-spark-testing.md) supplies repeatable commands.
These are route-specific results, not a full-suite, hosted CI or cross-platform pass.

## 2026-11-03: 48 GB baseline

Native Windows ARM64; NVIDIA driver 621.01. Observed budgets: about 20.8 GiB Windows-visible RAM,
26 GiB dedicated GPU and 35.63 GiB CUDA allocation budget.

| Check | Result / useful measurement |
|---|---|
| Planning and portable films | Browser import, boards, Slate edit/save/reopen and clean-folder media ZIP import passed |
| SD-Turbo | Six 512x512 one-step images plus sheet: 5.01 s after startup; peak tensors/reserved 3.01/3.31 GiB; private/job commitment 7.57/9.27 GiB |
| Stable Audio Open | Five-second recipe: 9.29 s after startup; 5.01551 s stereo 44.1 kHz WAV; peak tensors/reserved 6.53/6.65 GiB; private/job commitment 11.99/13.68 GiB |
| Qwen3-4B writing | One-scene/two-shot First AD response applied and saved in 5.22 s |
| Copilot CLI | Connection Check, read-only `studio_state` tool use and session continuity passed |
| Media and handoff | Browser decoded 2.0 s, 1344x768 mixed silent/audio export; FCPXML/EDL/media ZIP API/file checks passed |
| Graphics and beats | Six ten-second 1280x720/24 fps templates passed in 10.82-20.83 s; about 892-934 MiB peak process-tree commitment; no new visible browser windows; 120 BPM fixture/cache passed |
| Lifecycle | Warm reuse observed after 314 s idle; running/queued cancellation, sampling interruption, unload and Ctrl+Break/restart passed without cancelled outputs or owned process leaks |

## 2026-11-04: 64 GB baseline and regressions

Native Windows ARM64; NVIDIA driver 621.01; Python 3.13.17 ARM64 with the pinned Windows CUDA wheel set.
Observed budgets: 31.70 GiB Windows-visible RAM, 30.50 GiB dedicated GPU and 45.59 GiB CUDA allocation budget.

| Check | Result / useful measurement |
|---|---|
| Setup | Official UI/GPU/ComfyUI, media, graphics and model setup/check paths passed; image/sound qualification refreshed after renderer changes |
| Planning and portable films | Two-scene API flow, cast/dialogue/reference links, intent exclusion, save/reload and ZIP manifest checks passed; media reimport worked with original board unavailable |
| SD-Turbo | One- and six-image checks passed at 512x512; six images plus sheet: 5.714 s after startup |
| Stable Audio Open | 15.155 s after startup; 5.015510 s stereo 44.1 kHz WAV; peak -14.745709 dBFS |
| Qwen3-4B writing | CUDA startup passed; title/logline in 661 ms and three-shot First AD JSON in 3,546 ms |
| Copilot CLI | Connection Check, actual read-only `studio_state` execution and same-thread follow-up passed |
| Media and handoff | Probe/frame extraction, timeline reload, FCPXML/EDL/media ZIP passed; six-second export and separate 2.002 s, 1344x768 mixed-source export fully decoded |
| Graphics and beats | All six bounded templates passed with no new visible browser windows; 120.02 BPM fixture result and cache passed |
| Lifecycle | Warm reuse, image/sound switching, queued/running cancellation, busy-unload refusal and shutdown passed; sampling cancellation saved no audio |
| Admission regression | Image accepted while sound started, then valid sound and queued image completed; final unload and graceful shutdown left zero owned descendants |
| Browser sweep | Official read-only checker passed all 33 tabs: no page errors, broken images/media, remaining failed requests or phone overflow; injected write refusals and confirmed downloads classified separately |
| Focused regressions | 56 tests passed across storyboard, sound, UI-checker and API-answer tests; JavaScript syntax check passed |

## 2026-11-04: independent features after the native Windows merge

Same 64 GB machine; native H3 tier remained off and its 90,000 MB CUDA-pool gate was unchanged.
These results do not qualify either new route on the 48 GB machine.

| Check | Result / useful measurement |
|---|---|
| HDR grading | Two ten-second 1280x720/24 fps sound/silent cases: 66.34 / 68.55 s; peak owned-job commitment 2,116.9 / 2,116.0 MiB under the 4 GiB cap. HLG 10-bit BT.2020 metadata, SDR companion, complete decode and unchanged source audio packets verified |
| Local Tesseract Finish | Three 9.5-second mixes with title/subtitle, 12 mixed sound/silent cuts, song and narration: 14.46-15.05 s; peak 946.3-947.7 MiB. Silent ten-second cut: 8.95 s / 817.8 MiB. MP4, editable project, FCPXML and EDL outputs verified |
| Actual app and browser | Both independently ready without H3; invalid settings and already-HDR input refused. Queued/running cancellation, actual Finish button, 720p/24 fps controls, playback/download and disabled transcription/conversion passed; no page errors; graceful shutdown left zero owned descendants |
| Progressive check UI | Machine cards displayed unknown minimum RAM, conservative headroom/cap and saved evidence. Opening the page submitted no tests; an actual Finish Check/Rerun completed through the normal queue, persisted its live pass and downloaded the saved report with no page errors |
| Finish reliability caveat | One repeated app check failed inside Tesseract 0.4.0 at `project apply`: `source .tsrct file changed after it was opened`. It did not reproduce in subsequent completed runs. The job reports the failure; no automatic retry or success fallback was added |
| Existing baseline refreshed | Six headless graphics templates passed with no visible browser windows. SD-Turbo and five-second Stable Audio Open requalified after the merge; sound request-to-file 12.88 s, 5.015510 s stereo 44.1 kHz. A watchdog shutdown queue-overflow race was fixed without changing guard limits |
| Foley setup | Pinned ComfyUI-MMAudio and weights installed into an isolated Foley runtime. Native `soxr` resampling passed; standard setup, repeat without downloads/reinstalls, read-only verification and `pip check` passed. Baseline GPU pins remained unchanged |
| Foley qualification | Five-second mix / one-second replacement: 20.58 / 17.50 s; peak owned-job commitment 12,307.1 / 11,902.4 MiB under the unchanged 16 GiB cap. Mono 44.1 kHz, duration/levels, full decode and unchanged source frames passed |
| Foley normal app route | Owned isolated app rendered a 720p five-second mix and 3.5-second replacement. Invalid settings refused; queued/synthesis cancellation, no cancelled-take publication, backend drain and graceful shutdown passed |
| Sound telemetry refresh | UUID/LUID-bound native WDDM usage removed model-load HTTP/NVML stalls without extending two-second freshness or raising caps. Stable Audio Open requalified; process peak 12,872,540,160 bytes remained below its 12 GiB cap |
| Six progressive checks | Image drafts, sound effects and six-template motion graphics now join Foley/HDR/Finish in room-grouped cards. All six existing passes were retained byte-for-byte under balanced matching, with zero requalification renders. Read-only browser checks passed group collapse, saved badges and exact card focus |
| Update policy and card layout | Balanced reuse and opt-in strictness, ABI/recipe/model compatibility boundaries, prior-pass retention and unload-before-check ordering passed model-free regressions. Compact in-card badges preserved row alignment and independent clickability at desktop and mobile widths; no browser check started a render |

CPU feature receipts are hardware/runtime/code-bound, with a 4 GiB job cap and OS low-memory supervision.
The ten-second limits are measured qualification boundaries, not claims about the models or all CPU workflows.

## 2026-11-04: 48 GB expanded qualifications and checker diagnostics

Native Windows ARM64 on the 48 GB N1X: 20.79 GiB Windows-visible RAM, 26 GiB dedicated GPU memory
and 35.63 GiB CUDA allocation budget. Official expanded checks from `25dc13a`, with a local checker
diagnostic fix; no allocator/process/job cap, reserve, freshness window or startup deadline changed.

| Check | Result / useful measurement |
|---|---|
| Checker diagnostics | Cleanup failure preserves the original check error and reports the retained workspace; cleanup-only failure still fails. 190 focused regressions passed, including active-caller exception handling and the expanded qualification contracts |
| Balanced reuse | Earlier HDR/Finish passes retained their dates and readiness. Existing image/SFX/graphics evidence stayed qualified with a runtime-update note until explicitly rerun; opening Machine submitted no checks |
| Foley qualification | Official Machine-card check passed five-second mix / one-second replacement: 21.13 / 36.65 s after startup; peak job commitment 12,308.4 / 11,901.9 MiB under the unchanged 16 GiB cap. Mono 44.1 kHz, duration/levels, full decode and unchanged decoded source frames verified |
| Expanded image/SFX checks | Both official Machine-card reruns passed. Six 512x512 drafts: 5.91 s after startup, peak job 7,816.0 MiB. Five-second SFX: 11.15 s, peak process 12,275.9 MiB and job 12,332.3 MiB; process commitment remains close to its unchanged 12 GiB cap |
| Expanded graphics check | Official Machine-card rerun passed all six ten-second 1280x720/24 fps templates, with peak job commitment 932.7 MiB and no added visible browser windows |
| Actual Foley UI | A separate five-second 660x360 take from the official check's source fixture completed through Make the foley, published MP4/WAV and appeared under Made here. Its video played in the actual page at the expected size/duration, with advancing playback and no media error |
| Read-only browser sweep | Seven affected tabs matched their requested routes: no page/console/HTTP errors, failed requests or broken media; 19 writes blocked and zero phone overflow. All six qualification cards were qualified afterwards; native H3 video remained unavailable |

The earlier Foley attempt failed before backend readiness and its cleanup error masked the original cause.
The explicit rerun above passed; it does not establish that the earlier cold-start cause was fixed or that
the failed attempt ran out of memory. These are bounded 48 GB results, not larger-model qualification.

## Interpretation limits

The unified-routing change was checked on the actual 64 GB app: one native Windows label, no global support
banner, qualification on/locked, six saved passes retained and informational native-model cards, with no
new qualification renders. The live GPU inventory reports one RTX Spark GPU and excludes the NPU. Default
selection uses the available SD-Turbo draft rather than freezing an unavailable Qwen preference; explicit
full-model choices are not silently replaced. 80/128 GiB policy cases are model-free simulations, not hardware
or larger-model qualification.

- Image/sound retained 12/12/16 GiB allocator/process/job caps and a 2 GiB GPU reserve; graphics retained
  its 4 GiB job cap. Memory counters overlap and must not be added as physical capacity.
- Sound's private commitment is close to its cap. No longer effect, larger image or other model is
  qualified by these results. Format/level checks are not a listening-quality review.
- Timings exclude startup where stated. Writing checks differ in scope and are not direct speed comparisons.
- The baseline API checks/read-only sweep do not prove interactive First AD apply/save or baseline media
  playback/download. The later independent-feature check did verify actual Finish playback/download.
  An actual Windows Resolve import remains unverified. These local runs did not use 128 GB hardware;
  upstream's separate native-tier measurements are in the [N1X notes](rtx-spark-n1x.md#native-windows-the-native-cuda-tier).
- Official calibration reports are generated under Studio's `renders\calibration`; capture each new
  machine separately using the runbook. Do not copy qualification receipts between hardware tiers.
