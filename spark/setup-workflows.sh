#!/bin/bash
# The pieces behind the Library actions on a DGX Spark: "Turnaround sheet", "Upscale", "Remove a person" and "Create a RefMod".
#   bash spark/setup-workflows.sh            # install missing node packs and workflow models
#   bash spark/setup-workflows.sh --verify   # check, download nothing
#   bash spark/setup-workflows.sh --no-models   # the node packs only (what --upgrade runs: it fetches no weights)
# Each action runs its author's own ComfyUI nodes, cloned from the author's repository at a pinned commit into
# ComfyUI/custom_nodes. Nothing of theirs is copied into this pack; the Studio talks to them over ComfyUI's API.
#   Turnaround sheet   ComfyUI-H3-ContactSheet by MATLOWAI (GPL-3.0) + the Turnaround LoRA (hub)
#   Upscale            ComfyUI-H3-Video-Upsampler by dntpi / Sandpies (MIT) + LBH-123-AI's H3 latent upscaler
#                      (weights Apache-2.0, 0.7 GB) + the Video Upscaler LoRA (hub)
#   Remove a person    H3 Relay by Akatz Labs (GPL-3.0) + Meta's SAM 3.1 as repackaged by Comfy-Org (SAM License,
#                      1.7 GB) + the Person Remover LoRA (hub)
#   Fix faces          H3 FaceRefine by Carasibana (MIT), H3 Face Fix by PixelAI Labs (MIT),
#                      MaskVidExperiments by drozbay (GPL-3.0), ComfyUI-VOSR2 by
#                      ylchen333 (Apache-2.0) + Bingsu's YOLOv8 face detector (6 MB) + CSWRY's VOSR 2.0 (Apache-2.0,
#                      7 GB) + SAM 3.1; ultralytics (AGPL-3.0) and OpenCV in $PREFIX/face-site, not the venv
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
# Fix faces (spark_workflows.py facefix): Carasibana's H3 FaceRefine (MIT) finds and crops the face, PixelAI Labs'
# H3 Face Size Fade (MIT) reduces the paste on close-ups, drozbay's
# MaskVidExperiments (GPL-3.0) turns SAM's face mask into the latent's noise mask, ylchen333's VOSR 2.0 nodes
# (Apache-2.0) restore the refined crops before they are stitched back
node ComfyUI-H3-FaceRefine        https://github.com/Carasibana/ComfyUI-H3-FaceRefine    d8521d14fe0d721d80cd9417fff5a559cbc21aba
node ComfyUI-H3-FaceFix-PixelAILabs https://github.com/Pixelailabs/ComfyUI-H3-FaceFix-PixelAILabs 58301602a2f6ba54904c25c420367bfa9ffcf8b9
node MaskVidExperiments           https://github.com/drozbay/MaskVidExperiments          c32ed8c17e6fe892a174ebf25fe98b1317de97fb
node ComfyUI-VOSR2                https://github.com/ylchen333/ComfyUI-VOSR2             6a21810b3f4f1a0ffc477c55330db0ac03c671f0
# FaceRefine's face detector needs ultralytics, which needs OpenCV. Neither goes into the Studio's venv (the app runs
# in it too, and keeps no OpenCV): they go, without their dependencies, into a folder of their own that spark/comfy.sh
# puts on the path of ComfyUI's process alone. insightface (identity tracking) is left out: it needs a native build,
# and a clip of one person does not need it (the action tracks with identity_track off).
FACE_SITE="$PREFIX/face-site"
if PYTHONPATH="$FACE_SITE" "$PREFIX/venv/bin/python" -c "import ultralytics, cv2; assert ultralytics.__version__ == '8.4.215'" 2>/dev/null; then
  echo "  ✓ face detector packages (ultralytics 8.4.215, OpenCV headless) in $FACE_SITE"
elif [ "$VERIFY" = 1 ]; then echo "  ! the face detector packages are missing from $FACE_SITE"; bad=1
else
  "$PREFIX/venv/bin/python" -m pip install -q --no-deps --upgrade --target "$FACE_SITE" \
    "ultralytics==8.4.215" "ultralytics-thop==2.2.4" "opencv-python-headless==4.13.0.92"
  echo "  ✓ face detector packages installed in $FACE_SITE"; touch "$PREFIX/workflows.restart"
