#!/bin/bash
# The pieces behind the Library actions on a DGX Spark: "Turnaround sheet", "Upscale", "Remove a person" and "Create a RefMod".
#   bash spark/setup-workflows.sh            # install what is missing (about 2.5 GB of models, three node packs)
#   bash spark/setup-workflows.sh --verify   # check, download nothing
#   bash spark/setup-workflows.sh --no-models   # the node packs only (what --upgrade runs: it fetches no weights)
# Each action runs its author's own ComfyUI nodes, cloned from the author's repository at a pinned commit into
# ComfyUI/custom_nodes. Nothing of theirs is copied into this pack; the Studio talks to them over ComfyUI's API.
#   Turnaround sheet   ComfyUI-H3-ContactSheet by MATLOWAI (GPL-3.0) + the Turnaround LoRA (hub)
#   Upscale            ComfyUI-H3-Video-Upsampler by dntpi / Sandpies (MIT) + LBH-123-AI's H3 latent upscaler
#                      (weights Apache-2.0, 0.7 GB) + the Video Upscaler LoRA (hub)
#   Remove a person    H3 Relay by Akatz Labs (GPL-3.0) + Meta's SAM 3.1 as repackaged by Comfy-Org (SAM License,
#                      1.7 GB) + the Person Remover LoRA (hub)
# The three LoRAs come from the LoRA hub (oneshot-essential-bundle.sh). All three actions render with MiniMax H3
# Ref2VA (oneshot-setup-spark.sh --ref2va).
# Environment: PREFIX, SPARK_MODELS.
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
MODELS="${SPARK_MODELS:-$HOME/spark-studio-models}"
VERIFY=0; NOMODELS=0
for arg in "$@"; do case "$arg" in --verify) VERIFY=1;; --no-models) NOMODELS=1;; *) echo "usage: setup-workflows.sh [--verify] [--no-models]" >&2; exit 2;; esac; done
COMFY="$PREFIX/ComfyUI"
[ -f "$COMFY/main.py" ] || { echo "no ComfyUI at $COMFY: run oneshot-setup-spark.sh first" >&2; exit 1; }
bad=0

# node <folder> <repository> <commit>
node() {
  local dir="$COMFY/custom_nodes/$1" repo="$2" commit="$3"
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$commit" ]; then echo "  ✓ $1 @ ${commit:0:8}"; return 0; fi
  if [ "$VERIFY" = 1 ]; then echo "  ! $1 is missing or not at ${commit:0:8}"; bad=1; return 0; fi
  [ -d "$dir/.git" ] || git clone -q "$repo" "$dir"
  git -C "$dir" fetch -q origin "$commit" 2>/dev/null || git -C "$dir" fetch -q origin
  git -C "$dir" checkout -q "$commit"
  echo "  ✓ $1 @ ${commit:0:8} (new: restart ComfyUI to load it)"; touch "$PREFIX/workflows.restart"
}
node ComfyUI-H3-ContactSheet     https://github.com/matlowai/ComfyUI-H3-ContactSheet     3c8c8c66e69362e5abf9cf4c4e693baddab90ca3
node ComfyUI-H3-Video-Upsampler  https://github.com/dntpi/ComfyUI-H3-Video-Upsampler     36cb612ec3df30094eeb4fec66f1528925ebba73
node h3-relay                    https://github.com/akatz-ai/h3-relay                    cf489b7f2472fdf33866fae36de49aea03ef464f
# Create a RefMod: Luisa (luisacaotica)'s RefMod nodes (MIT), pictures and clips saved as a MiniMax H3 reference with
# the H3 video VAE alone (spark/spark_refmod.py, app/refmod.py); no model of its own
node ComfyUI-MiniMaxH3Mod         https://github.com/Luisacaotica/ComfyUI-MiniMaxH3Mod   54f93eb6569fe35c9303e9bceb1c8b66c5722bbf

