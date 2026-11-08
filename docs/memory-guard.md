# Memory: the Studio's manager and the machine guard

A render takes most of a machine's memory. On a DGX Spark or a Mac the GPU and the system share one pool, so a
model server an agent started beside a render, or a ComfyUI run by hand, can take the whole machine down: on
2026-11-08 a Spark hung for 50 minutes after its GPU driver was refused memory (`NV_ERR_NO_MEMORY`) with an LLM server
(22 GB), the video worker (52 GB) and the app resident at once, past a guard that read the available memory every
few seconds. Three layers now stand in the way.

## 1. Before a job starts (the app: `app/memory_manager.py`)

Every job of this machine's queue is checked against its **measured peak** (the estimates in
`spark/memory_guard.py` on a Spark or a card, the M5 Ultra's peaks on a Mac) plus a 6 GB error margin, against the
available memory less the floor (a tenth of the machine, never under 8 GB; `SPARK_MEMORY_FLOOR_MB`). When it does not
fit:

1. the Studio unloads its own idle modules, in this order: Fish Audio Drama 3, the song engines, ComfyUI and its
   cached models, then the Bolt brain (never one the job uses, never Bolt while it answers);
2. if other programs still hold the memory, the job waits (up to two minutes, `STUDIO_MEMORY_WAIT_S`), its progress
   naming the largest of them;
3. then it is refused with a message naming them (GPU programs first) and their sizes. The Studio never stops a
   program it did not start. `STUDIO_MEMORY_GUARD=warn` (an RTX Spark N1X's default) runs it with a warning instead.
   On a Mac the idle modules are unloaded and a short memory is a warning, not a wait or a refusal (vm_stat's
   available memory leaves out what macOS can still reclaim by compressing); `STUDIO_MEMORY_GUARD=strict` makes
   it wait and refuse there too.

## 2. While the app runs (the app's watch)

Every 2 seconds (every 0.5 s while a job runs here) the app reads the available memory and the kernel's memory stall
time. Under 1.5 floors, or under two floors with tasks waiting on memory, it unloads the next idle module. Beside a
ComfyUI render the floor watch of `spark/memory_guard.py` (20 readings a second) still stops the render server under
the floor, as before. Everything done is listed by `GET /api/memory` (`events`) and logged in
`~/ImaginationStudio/logs/memory.log`. **Machine → The memory guard** shows the loaded modules, the largest other
programs and a **Release idle modules** button.

## Heat: the thermal gate and the live thermal stop

Memory is not the only way a render takes a machine down. DGX Spark .3 hung hard twice during back-to-back clips with
41.7 GB available and no memory stall: the GPU stood at 88 to 89 °C on every clip and the hottest ACPI zone at
96.6 °C, and the throttle grew clip after clip (software slowdown 18, 31, 37 s; hardware slowdown 3, 5, 8 s; clock
event reasons 0x20, 0x48, 0x68). With 60 s between clips it cooled to 60 to 70 °C and stayed flat.

- **The gate** (`memory_manager.thermal_admit`, after the memory admission): a GPU job waits, polling every 2 s,
  until the GPU is at most 70 °C and the hottest thermal zone at most 85 °C; its progress says
  `Cooling the GPU: 78 °C → 70 °C`. After 300 s it starts anyway with a warning, unless the hottest reading is still
  88 °C or more: then it is refused with the reason.
- **The live stop** (the app's watch, every 2 s while a GPU job runs here): at 90 °C, or after 10 s of the GPU's
  hardware thermal slowdown (clock event reasons 0x8 or 0x40; software slowdown, 0x20, alone does not count), the
  render is cancelled as Cancel does it, the reason in its error, and queued again once: the repeat waits at the gate.
  A repeat that overheats too is not queued a third time.
- **The machine guard's backstop** (`scripts/memguard.py`, Linux and Windows): at 88 °C or more it logs; when the
  same condition has held 20 s past the app's own stop (90 °C for 20 s, hardware slowdown for 30 s), it cancels the
  render through the Studio's API as its last resort. `--no-thermal` leaves heat out.

| Platform | Read |
|---|---|
| DGX Spark, CUDA Linux | nvidia-smi `temperature.gpu` and `clocks_event_reasons.active` (`clocks_throttle_reasons.active` on older drivers), and the hottest of `/sys/class/thermal` |
| Windows (RTX Spark N1X) | nvidia-smi, the same two fields |
| macOS | the thermal state macOS reports (`NSProcessInfo.thermalState`, read with `osascript`, no sudo): the gate waits at "serious" and refuses at "critical"; the live stop acts at "critical". A Mac's GPU temperature needs sudo, so there is no °C line there |

Settings: `STUDIO_THERMAL_GATE=off|on` (on by default where something is readable), `STUDIO_THERMAL_GPU_C` (70),
`STUDIO_THERMAL_ZONE_C` (85), `STUDIO_THERMAL_REFUSE_C` (88), `STUDIO_THERMAL_STOP_C` (90),
`STUDIO_THERMAL_THROTTLE_S` (10), `STUDIO_THERMAL_WAIT_S` (300), `STUDIO_THERMAL_POLL_S` (2). `GET /api/memory` has the
readings under `thermal`; what the gate and the stop did is in its `events` and in `logs/memory.log`.

## 3. The whole machine (`scripts/memguard.py`, installed by the one-shot setup)

A small per-user watchdog that reads the machine's memory every half second (a tenth of a second near the floor,
while memory falls over 1 GB a second or while the kernel reports a stall) and acts before the OOM killer or a hang:

| When | It |
|---|---|
| free memory crowded out by the page cache (Linux) | gives back the model files' cache (the GB10's driver takes what is *free*) |
| under 1.5 floors, falling to the floor within 2 s, or macOS pressure "warn" | has the Studio unload its idle modules (`POST /api/memory/release`) |
| under the floor, commit nearly used up, or the machine stalled on memory | **only if you opted in:** stops the largest of your own model servers (vLLM, llama.cpp, Ollama, SGLang, LM Studio, mlx_lm, a separate ComfyUI...) |
| under half the floor for a second, or macOS pressure "critical" | as the last resort cancels the render running here, so the machine survives (`--no-cancel` leaves it to the kernel) |

