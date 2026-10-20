#!/usr/bin/env bash
# ComfyUI on a Mac for what a DGX Spark runs there: Upscale, Find a person and Remove a person (Library actions) and the
# MOSS-Image picture model. The same graphs and the same authors' node packs as on a Spark (spark/spark_generate.py,
# spark/spark_workflows.py), on PyTorch's Metal device, in an environment of their own (never the Studio's MLX venv):
#   bash scripts/setup-comfy.sh --upscale     # dntpi's H3 Video Upsampler + LBH-123-AI's latent upscaler (0.7 GB)
#   bash scripts/setup-comfy.sh --remove      # Akatz Labs' H3 Relay + Meta's SAM 3.1 (1.7 GB): Find and Remove a person
#   bash scripts/setup-comfy.sh --moss        # MOSS-Image (OpenMOSS, Apache 2.0): 28 GB, Comfy-Org/MOSS-Image's files
#   bash scripts/setup-comfy.sh --verify      # check what is installed, download nothing
# oneshot-setup.sh --upscale / --remove / --moss run this. Under $PREFIX (default ~/.local/opt/tensorfold-studio):
#   comfy-venv/    PyTorch (Metal) and ComfyUI's requirements (about 1.6 GB)
#   ComfyUI/       ComfyUI at the Spark's commit (COMFY_COMMIT in oneshot-setup-spark.sh), the node packs at the Spark's
#                  pins (spark/setup-workflows.sh)
#   comfy-models/  (MAC_COMFY_MODELS) the files above and MiniMax H3's audio decoder (0.6 GB). MiniMax H3 itself is not
#                  downloaded again: mac/studio_files reads the Mac's own Ref2VA transformer, text encoder and video
#                  decoder in H3_MODEL_DIR (oneshot-setup.sh --ref2va), which Upscale and Remove a person need.
# The LoRAs (Video Upscaler, Person Remover) come from the LoRA hub as on a Spark. Licences: SAM 3.1 is under Meta's SAM
# License, the latent upscaler's weights Apache-2.0, the H3 Video Upsampler MIT, H3 Relay GPL-3.0 (cloned, not copied).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
MODELS="${MAC_COMFY_MODELS:-$PREFIX/comfy-models}"
H3_MODEL_DIR="${H3_MODEL_DIR:-$HOME/h3-models/MiniMax-H3}"
VENV="$PREFIX/comfy-venv"; COMFY="$PREFIX/ComfyUI"
# the Spark's pins, read from its own scripts so the two never drift apart
COMFY_COMMIT="$(sed -n 's/^COMFY_COMMIT=\([0-9a-f]*\).*/\1/p' "$HERE/oneshot-setup-spark.sh")"
pin() { awk -v n="$1" '$1 == "node" && $2 == n {print $4}' "$HERE/spark/setup-workflows.sh"; }
TORCH="torch==2.14.1 torchvision==0.29.1"
UPSCALE=0; REMOVE=0; MOSS=0; VERIFY=0
for arg in "$@"; do
  case "$arg" in
    --upscale) UPSCALE=1;; --remove) REMOVE=1;; --moss) MOSS=1;; --verify) VERIFY=1;;
    *) echo "usage: setup-comfy.sh [--upscale] [--remove] [--moss] [--verify]" >&2; exit 2;;
  esac
done
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
ok() { echo "  ✓ $*"; }
note() { echo "  ! $*"; }
bad=0

echo "ComfyUI for the Library actions and MOSS-Image (Mac)"
if [ "$(uname -s)" != Darwin ]; then
  note "this is the Mac's: on a DGX Spark these are spark/setup-workflows.sh and oneshot-setup-spark.sh --moss"; exit 0
fi
if [ "$VERIFY" = 1 ]; then  # what is there decides what is checked
  [ -d "$COMFY/custom_nodes/ComfyUI-H3-Video-Upsampler" ] && UPSCALE=1
  [ -d "$COMFY/custom_nodes/h3-relay" ] && REMOVE=1
  [ -s "$MODELS/diffusion_models/moss_image_sft_bf16.safetensors" ] && MOSS=1
  [ -d "$COMFY" ] || { note "no ComfyUI at $COMFY: bash scripts/setup-comfy.sh --upscale|--remove|--moss"; exit 1; }
fi
[ -n "$COMFY_COMMIT" ] || { echo "no COMFY_COMMIT in oneshot-setup-spark.sh" >&2; exit 1; }
command -v uv >/dev/null || { echo "  uv is needed: brew install uv" >&2; exit 1; }

# the environment and ComfyUI itself
if [ ! -x "$VENV/bin/python" ]; then
  [ "$VERIFY" = 0 ] || { note "no environment at $VENV"; exit 1; }
  uv venv -q -p 3.11 "$VENV"
fi
if [ "$(git -C "$COMFY" rev-parse HEAD 2>/dev/null)" != "$COMFY_COMMIT" ]; then
  [ "$VERIFY" = 0 ] || { note "ComfyUI is missing or not at ${COMFY_COMMIT:0:8}"; exit 1; }
  [ -d "$COMFY/.git" ] || git clone -q https://github.com/comfyanonymous/ComfyUI.git "$COMFY"
  git -C "$COMFY" fetch -q origin "$COMFY_COMMIT" 2>/dev/null || git -C "$COMFY" fetch -q origin
  git -C "$COMFY" checkout -q "$COMFY_COMMIT"
