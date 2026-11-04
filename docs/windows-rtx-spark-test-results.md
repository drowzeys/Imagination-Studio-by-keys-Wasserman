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

## Interpretation limits

- Image/sound retained 12/12/16 GiB allocator/process/job caps and a 2 GiB GPU reserve; graphics retained
  its 4 GiB job cap. Memory counters overlap and must not be added as physical capacity.
- Sound's private commitment is close to its cap. No longer effect, larger image or other model is
  qualified by these results. Format/level checks are not a listening-quality review.
- Timings exclude startup where stated. Writing checks differ in scope and are not direct speed comparisons.
- The 64 GB API checks/read-only sweep do not prove interactive First AD apply/save or browser playback/download.
  An actual Windows Resolve import remains unverified. These local runs did not use 128 GB hardware;
  upstream's separate native-tier measurements are in the [N1X notes](rtx-spark-n1x.md#native-windows-the-native-cuda-tier).
- Official calibration reports are generated under Studio's `renders\calibration`; capture each new
  machine separately using the runbook. Do not copy qualification receipts between hardware tiers.
