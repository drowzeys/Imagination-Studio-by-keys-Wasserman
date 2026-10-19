#!/usr/bin/env bash
# Sound effects and foley (app/sound.py, sound_generate.py) in an environment of their own, on a Mac.
#   bash scripts/setup-sound.sh            # install or complete
#   bash scripts/setup-sound.sh --verify   # check an existing install, download nothing
# oneshot-setup.sh --sound runs this. Under $PREFIX (default ~/.local/opt/tensorfold-studio) it adds:
#   sound-venv/                PyTorch (Metal), diffusers (Stable Audio Open), MMAudio at a pinned commit
#   sound-models/stable-audio-open-1.0/   Stability AI's Stable Audio Open 1.0, diffusers layout (about 3.4 GB)
#   sound-models/mmaudio/      MMAudio large_44k_v2, its VAE and Synchformer (about 5.6 GB)
# and, in Hugging Face's own cache, the CLIP (DFN5B ViT-H-14-384, about 4 GB) and BigVGAN v2 vocoder MMAudio loads.
# Licences: Stable Audio Open 1.0 is under the Stability AI Community License (free under 1 M USD yearly revenue) and
# its page is gated: accept the licence on huggingface.co/stabilityai/stable-audio-open-1.0 with your account first
# (the studio's Install guide signs you in to Hugging Face; `hf auth login` works too). MMAudio's code is MIT and its
# weights are CC-BY-NC 4.0: non-commercial. Do not redistribute either.
# On a DGX Spark this only says it is not there yet: the Spark runs models through ComfyUI with a measured memory
# estimate (spark/memory_guard.py), and neither has one yet.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
VENV="$PREFIX/sound-venv"; MODELS="${SOUND_MODELS:-$PREFIX/sound-models}"
MMAUDIO_COMMIT="974010a026c731054592d8f777218bd9d85a6c24"  # hkchengrex/MMAudio, October 2026
VERIFY=0; [ "${1:-}" = "--verify" ] && VERIFY=1
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
ok() { echo "  ✓ $*"; }
note() { echo "  ! $*"; }

echo "Sound effects and foley (Stable Audio Open, MMAudio)"
if [ "$(uname -s)" != Darwin ]; then
  note "not on this machine yet: on a DGX Spark these need ComfyUI's nodes and a measured memory estimate first"
  exit 0
fi
command -v uv >/dev/null || { echo "  uv is needed: brew install uv" >&2; exit 1; }
command -v ffmpeg >/dev/null || note "ffmpeg is not installed (brew install ffmpeg): foley needs it to read the clip and lay the sound"

if [ ! -x "$VENV/bin/python" ]; then
  [ "$VERIFY" = 0 ] || { note "no environment at $VENV"; exit 1; }
  uv venv -q -p 3.11 "$VENV"
fi
if ! "$VENV/bin/python" -c "import mmaudio, diffusers, soundfile, torchsde" 2>/dev/null; then
  [ "$VERIFY" = 0 ] || { note "the environment at $VENV is not complete"; exit 1; }
  # MMAudio by commit; diffusers and transformers at versions that carry StableAudioPipeline together
  uv pip install -q -p "$VENV/bin/python" "mmaudio @ git+https://github.com/hkchengrex/MMAudio@$MMAUDIO_COMMIT" \
    "diffusers==0.32.2" "transformers==4.46.3" "sentencepiece" "torchsde" "soundfile" "accelerate"
fi
ok "environment: $VENV"

fetch() {  # a Hugging Face repo (or some of its files) into a folder, with the person's own sign-in
  "$VENV/bin/python" - "$@" <<'PY'
import sys
from huggingface_hub import snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError
repo, target, *patterns = sys.argv[1:]
try:
    snapshot_download(repo, local_dir=target if target != "-" else None, allow_patterns=patterns or None)
except GatedRepoError:
    print(f"  ! {repo} is gated: accept its licence on https://huggingface.co/{repo} with your Hugging Face account, sign in "
          "(the studio's Install guide, or `hf auth login`), then run this again", file=sys.stderr)
    sys.exit(3)
except HfHubHTTPError as error:
    print(f"  ! {repo}: {error}", file=sys.stderr)
    sys.exit(4)
PY
}

SAO="$MODELS/stable-audio-open-1.0"
if [ -f "$SAO/model_index.json" ] && [ -f "$SAO/transformer/diffusion_pytorch_model.safetensors" ]; then
  ok "Stable Audio Open 1.0: $SAO"
elif [ "$VERIFY" = 1 ]; then note "Stable Audio Open 1.0 is not downloaded"
else
  fetch stabilityai/stable-audio-open-1.0 "$SAO" "model_index.json" "scheduler/*" "text_encoder/*" "tokenizer/*" \
    "transformer/*" "projection_model/*" "vae/*" "LICENSE.md" && ok "Stable Audio Open 1.0: $SAO" || note "Stable Audio Open 1.0 left out: sound effects wait for it"
fi

MMA="$MODELS/mmaudio"
if [ -f "$MMA/weights/mmaudio_large_44k_v2.pth" ] && [ -f "$MMA/ext_weights/v1-44.pth" ] && [ -f "$MMA/ext_weights/synchformer_state_dict.pth" ]; then
  ok "MMAudio large_44k_v2: $MMA"
elif [ "$VERIFY" = 1 ]; then note "MMAudio's weights are not downloaded"
else
  fetch hkchengrex/MMAudio "$MMA" "weights/mmaudio_large_44k_v2.pth" "ext_weights/v1-44.pth" "ext_weights/synchformer_state_dict.pth" \
    && ok "MMAudio large_44k_v2: $MMA" || note "MMAudio left out: foley waits for it"
  # what MMAudio loads by name at its first run, fetched now so the first foley does not wait on it
  fetch apple/DFN5B-CLIP-ViT-H-14-384 - >/dev/null && ok "CLIP DFN5B ViT-H-14-384 (Hugging Face cache)" || note "CLIP is fetched at the first foley"
  fetch nvidia/bigvgan_v2_44khz_128band_512x - >/dev/null && ok "BigVGAN v2 vocoder (Hugging Face cache)" || note "BigVGAN is fetched at the first foley"
fi
echo "Done. In the studio: Sound → Sound effects."
