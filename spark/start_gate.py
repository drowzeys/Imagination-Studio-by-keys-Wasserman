#!/usr/bin/env python3
"""Before a job is submitted to the studio's ComfyUI server on a machine with a workload governor
(``SPARK_GOVERNOR``, ``workload.py``): the server must hold its own live reservation, and the machine must be cool
and calm (``workload.start_reasons``); it waits up to three minutes for that. Without a governor it passes at once.

    python3 spark/start_gate.py [comfy|drama3]

Exit status 0 when the job may go, 1 with the reason on stderr when it may not.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workload  # noqa: E402


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else "comfy"
    if kind not in ("comfy", "drama3"):
        print("usage: start_gate.py [comfy|drama3]", file=sys.stderr)
        return 2
    if not workload.enabled():
        return 0
    try:
        workload.gate(kind)
    except Exception as error:  # noqa: BLE001 - any failure to check is a refusal
        print(f"submission refused: {error}", file=sys.stderr, flush=True)
        return 1
    print("[studio] submission checks passed: reservation, memory, pressure and a cool start", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