fi

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
# Fix faces: Bingsu's YOLOv8 face detector (adetailer, Apache-2.0 card, 6 MB) and CSWRY's VOSR 2.0 bundle (Apache-2.0,
# 7 GB), both at pinned revisions. The VOSR 2.0 loader reads ComfyUI/models/vosr2, linked to the folder here.
pinned() {  # pinned <repository> <revision> <file> <folder under Workflows-Comfy> <name> <sha256>
  local dir="$MODELS/Workflows-Comfy/$4"
  if [ -s "$dir/$5" ]; then echo "  ✓ $4/$5"; return 0; fi
  if [ "$VERIFY" = 1 ]; then echo "  ! missing: $dir/$5"; bad=1; return 0; fi
  if [ "$NOMODELS" = 1 ]; then echo "  ! missing: $dir/$5 (run spark/setup-workflows.sh to fetch it)"; return 0; fi
  mkdir -p "$(dirname "$dir/$5")"
  curl -fL --retry 5 -C - -sS -o "$dir/$5.part" "https://huggingface.co/$1/resolve/$2/$3"
  echo "$6  $dir/$5.part" | sha256sum -c --quiet - || { echo "  ! $5: checksum mismatch" >&2; rm -f "$dir/$5.part"; exit 1; }
  mv "$dir/$5.part" "$dir/$5"; echo "  ✓ $4/$5 (downloaded)"; touch "$PREFIX/workflows.restart"
}
pinned Bingsu/adetailer 53cc19de382014514d9d4038601d261a7faa9b7b face_yolov8n.pt ultralytics/bbox face_yolov8n.pt \
  70b640f8f60b1cf0dcc72f30caf3da9495eb2fb6509da48c53374ad6806e6a9c
VOSR_REV=f24b3061b7f350b81e1907bdcfc27f71fb4ff3f3
pinned CSWRY/VOSR $VOSR_REV VOSR2/args.json vosr2 VOSR2/args.json 63b2f423eafae1f684ca860eb8fd0e81aa5fac858397f783d8f337871241c281
pinned CSWRY/VOSR $VOSR_REV VOSR2/checkpoints/ema_model.safetensors vosr2 VOSR2/checkpoints/ema_model.safetensors \
  bdcaa81e4c675b6074de643e27daafe73eb8125d4d0264d1bf30a004e9644b71
pinned CSWRY/VOSR $VOSR_REV Qwen-Image-vae-2d/config.json vosr2 VOSR2/Qwen-Image-vae-2d/config.json \
  1e294faf15188e06fcb72ce09652e8fb7368bd77b8dd794b5b39fe77d610681b
pinned CSWRY/VOSR $VOSR_REV Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors vosr2 VOSR2/Qwen-Image-vae-2d/diffusion_pytorch_model.safetensors \
  1dd2c67f3f29734d48709513ed181d4f351966d76ba95f77a5cff8e1daf547fa
# DINOv2-L comes as a torch pickle: checked, then written as safetensors the way the VOSR 2.0 loader itself converts it
# (weights only, every tensor kept), so the loader never fetches anything at run time
VISION="$MODELS/Workflows-Comfy/vosr2/VOSR2/dinov2_vitl14.safetensors"
if [ ! -s "$VISION" ] && [ "$VERIFY" = 0 ] && [ "$NOMODELS" = 0 ]; then
  pinned CSWRY/VOSR $VOSR_REV torch_cache/checkpoints/dinov2_vitl14_pretrain.pth vosr2 dinov2_vitl14_pretrain.pth \
    d5383ea8f4877b2472eb973e0fd72d557c7da5d3611bd527ceeb1d7162cbf428
  "$PREFIX/venv/bin/python" - "$MODELS/Workflows-Comfy/vosr2/dinov2_vitl14_pretrain.pth" "$VISION" <<'PY'
import sys, torch
from safetensors.torch import save_file
state = torch.load(sys.argv[1], map_location="cpu", weights_only=True)
save_file({k: v.contiguous() for k, v in state.items() if isinstance(v, torch.Tensor)}, sys.argv[2] + ".tmp")
PY
  mv "$VISION.tmp" "$VISION"; rm -f "$MODELS/Workflows-Comfy/vosr2/dinov2_vitl14_pretrain.pth"
  echo "  ✓ vosr2/VOSR2/dinov2_vitl14.safetensors (converted)"