# model <repository> <file in it> <folder under $MODELS/Workflows-Comfy> <name there> <sha256>
model() {
  local repo="$1" file="$2" dir="$MODELS/Workflows-Comfy/$3" name="$4" sum="$5"
  if [ -s "$dir/$name" ]; then echo "  ✓ $name"; return 0; fi
  if [ "$VERIFY" = 1 ]; then echo "  ! missing: $dir/$name"; bad=1; return 0; fi
  if [ "$NOMODELS" = 1 ]; then echo "  ! missing: $dir/$name (run spark/setup-workflows.sh to fetch it)"; return 0; fi
  mkdir -p "$dir"
  curl -fL --retry 5 -C - -sS -o "$dir/$name.part" "https://huggingface.co/$repo/resolve/main/$file"
  echo "$sum  $dir/$name.part" | sha256sum -c --quiet - || { echo "  ! $name: checksum mismatch" >&2; rm -f "$dir/$name.part"; exit 1; }
  mv "$dir/$name.part" "$dir/$name"; echo "  ✓ $name (downloaded)"; touch "$PREFIX/workflows.restart"
}
model Comfy-Org/sam3.1 checkpoints/sam3.1_multiplex_fp16.safetensors checkpoints sam3.1_multiplex_fp16.safetensors \
  9ba99c92703c2e8b4f47de2d34a539bb8e18923049e238b780d70dbe6368eb03
model LBH-123-AI/Minimax_h3_latent_Upscaler minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors \
  latent_upscale_models minimax_h3_latent_upscaler_3d_bf16.safetensors 4f57821f5837f32f7142b67d815606dbd7550f194e5c769f7d6c3f83b146a5e6

# ComfyUI reads these folders from extra_model_paths.yaml
YAML="$COMFY/extra_model_paths.yaml"
# (each folder looked for on its own: spark/setup-swap.sh may have added the checkpoints one already)
if ! grep -q "Workflows-Comfy/checkpoints" "$YAML" 2>/dev/null || ! grep -q "Workflows-Comfy/latent_upscale_models" "$YAML" 2>/dev/null; then
  if [ "$VERIFY" = 1 ]; then echo "  ! $YAML does not list the workflow models"; bad=1; else
    grep -q "Workflows-Comfy/checkpoints" "$YAML" 2>/dev/null || printf '  checkpoints: |\n    Workflows-Comfy/checkpoints\n' >> "$YAML"
    grep -q "Workflows-Comfy/latent_upscale_models" "$YAML" 2>/dev/null \
      || printf '  latent_upscale_models: |\n    Workflows-Comfy/latent_upscale_models\n' >> "$YAML"
    echo "  ✓ model folders added to extra_model_paths.yaml"; touch "$PREFIX/workflows.restart"
  fi
fi
# the RefMods: written to the studio's own refmods folder (first, so the node saves there); a second folder named in
# $STUDIO_HOME/refmods/share.txt (another ComfyUI's models/refmods) is read too, and a new RefMod is hard-linked into it
STUDIO_HOME="${STUDIO_HOME:-$HOME/ImaginationStudio}"
if ! grep -q "^studio_refmods:" "$YAML" 2>/dev/null; then
  if [ "$VERIFY" = 1 ]; then echo "  ! $YAML does not list the studio's refmods folder"; bad=1; else
    mkdir -p "$STUDIO_HOME/refmods"
    printf 'studio_refmods:\n  base_path: %s\n  is_default: true\n  refmods: refmods\n' "$STUDIO_HOME" >> "$YAML"
    echo "  ✓ refmods folder added to extra_model_paths.yaml"; touch "$PREFIX/workflows.restart"
  fi
fi
SHARE="$(head -n1 "$STUDIO_HOME/refmods/share.txt" 2>/dev/null || true)"
if [ -n "$SHARE" ] && [ -d "$SHARE" ] && ! grep -q "^shared_refmods:" "$YAML" 2>/dev/null && [ "$VERIFY" = 0 ]; then
  printf 'shared_refmods:\n  base_path: %s\n  refmods: .\n' "$SHARE" >> "$YAML"
  echo "  ✓ shared refmods folder $SHARE added to extra_model_paths.yaml"; touch "$PREFIX/workflows.restart"
fi
# the fp16 video decoder is optional: the Studio's graphs use the files it already has (Ref2VA in bf16, or in int8
# where only Character Swap's int8 file is here, and the 8-bit decoder)
[ -s "$MODELS/MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors" ] \
  || [ -s "$MODELS/MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors" ] \
  || { echo "  ! MiniMax H3 Ref2VA is not installed: bash oneshot-setup-spark.sh --ref2va"; bad=1; }
if [ -e "$PREFIX/workflows.restart" ] && bash "$(dirname "$0")/comfy.sh" status | grep -q running; then
  echo "  restart ComfyUI to load the new nodes: bash spark/comfy.sh stop (the next render starts it)"
fi
exit $bad
