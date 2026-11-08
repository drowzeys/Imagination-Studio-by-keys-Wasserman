"""Download or verify the optional Small SFX cache; never run inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from windows_audio3 import SOURCE_REVISION

REPOS = ("stabilityai/stable-audio-3-small-sfx",)
PATTERNS = ("*.safetensors", "*.json", "*.model", "*.txt", "LICENSE*", "NOTICE*", "*.tiktoken")


def setup(root: Path, download: bool) -> None:
    if download:
        from huggingface_hub import HfApi, get_token, snapshot_download
        token = get_token()
        api = HfApi(token=token)
        infos = [api.model_info(repo, files_metadata=True) for repo in REPOS]
        import fnmatch
        size = sum(file.size or 0 for info in infos for file in info.siblings
                   if any(fnmatch.fnmatch(file.rfilename, pattern) for pattern in PATTERNS))
        print(f"Small SFX and T5Gemma: {size / 2 ** 30:.2f} GiB of model files; stored outside the repository.", flush=True)
        root.mkdir(parents=True, exist_ok=True)
        revisions = {}
        for repo, info in zip(REPOS, infos, strict=True):
            # The offline production API resolves "main"; preserve its ref in this model's isolated cache.
            snapshot = Path(snapshot_download(repo, revision="main", cache_dir=root / "hub" / "hub",
                                              allow_patterns=PATTERNS, token=token))
            if snapshot.name != info.sha:
                raise RuntimeError("A model revision changed during setup; rerun before recording its manifest.")
            revisions[repo] = info.sha
        files = {}
        for path in (root / "hub").rglob("*"):
            if not path.is_file() or ".locks" in path.parts:
                continue
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            files[path.relative_to(root).as_posix()] = {"size": path.stat().st_size, "sha256": digest,
                                                       "mtime_ns": path.stat().st_mtime_ns}
        manifest = {"source_revision": SOURCE_REVISION, "revisions": revisions, "files": files}
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["source_revision"] != SOURCE_REVISION or set(manifest["revisions"]) != set(REPOS) or not manifest["files"]:
        raise RuntimeError("The Small SFX manifest is incomplete or incompatible.")
    for name, row in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise RuntimeError("The manifest names a file outside its model folder.")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if path.stat().st_size != row["size"] or digest != row["sha256"]:
            raise RuntimeError("A Small SFX model file is missing or changed: " + name)
    print("Stable Audio 3 cache verified. No model was loaded.", flush=True)


if __name__ == "__main__":
    from huggingface_hub.errors import HfHubHTTPError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    try:
        setup(args.models, args.download)
    except HfHubHTTPError as error:
        code = error.response.status_code if error.response is not None else "unknown"
        print(f"Hugging Face refused the model download (HTTP {code}). Accept the model licences and sign in, then retry.",
              file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"Stable Audio 3 setup failed: {error}. For gated downloads, accept the licence and sign in to Hugging Face.", file=sys.stderr)
        raise SystemExit(1) from None