fi
if ! "$VENV/bin/python" -c "import torch, aiohttp, comfy_kitchen, av" 2>/dev/null; then
  [ "$VERIFY" = 0 ] || { note "the environment at $VENV is not complete"; exit 1; }
  # shellcheck disable=SC2086
  uv pip install -q -p "$VENV/bin/python" $TORCH -r "$COMFY/requirements.txt"
fi
ok "ComfyUI ${COMFY_COMMIT:0:8} in $VENV"

node() {  # node <folder> <repository>: the author's pack at the Spark's pin
  local dir="$COMFY/custom_nodes/$1" commit; commit="$(pin "$1")"
  [ -n "$commit" ] || { echo "no pin for $1 in spark/setup-workflows.sh" >&2; exit 1; }
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$commit" ]; then ok "$1 @ ${commit:0:8}"; return 0; fi
  if [ "$VERIFY" = 1 ]; then note "$1 is missing or not at ${commit:0:8}"; bad=1; return 0; fi
  [ -d "$dir/.git" ] || git clone -q "$2" "$dir"
  git -C "$dir" fetch -q origin "$commit" 2>/dev/null || git -C "$dir" fetch -q origin
  git -C "$dir" checkout -q "$commit"; ok "$1 @ ${commit:0:8}"
}
model() {  # model <repository> <file in it> <folder under $MODELS> <name there> [sha256]
  local target="$MODELS/$3/$4"
  if [ -s "$target" ]; then ok "$4"; return 0; fi
  if [ "$VERIFY" = 1 ]; then note "missing: $target"; bad=1; return 0; fi
  mkdir -p "$MODELS/$3"
  "$VENV/bin/python" - "$1" "$2" "$target" <<'PY'
import os, shutil, sys, tempfile
from huggingface_hub import hf_hub_download
repo, name, target = sys.argv[1:]
with tempfile.TemporaryDirectory(dir=os.path.dirname(target)) as folder:
    shutil.move(hf_hub_download(repo, name, local_dir=folder), target + ".part")
PY
  if [ -n "${5:-}" ] && [ "$(shasum -a 256 "$target.part" | cut -c1-64)" != "$5" ]; then
    rm -f "$target.part"; echo "  ! $4: checksum mismatch" >&2; exit 1
  fi
  mv "$target.part" "$target"; ok "$4 (downloaded)"
}

if [ "$UPSCALE" = 1 ] || [ "$REMOVE" = 1 ]; then
  [ "$UPSCALE" = 0 ] || node ComfyUI-H3-Video-Upsampler https://github.com/dntpi/ComfyUI-H3-Video-Upsampler
  [ "$REMOVE" = 0 ] || node h3-relay https://github.com/akatz-ai/h3-relay
  model FastVideo/FastVideo-FastH3-Comfy vae/minimax_h3_audio_vae_fp32.safetensors vae minimax_h3_audio_vae_fp32.safetensors \
    8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48
  [ "$UPSCALE" = 0 ] || model LBH-123-AI/Minimax_h3_latent_Upscaler \
    minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors latent_upscale_models \
    minimax_h3_latent_upscaler_3d_bf16.safetensors 4f57821f5837f32f7142b67d815606dbd7550f194e5c769f7d6c3f83b146a5e6
  [ "$REMOVE" = 0 ] || model Comfy-Org/sam3.1 checkpoints/sam3.1_multiplex_fp16.safetensors checkpoints \
    sam3.1_multiplex_fp16.safetensors 9ba99c92703c2e8b4f47de2d34a539bb8e18923049e238b780d70dbe6368eb03
  if [ -d "$H3_MODEL_DIR/Ref2VA/transformer" ] && [ -d "$H3_MODEL_DIR/FL2VA/text_encoder" ] && [ -d "$H3_MODEL_DIR/FL2VA/video_vae" ]; then
    ok "MiniMax H3 Ref2VA, its text encoder and video decoder: read from $H3_MODEL_DIR"
  else
    note "MiniMax H3 Ref2VA is not installed: bash oneshot-setup.sh --ref2va (Upscale and Remove a person render on it)"; bad=1
  fi
fi
if [ "$MOSS" = 1 ]; then
  [ "$VERIFY" = 1 ] || echo "  MOSS-Image (OpenMOSS) is under the Apache License 2.0: https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT (28 GB)"
  model Comfy-Org/MOSS-Image diffusion_models/moss_image_sft_bf16.safetensors diffusion_models moss_image_sft_bf16.safetensors
  model Comfy-Org/MOSS-Image text_encoders/qwen3.5_9b_bf16.safetensors text_encoders qwen3.5_9b_bf16.safetensors
  model Comfy-Org/MOSS-Image vae/flux2-vae_small.safetensors vae flux2-vae_small.safetensors
  [ -f "$COMFY/comfy_extras/nodes_moss_image.py" ] || { note "this ComfyUI has no MOSS-Image nodes"; bad=1; }
fi
"$VENV/bin/python" "$HERE/mac/comfy_files.py" "$PREFIX" >/dev/null && ok "model folders: $MODELS ($PREFIX/comfy-models.yaml)"
if bash "$HERE/mac/comfy.sh" status | grep -q "^running"; then
  echo "  restart ComfyUI to see what is new: bash mac/comfy.sh stop (the next job starts it)"
fi
exit $bad
