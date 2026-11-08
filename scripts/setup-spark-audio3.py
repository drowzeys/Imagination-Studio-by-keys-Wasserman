"""Download/verify the pinned native ComfyUI Small SFX files; never load a model."""
import argparse
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import spark_audio3 as audio3


def setup(folder: Path, verify: bool) -> None:
    if not verify:
        from huggingface_hub import hf_hub_download
        print(f"Stable Audio 3 Small SFX and T5Gemma: {sum(size for size, _ in audio3.FILES.values()) / 2 ** 30:.2f} GiB.",
              flush=True)
        print("Stability AI Community License and Gemma Terms of Use apply; weights are not redistributed.", flush=True)
        for name in audio3.FILES:
            hf_hub_download(audio3.REPO, name, revision=audio3.REVISION, local_dir=folder)
    for name, (size, expected) in audio3.FILES.items():
        path = folder / name
        if not path.is_file() or path.stat().st_size != size:
            raise RuntimeError("Missing or incomplete Small SFX file: " + name)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != expected:
            raise RuntimeError("Small SFX checksum verification failed: " + name)
    print("Stable Audio 3 ComfyUI files verified; no inference or memory qualification ran.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True, help="the Sound-Comfy directory")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        setup(args.models, args.verify)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Stable Audio 3 setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