elif [ -s "$VISION" ]; then echo "  ✓ vosr2/VOSR2/dinov2_vitl14.safetensors"
else echo "  ! missing: $VISION"; [ "$VERIFY" = 1 ] && bad=1; fi
if [ "$(readlink "$COMFY/models/vosr2" 2>/dev/null)" != "$MODELS/Workflows-Comfy/vosr2" ]; then
  if [ "$VERIFY" = 1 ]; then echo "  ! $COMFY/models/vosr2 is not linked to $MODELS/Workflows-Comfy/vosr2"; bad=1
  elif [ -d "$COMFY/models/vosr2" ] && [ ! -L "$COMFY/models/vosr2" ] && [ -n "$(ls -A "$COMFY/models/vosr2")" ]; then
    echo "  ! $COMFY/models/vosr2 is a folder of its own: move its files to $MODELS/Workflows-Comfy/vosr2 and run this again"; bad=1
  else
    # an empty folder (the VOSR 2.0 pack makes one when it is first imported) is replaced by the link
    [ -d "$COMFY/models/vosr2" ] && [ ! -L "$COMFY/models/vosr2" ] && rmdir "$COMFY/models/vosr2"
    mkdir -p "$MODELS/Workflows-Comfy/vosr2" "$COMFY/models"; ln -sfn "$MODELS/Workflows-Comfy/vosr2" "$COMFY/models/vosr2"
    echo "  ✓ ComfyUI's models/vosr2 linked to $MODELS/Workflows-Comfy/vosr2"
  fi
fi

# ComfyUI reads these folders from extra_model_paths.yaml
YAML="$COMFY/extra_model_paths.yaml"
# A reference-to-video checkpoint in the standalone ComfyUI-Easy-Install tree can also be
# selected by Video Create. Register its parent with this ComfyUI so the loader sees it too.
EXTRA_VIDEO_MODELS="${STUDIO_EXTRA_VIDEO_MODELS:-$HOME/Documents/Workspace/ComfyUI-Easy-Install/ComfyUI/models/diffusion_models}"
if [ -d "$EXTRA_VIDEO_MODELS" ] && ! grep -q '^studio_extra_video_models:' "$YAML" 2>/dev/null; then
  if [ "$VERIFY" = 1 ]; then echo "  ! $YAML does not list the extra video model folder $EXTRA_VIDEO_MODELS"; bad=1; else
    printf "studio_extra_video_models:\n  base_path: '%s'\n  diffusion_models: '%s'\n" "$(dirname "$EXTRA_VIDEO_MODELS")" "$(basename "$EXTRA_VIDEO_MODELS")" >> "$YAML"
    echo "  ✓ extra video model folder added to extra_model_paths.yaml"; touch "$PREFIX/workflows.restart"
  fi
fi
# (each folder looked for on its own: spark/setup-swap.sh may have added the checkpoints one already)
if ! grep -q "Workflows-Comfy/checkpoints" "$YAML" 2>/dev/null || ! grep -q "Workflows-Comfy/latent_upscale_models" "$YAML" 2>/dev/null; then
  if [ "$VERIFY" = 1 ]; then echo "  ! $YAML does not list the workflow models"; bad=1; else
    grep -q "Workflows-Comfy/checkpoints" "$YAML" 2>/dev/null || printf '  checkpoints: |\n    Workflows-Comfy/checkpoints\n' >> "$YAML"
    grep -q "Workflows-Comfy/latent_upscale_models" "$YAML" 2>/dev/null \
      || printf '  latent_upscale_models: |\n    Workflows-Comfy/latent_upscale_models\n' >> "$YAML"
    echo "  ✓ model folders added to extra_model_paths.yaml"; touch "$PREFIX/workflows.restart"
  fi
fi
# the folder H3 FaceRefine lists its face detectors from: a block of its own, since the blocks above may end with the
# refmods' one (a key appended to the end would land under that block's base path)
if ! grep -q "^studio_faces:" "$YAML" 2>/dev/null; then
  if [ "$VERIFY" = 1 ]; then echo "  ! $YAML does not list the face detector folder"; bad=1; else
    printf 'studio_faces:\n  base_path: %s\n  ultralytics_bbox: Workflows-Comfy/ultralytics/bbox\n' "$MODELS" >> "$YAML"
    echo "  ✓ face detector folder added to extra_model_paths.yaml"; touch "$PREFIX/workflows.restart"
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
