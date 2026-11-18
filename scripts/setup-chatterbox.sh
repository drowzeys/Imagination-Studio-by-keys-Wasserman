#!/usr/bin/env bash
# Optional local voice engine for smaller NVIDIA cards. The Studio runs its takes through the queue.
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
ENV_DIR="${CHATTERBOX_VENV:-$PREFIX/chatterbox-venv}"
MODEL_DIR="${CHATTERBOX_MODEL:-$PREFIX/chatterbox-models/turbo}"
HF_BIN="${HF_BIN:-$PREFIX/venv/bin/hf}"
REVISION="749d1c1a46eb10492095d68fbcf55691ccf137cd"
REQUIRED=(t3_turbo_v1.safetensors s3gen_meanflow.safetensors ve.safetensors conds.pt tokenizer_config.json)

verify() {
  [ -x "$ENV_DIR/bin/python" ] || { echo "Chatterbox environment is missing: $ENV_DIR" >&2; return 1; }
  "$ENV_DIR/bin/python" -c 'from chatterbox.tts_turbo import ChatterboxTurboTTS' >/dev/null 2>&1 || {
    echo "Chatterbox package is missing from $ENV_DIR" >&2
    return 1
  }
  "$ENV_DIR/bin/python" -c 'import torch; assert torch.version.cuda' >/dev/null 2>&1 || {
    echo "Chatterbox needs a CUDA-enabled PyTorch wheel in $ENV_DIR" >&2
    return 1
  }
  for name in "${REQUIRED[@]}"; do
    [ -s "$MODEL_DIR/$name" ] || { echo "Chatterbox model file is missing: $MODEL_DIR/$name" >&2; return 1; }
  done
  echo "Chatterbox Turbo is installed at $MODEL_DIR"
}

if [ "${1:-}" = --verify ]; then verify; exit; fi
if [ "$#" -ne 0 ]; then echo "Usage: bash scripts/setup-chatterbox.sh [--verify]" >&2; exit 2; fi
command -v uv >/dev/null || { echo "uv is required for Chatterbox installation" >&2; exit 1; }
[ -x "$HF_BIN" ] || { echo "Hugging Face CLI is missing: $HF_BIN" >&2; exit 1; }

# Python 3.14 lets the package select a recent PyTorch wheel for Blackwell cards.
if [ ! -x "$ENV_DIR/bin/python" ]; then uv venv "$ENV_DIR" --python 3.14; fi
uv pip install --python "$ENV_DIR/bin/python" 'chatterbox-tts==0.1.7'
mkdir -p "$MODEL_DIR"
"$HF_BIN" download ResembleAI/chatterbox-turbo --revision "$REVISION" --local-dir "$MODEL_DIR"
verify
echo "Restart Imagination Studio. Voice will use Chatterbox Turbo when Drama 3 cannot run here."