| Platform | Installed as | Reads |
|---|---|---|
| Linux (DGX Spark, CUDA) | `~/.config/systemd/user/imagination-studio-memguard.service` | MemAvailable, MemFree and the page cache, `/proc/pressure/memory`, the commit left under strict overcommit |
| macOS | `~/Library/LaunchAgents/com.imagination-studio.memguard.plist` | `vm_stat`, `kern.memorystatus_vm_pressure_level` |
| Windows (RTX Spark N1X) | Task Scheduler task "Imagination Studio Memory Guard" (at logon) | GlobalMemoryStatusEx: available memory and the commit left |

```bash
python3 scripts/memguard.py status                    # thresholds, a reading now, installed or not, the last log lines
python3 scripts/memguard.py install --dry-run         # what it would write and run, nothing changed
python3 scripts/memguard.py install --stop-foreign    # also allowed to stop your own model servers under the floor
python3 scripts/memguard.py uninstall                 # everything install made, gone
```

The one-shot setups install it (`--no-memguard` / `-NoMemoryGuard` leaves it out; `--memguard-stop-foreign` /
`-MemoryGuardStopForeign` opts in to stopping your own servers). `STUDIO_MEMGUARD_FLOOR_MB` sets its floor,
`STUDIO_MEMGUARD_STOP_PATTERNS` (comma separated) the servers it may stop. Its log is `memguard.log` in
`~/.local/state/imagination-studio` (Linux), `~/Library/Logs/ImaginationStudio` (macOS) or
`%LOCALAPPDATA%\ImaginationStudio` (Windows). A Linux user unit runs while you are logged in; `loginctl enable-linger`
keeps it running without a login.

The install also places [STUDIO-AGENT.md](../STUDIO-AGENT.md) in the Studio's folder and, where Claude Code is set up,
as the skill `~/.claude/skills/imagination-studio/SKILL.md`: how an agent renders through the Studio and checks the
headroom first.

The idea of acting on memory pressure in user space before the kernel's OOM killer is earlyoom's (Jakob
Unterwurzacher) and systemd-oomd's, with the kernel's pressure stall information (PSI, Johannes Weiner).
