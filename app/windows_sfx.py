"""Verified local Stable Audio Open files for the explicit Windows calibration test."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

REPO = "stabilityai/stable-audio-open-1.0"
REVISION = "f21265c1e2710b3bd2386596943f0007f55f802e"
FILES = {
    "model.safetensors": (4853889016, "7b20458a071231aaf32613b6fbc7945f28f34dbba4f295bb49bad56f5f66b57e"),
    "text_encoder/model.safetensors": (438525864, "095878c81d21f17d235e1b4aa20e9595289177e2658ef4dcec69fa5aa3b07ca2"),
}


def verify(models: Path) -> dict:
    for name, (size, expected) in FILES.items():
        path = models / name
        if not path.is_file():
            raise RuntimeError(f"missing model file: {name}; run setup-windows-sfx.ps1 after accepting the model terms")
        if path.stat().st_size != size:
            raise RuntimeError(f"model file has the wrong size: {name}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(4 << 20):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"model file failed SHA-256 verification: {name}")
    if not (models / "LICENSE.md").is_file():
        raise RuntimeError("the model license file is missing")
    return {"ok": True, "repo": REPO, "revision": REVISION, "weight_bytes": sum(size for size, _ in FILES.values()),
            "scope": "files verified only; no model loaded and no Windows route qualified"}


def download(models: Path) -> dict:
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    from huggingface_hub import get_token, hf_hub_download
    from huggingface_hub.errors import HfHubHTTPError

    if not get_token():
        raise RuntimeError("sign into Hugging Face locally and accept the Stable Audio Open terms; do not paste tokens into the repo")
    models.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE.md", *FILES):
        print(f"[windows-sfx] fetching {name} at the pinned revision", file=sys.stderr, flush=True)
        try:
            hf_hub_download(REPO, filename=name, revision=REVISION, local_dir=models, token=True)
        except HfHubHTTPError as error:
            status = error.response.status_code if error.response is not None else "no response"
            raise RuntimeError(f"Hugging Face download failed for {name} (HTTP {status}); check local login and model access") from None
        except (OSError, RuntimeError) as error:
            raise RuntimeError(f"Hugging Face download failed for {name} ({type(error).__name__}); no inference was started") from None
    return verify(models)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        result = download(args.models) if args.download else verify(args.models)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
