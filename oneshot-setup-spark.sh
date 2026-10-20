#!/bin/bash
# TensorFold Studio on an NVIDIA DGX Spark (GB10, 128 GB, CUDA 13): one-shot install.
#   bash oneshot-setup-spark.sh --all         # every feature below in one go (about 400 GB of disk), then a test clip
#   bash oneshot-setup-spark.sh               # the core (pictures and FastH3 video with sound), then a 5 second test clip
#   bash oneshot-setup-spark.sh --image-only  # Qwen-Image-2.1 only (26 GB)
#   bash oneshot-setup-spark.sh --no-render   # skip the test clip
#   bash oneshot-setup-spark.sh --ref2va      # add MiniMax H3 Ref2VA (reference pictures hold a face), 39 GB more
#   bash oneshot-setup-spark.sh --h3-base     # add MiniMax H3's own first-and-last-frame transformer, 40 GB more:
#                                             # what keyframe LoRAs (360 Orbit, Transformation) were trained on
#                                             # and, with it, the Library's actions (Turnaround sheet, Upscale, Remove a person:
#                                             # their authors' node packs and 2.5 GB of models; --no-workflows leaves them out)
#   bash oneshot-setup-spark.sh --image-edit  # add Qwen-Image-2.1's 40-step base model, 14 GB more (Qwen Research License,
#                                             # non-commercial): Edit a picture works without it, on Turbo's 8 steps
#   bash oneshot-setup-spark.sh --flux3       # add FLUX3-K (Black Forest Labs' FLUX 3 Image Klein) as picture models, 41 GB more:
#                                             # FLUX3-K Fast (distilled, 16 steps) and Full (base, 50 steps), their text
#                                             # encoder, BFL's prompt upsampler and the decoder. FLUX Kommunity License; the
#                                             # Hugging Face repo is gated: accept its licence and `hf auth login` first
#   bash oneshot-setup-spark.sh --moss        # add MOSS-Image (OpenMOSS) as a picture model, 30 GB more: the SFT checkpoint
#                                             # (bf16), its Qwen3.5 9B text encoder (bf16) and the FLUX.2 small decoder, from
#                                             # Comfy-Org/MOSS-Image. Apache 2.0, commercial use allowed
#   bash oneshot-setup-spark.sh --music       # add MiniMax Music 3 for the Music tab, 22 GB more: its DiT (fp16), text encoder
#                                             # (pruned, bf16) and decoder, read by ComfyUI and by TensorFold's CUDA engine,
#                                             # which it builds too (spark/build-music3.sh). MiniMax-Music3 Community License:
#                                             # commercial use allowed; a commercial UI shows "MiniMax-Music3"; above US$20M
#                                             # yearly revenue needs MiniMax's written authorisation
#   bash oneshot-setup-spark.sh --bolt        # add NVIDIA Nemotron 3.5 Bolt as the Studio's own brain engine, about 25 GB more:
#                                             # NVIDIA's 24 GB checkpoint (a326822a) and its LiLiCorr drafter (4c6a5613), and
#                                             # the engine (spark/build-bolt.sh: TensorFold with MiaAI-Lab's Bolt backend).
#                                             # OpenMDW License Agreement 1.1: commercial use allowed, keep the licence and
#                                             # NVIDIA's notices with any copy (scripts/setup-bolt.sh)
#   bash oneshot-setup-spark.sh --sound       # add sound effects and foley (Sound -> Sound effects), 11 GB more: Stability AI's
#                                             # Stable Audio Open 1.0 on ComfyUI's own nodes (Stability AI Community License; the
#                                             # Hugging Face repo is gated: accept its licence there and sign in first) and
#                                             # MMAudio large_44k_v2 on kijai's ComfyUI-MMAudio at a pinned commit (weights
#                                             # CC-BY-NC 4.0: non-commercial)
#   bash oneshot-setup-spark.sh --motion      # add the Motion Previs tab's analysis: pose, camera solve and AI depth (0.5 GB, own environment)
#   bash oneshot-setup-spark.sh --no-loras    # skip the essential LoRA bundle (about 4 GB from the LoRAs' authors; --loras=camera,style
#                                       # takes some groups, --lora-bundle=PATH installs from a bundle on disk with no other network)
#   bash oneshot-setup-spark.sh --voice       # add Fish Audio Drama 3 for the Voice tab, about 41 GB more (scripts/setup-voice.sh):
#                                             # TensorFold's CUDA engine (spark/build-drama3.sh: prebuilt from the carrier, else
#                                             # built), Fish's 39 GB checkpoint (fishaudio/drama-3, linked if a copy is already on
#                                             # this machine) and its codec converted once here (1.6 GB), then a test line.
#                                             # Fish Audio Community License v1.0: research, non-commercial and small-company
#                                             # commercial use; a hosted TTS or voice-cloning service needs Fish Audio's licence
#   bash oneshot-setup-spark.sh --verify      # check an existing install, download nothing
#   bash oneshot-setup-spark.sh --upgrade     # after `git pull`: move an existing install to this version (environment, ComfyUI pin,
#                                             # engine, Motion Previs environment), fetch no weights, then --verify. Safe to run twice.
# What it installs under $PREFIX (default ~/.local/opt/tensorfold-studio):
#   venv/       Python 3.12, PyTorch for CUDA 13, ComfyUI's requirements, the web app's
#   ComfyUI/    pinned; its own flash and block-sparse attention kernels are what the renders use
# and under $SPARK_MODELS (default ~/spark-studio-models), 99 GB:
#   FastH3 8-Step V2 for ComfyUI, bf16 transformer 44 GB, H3 text encoder (8-bit) 27 GB, video (8-bit) and audio decoders 3 GB
#   Qwen-Image-2.1-Turbo for ComfyUI (the 8-step checkpoint), 14 GB, its text encoder (8-bit) 9 GB, and decoder
# With --ref2va, 39 GB more: MiniMax H3 Ref2VA for ComfyUI (bf16, 37 GB) and LightX2V's 4-pass Ref2VA Turbo adapter (2 GB)
# Licenses: Qwen-Image-2.1-Turbo is non-commercial (Qwen Research License); FastH3 and the H3
# parts are under the MiniMax H3 Community License (territory limits).
# Credit: FastVideo (Hao AI Lab), MiniMax, the Qwen team, ComfyUI, Ash Hart (TensorFold, whose Studio this is
# the CUDA build of).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local/opt/tensorfold-studio}"
MODELS="${SPARK_MODELS:-$HOME/spark-studio-models}"
COMFY_COMMIT=b9b290e43f1eb379946dfeb013faf9bbefa1a618  # v0.42.0 and later: ComfyUI's FLUX 3 and MOSS-Image nodes
IMAGE_ONLY=0; RENDER=1; VERIFY=0; REF2VA=0; MOTION=0; VOICE=0; UPGRADE=0; LORAS=""; H3_BASE=0; WORKFLOWS=1; IMAGE_EDIT=0; FLUX3=0; MOSS=0; MUSIC=0; SOUND=0; BOLT=0
for arg in "$@"; do
  case "$arg" in
    --ref2va) REF2VA=1;; --h3-base) H3_BASE=1;; --image-edit) IMAGE_EDIT=1;; --flux3) FLUX3=1;; --moss) MOSS=1;; --music) MUSIC=1;; --bolt) BOLT=1;; --sound) SOUND=1;; --motion) MOTION=1;; --voice) VOICE=1;; --image-only) IMAGE_ONLY=1;; --no-render) RENDER=0;; --verify) VERIFY=1; RENDER=0;; --upgrade) UPGRADE=1; RENDER=0; MOTION=1;;
    --all) REF2VA=1; H3_BASE=1; IMAGE_EDIT=1; FLUX3=1; MOSS=1; MUSIC=1; SOUND=1; BOLT=1; VOICE=1; MOTION=1;;  # every feature
    --no-workflows) WORKFLOWS=0;;
    --loras) LORAS=all;; --loras=*) LORAS="${arg#--loras=}";; --no-loras) LORAS=none;; --lora-bundle=*) LORA_BUNDLE="${arg#--lora-bundle=}";;
    *) echo "unknown option $arg" >&2; exit 2;;
  esac
done
export PATH="$HOME/.local/bin:/usr/local/cuda/bin:$PATH"
say() { printf '==> %s\n' "$*"; }
ok() { printf '  \342\234\223 %s\n' "$*"; }
die() { printf '  \342\234\227 %s\n' "$*" >&2; exit 1; }

say "Preflight"
if [ "$(uname -s)" = Darwin ]; then echo "macOS detected: installing the Mac (Metal) build"; exec bash "$HERE/oneshot-setup.sh" "$@"; fi
[ "$(uname -s)" = Linux ] || die "this installs on a DGX Spark (Linux, CUDA) or an Apple-silicon Mac"
command -v nvidia-smi >/dev/null || die "no nvidia-smi: this needs an NVIDIA GPU with its driver"
# --upgrade moves an existing install to this version and fetches no weights: an image-only install stays image-only,
# Ref2VA stays if it is there. The studio folder (clips, projects, LoRAs, settings) is not touched.
INSTALLED="$(cat "$PREFIX/VERSION" 2>/dev/null || echo "2.6 or earlier")"
if [ "$UPGRADE" = 1 ]; then
  [ -x "$PREFIX/venv/bin/python" ] || die "nothing to upgrade at $PREFIX: run oneshot-setup-spark.sh without --upgrade"
  [ -s "$MODELS/FastH3-Comfy/diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors" ] || IMAGE_ONLY=1
  [ ! -s "$MODELS/MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors" ] || REF2VA=1
  [ ! -s "$MODELS/FLUX3-K-Comfy/diffusion_models/flux3-image-klein-bf16.safetensors" ] || FLUX3=1
  [ ! -s "$MODELS/FLUX3-K-Comfy/diffusion_models/flux3_image_klein_sd_bf16.safetensors" ] || FLUX3=1
  [ ! -s "$MODELS/MOSS-Image-Comfy/diffusion_models/moss_image_sft_bf16.safetensors" ] || MOSS=1
  [ ! -s "$MODELS/MiniMax-Music3-Comfy/diffusion_models/minimax_music3_dit_fp16.safetensors" ] || MUSIC=1
  [ ! -d "$PREFIX/ComfyUI/custom_nodes/ComfyUI-MMAudio" ] || SOUND=1
  [ ! -s "${BOLT_MODELS:-$MODELS}/Nemotron-3.5-Bolt-24GB/model.safetensors.index.json" ] || BOLT=1
  [ ! -x "$PREFIX/drama3/bin/drama3-generate" ] || VOICE=1
  ok "upgrade: installed $INSTALLED -> $(cat "$HERE/VERSION")$([ "$IMAGE_ONLY" = 0 ] || echo " (image-only install)")"
else ok "version $(cat "$HERE/VERSION")$([ ! -f "$PREFIX/VERSION" ] || echo " (installed: $INSTALLED)")"; fi
GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
MEM_GB=$(awk '/MemTotal/{print int($2/1048576)}' /proc/meminfo)
ok "$GPU, $(uname -m), ${MEM_GB} GB"
case "$GPU" in *GB10*) ;; *) echo "  ! measured only on a DGX Spark (GB10, 128 GB unified memory); other GPUs are untested";; esac
[ "$MEM_GB" -ge 100 ] || [ "$IMAGE_ONLY" = 1 ] || die "video needs about 90 GB of memory at 1344x768 (128 GB machine); this one has ${MEM_GB} GB. --image-only fits."
for tool in git ffmpeg ffprobe; do command -v "$tool" >/dev/null || die "install $tool first (sudo apt install $([ "$tool" = ffprobe ] && echo ffmpeg || echo "$tool"))"; done
if ! command -v uv >/dev/null; then
  [ "$VERIFY" = 0 ] || die "no uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
# disk for what is still to fetch (GB, with room): each part asked for whose weights are not here yet, by the file that
# says it is (the sizes in the header above); a machine that holds some of them already needs only the rest
missing() { [ ! -s "$1" ]; }
NEED=5
missing "$MODELS/Qwen-Image-2.1-Comfy/diffusion_models/qwen_image_2.1_turbo_bf16.safetensors" && NEED=$((NEED + 26))
if [ "$IMAGE_ONLY" = 0 ]; then
  missing "$MODELS/FastH3-Comfy/diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors" && NEED=$((NEED + 75))
  [ "$REF2VA" = 0 ] || ! missing "$MODELS/MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors" || NEED=$((NEED + 40))
  [ "$H3_BASE" = 0 ] || ! missing "$MODELS/MiniMax-H3-Comfy/diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors" || NEED=$((NEED + 42))
  [ "$IMAGE_EDIT" = 0 ] || ! missing "$MODELS/Qwen-Image-2.1-Comfy/diffusion_models/qwen_image_2.1_bf16.safetensors" || NEED=$((NEED + 15))
  [ "$FLUX3" = 0 ] || [ -s "$MODELS/FLUX3-K-Comfy/diffusion_models/flux3_image_klein_sd_bf16.safetensors" ] \
    || [ -s "$MODELS/FLUX3-K-Comfy/diffusion_models/flux3-image-klein-bf16.safetensors" ] || NEED=$((NEED + 42))
  [ "$MOSS" = 0 ] || ! missing "$MODELS/MOSS-Image-Comfy/diffusion_models/moss_image_sft_bf16.safetensors" || NEED=$((NEED + 31))
  [ "$MUSIC" = 0 ] || ! missing "$MODELS/MiniMax-Music3-Comfy/diffusion_models/minimax_music3_dit_fp16.safetensors" || NEED=$((NEED + 23))
  [ "$SOUND" = 0 ] || ! missing "$MODELS/Sound-Comfy/stable-audio-open-1.0/model.safetensors" || NEED=$((NEED + 12))
  [ "$BOLT" = 0 ] || ! missing "${BOLT_MODELS:-$MODELS}/Nemotron-3.5-Bolt-24GB/model.safetensors.index.json" || NEED=$((NEED + 26))
  [ "$VOICE" = 0 ] || ! missing "${DRAMA3_CUDA_MODEL:-${DRAMA3_CHECKPOINT:-$HOME/models/drama-3}}/config.json" || NEED=$((NEED + 45))
fi
if [ "$VERIFY" = 0 ]; then
  mkdir -p "$PREFIX" "$MODELS"
  FREE=$(df -BG --output=avail "$MODELS" | tail -1 | tr -dc 0-9)
  [ "$FREE" -ge "$NEED" ] || die "$MODELS has ${FREE} GB free; what is not here yet needs about ${NEED} GB"
  ok "disk: ${FREE} GB free, about ${NEED} GB to fetch"
fi

say "Environment: Python 3.12 and PyTorch for CUDA 13 -> $PREFIX/venv"
if [ ! -x "$PREFIX/venv/bin/python" ]; then
  [ "$VERIFY" = 0 ] || die "no environment at $PREFIX/venv"
  uv venv "$PREFIX/venv" --python 3.12 --python-preference only-managed --seed -q
fi
PY="$PREFIX/venv/bin/python"
pipi() { UV_TORCH_BACKEND=cu130 uv pip install -q --python "$PY" "$@"; }
if ! "$PY" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  [ "$VERIFY" = 0 ] || die "PyTorch does not see the GPU"
  pipi torch torchvision torchaudio
fi
"$PY" -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" || die "PyTorch does not see the GPU"
ok "torch $("$PY" -c 'import torch; print(torch.__version__, "on", torch.cuda.get_device_name(0))')"

say "ComfyUI @ ${COMFY_COMMIT:0:8} -> $PREFIX/ComfyUI"
if [ "$(git -C "$PREFIX/ComfyUI" rev-parse HEAD 2>/dev/null)" != "$COMFY_COMMIT" ]; then
  [ "$VERIFY" = 0 ] || die "ComfyUI is missing or not at the pinned commit"
  [ -d "$PREFIX/ComfyUI/.git" ] || git clone -q https://github.com/comfyanonymous/ComfyUI "$PREFIX/ComfyUI"
  git -C "$PREFIX/ComfyUI" fetch -q origin "$COMFY_COMMIT" 2>/dev/null || git -C "$PREFIX/ComfyUI" fetch -q origin
  git -C "$PREFIX/ComfyUI" checkout -q "$COMFY_COMMIT"
fi
if [ "$VERIFY" = 0 ]; then
  pipi -r "$PREFIX/ComfyUI/requirements.txt" huggingface_hub fastapi uvicorn python-multipart pillow
  # speech-to-text for the QA gate of the Wasserman Sharp Spec (app/qa.py); without it that gate is shown as skipped
  pipi faster-whisper || echo "  note: faster-whisper was not installed; the QA gate's speech check will be skipped"
  rm -rf "$PREFIX/ComfyUI/custom_nodes/fasth3_sparse"
  cat > "$PREFIX/ComfyUI/extra_model_paths.yaml" <<YAML
studio:
  base_path: $MODELS
  diffusion_models: |
    FastH3-Comfy/diffusion_models
    Qwen-Image-2.1-Comfy/diffusion_models
    MiniMax-H3-Comfy/diffusion_models
    FLUX3-K-Comfy/diffusion_models
    MOSS-Image-Comfy/diffusion_models
    MiniMax-Music3-Comfy/diffusion_models
  text_encoders: |
    FastH3-Comfy/text_encoders
    Qwen-Image-2.1-Comfy/text_encoders
    FLUX3-K-Comfy/text_encoders
    MOSS-Image-Comfy/text_encoders
    MiniMax-Music3-Comfy/text_encoders
    Sound-Comfy/text_encoders
  vae: |
    FastH3-Comfy/vae
    Qwen-Image-2.1-Comfy/vae
    FLUX3-K-Comfy/vae
    MOSS-Image-Comfy/vae
    MiniMax-Music3-Comfy/vae
  loras: |
    loras
    MiniMax-H3-Comfy/loras
  checkpoints: |
    Workflows-Comfy/checkpoints
    Sound-Comfy/checkpoints
  latent_upscale_models: |
    Workflows-Comfy/latent_upscale_models
  mmaudio: |
    Sound-Comfy/mmaudio
YAML
fi
"$PY" -c "import comfy_kitchen, aiohttp, fastapi, uvicorn, PIL" || die "the environment is missing packages"
ok "ComfyUI and its requirements"

# elsewhere PLACE...: before a download, what is already somewhere else on this machine is linked at PLACE instead
# (app/discover.py: a symlink, nothing moved or copied; one scan, kept an hour)
elsewhere() {
  [ "$VERIFY" = 0 ] || return 0
  local py="$PREFIX/venv/bin/python" said; [ -x "$py" ] || py=python3
  said="$(PREFIX="$PREFIX" SPARK_MODELS="$MODELS" "$py" "$HERE/app/discover.py" --scan --link --max-age 3600 --paths "$@" 2>/dev/null)" || return 0
  [ -z "$said" ] || { echo "  found elsewhere on this machine, linking instead of downloading:"; echo "$said" | sed 's/^/    /'; }
}

# fetch <repo> <folder under $MODELS> <file in the repo>...: download what is not there yet
fetch() {
  local repo="$1" folder="$2" missing=(); shift 2
  for file in "$@"; do [ -s "$MODELS/$folder/$file" ] || missing+=("$file"); done
  [ ${#missing[@]} = 0 ] && return 0
  [ "$VERIFY" = 0 ] || die "missing in $MODELS/$folder: ${missing[*]}"
  local file found=(); for file in "${missing[@]}"; do found+=("$MODELS/$folder/$file"); done
  elsewhere "${found[@]}"
  missing=(); for file in "$@"; do [ -s "$MODELS/$folder/$file" ] || missing+=("$file"); done
  [ ${#missing[@]} = 0 ] && return 0
  if [ "$UPGRADE" = 1 ]; then echo "  ! missing in $MODELS/$folder: ${missing[*]}: --upgrade fetches no weights (run oneshot-setup-spark.sh without it)"; return 0; fi
  "$PREFIX/venv/bin/hf" download "$repo" "${missing[@]}" --local-dir "$MODELS/$folder" >/dev/null
  for file in "${missing[@]}"; do [ -s "$MODELS/$folder/$file" ] || die "$file did not download"; done
}

say "Qwen-Image-2.1-Turbo for ComfyUI (24 GB) -> $MODELS"
fetch Comfy-Org/Qwen-Image-2.1 Qwen-Image-2.1-Comfy diffusion_models/qwen_image_2.1_turbo_bf16.safetensors \
  text_encoders/qwen3vl_8b_int8_convrot.safetensors vae/qwen_image_2.1_vae_bf16.safetensors
ok "Qwen-Image-2.1-Turbo (8 steps), its text encoder and decoder"

if [ "$IMAGE_ONLY" = 0 ]; then
  say "FastH3 8-Step V2 for ComfyUI (75 GB) -> $MODELS"
  fetch FastVideo/FastVideo-FastH3-Comfy FastH3-Comfy diffusion_models/fastvideo_fasth3_8step_v2_pruned_bf16.safetensors \
    text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors vae/minimax_h3_video_vae_int8_convrot.safetensors \
    vae/minimax_h3_audio_vae_fp32.safetensors
  ok "FastH3 transformer (bf16), H3 text encoder (8-bit), video decoder (8-bit) and audio decoder"
fi

if [ "$REF2VA" = 1 ] && [ "$IMAGE_ONLY" = 0 ]; then
  say "MiniMax H3 Ref2VA for ComfyUI and its Turbo adapter (39 GB) -> $MODELS"
  fetch Comfy-Org/MiniMax-H3 MiniMax-H3-Comfy diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors \
    loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
  ok "Ref2VA transformer (bf16) and LightX2V's Ref2VA Turbo adapter (4 passes)"
  # a running ComfyUI read its model folders when it started
  if bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see them: bash spark/comfy.sh stop (the next render starts it)"; fi
fi

if [ "$IMAGE_EDIT" = 1 ] && [ "$VERIFY" = 0 ]; then
  say "Qwen-Image-2.1's 40-step base model for ComfyUI (14 GB) -> $MODELS"
  echo "  Qwen-Image-2.1 is under the Qwen Research License Agreement: NON-COMMERCIAL use only. Read it first:"
  echo "  https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE"
  fetch Comfy-Org/Qwen-Image-2.1 Qwen-Image-2.1-Comfy diffusion_models/qwen_image_2.1_bf16.safetensors
  ok "Qwen-Image-2.1 base model (bf16): BASE=1 bash scripts/edit.sh, and the 40 steps choice of Edit a picture"
  if bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see it: bash spark/comfy.sh stop (the next render starts it)"; fi
fi

if [ "$FLUX3" = 1 ]; then
  say "FLUX3-K for ComfyUI: Fast and Full, text encoder, prompt upsampler, decoder (41 GB) -> $MODELS"
  [ "$VERIFY" = 1 ] || { echo "  FLUX 3 Image Klein is under the FLUX Kommunity License: non-commercial use, and commercial use of the outputs"
    echo "  only for Qualifying Users (under US\$5M revenue). Read it, and accept it on the model page, first:"
    echo "  https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy"; }
  # BFL renamed the checkpoints on 2026-10-17 (the same files, same SHA-256): an older install's are renamed, not fetched again
  KLEIN=()
  for pair in sd_bf16:flux3-image-klein-bf16 base_bf16:flux3-image-klein-base-bf16; do
    old="diffusion_models/flux3_image_klein_${pair%%:*}.safetensors"; new="diffusion_models/${pair#*:}.safetensors"
    if [ -s "$MODELS/FLUX3-K-Comfy/$old" ] && [ ! -e "$MODELS/FLUX3-K-Comfy/$new" ]; then
      if [ "$VERIFY" = 1 ]; then echo "  - $old has BFL's old name (the app reads it; the setup renames it)"; new="$old"
      else mv "$MODELS/FLUX3-K-Comfy/$old" "$MODELS/FLUX3-K-Comfy/$new" && ok "renamed $old to BFL's new name $new"; fi
    fi
    KLEIN+=("$new")
  done
  fetch black-forest-labs/flux-3-image-klein-comfy FLUX3-K-Comfy "${KLEIN[@]}" text_encoders/qwen3vl_4b_bf16.safetensors \
    text_encoders/qwen3.5_9b_flux3_prompt_upsampler_int8_convrot.safetensors vae/flux2-vae.safetensors
  fetch black-forest-labs/flux-3-image-klein-pup FLUX3-K-Comfy system_prompts.json
  ok "FLUX3-K Fast and Full (bf16), Qwen3-VL-4B text encoder, BFL's prompt upsampler (8-bit) and the decoder"
  if [ "$VERIFY" = 0 ] && bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see them: bash spark/comfy.sh stop (the next render starts it)"; fi
fi

if [ "$MOSS" = 1 ]; then
  say "MOSS-Image for ComfyUI: the SFT checkpoint, Qwen3.5 9B text encoder, FLUX.2 small decoder (30 GB) -> $MODELS"
  [ "$VERIFY" = 1 ] || echo "  MOSS-Image (OpenMOSS) is under the Apache License 2.0: https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT"
  fetch Comfy-Org/MOSS-Image MOSS-Image-Comfy diffusion_models/moss_image_sft_bf16.safetensors \
    text_encoders/qwen3.5_9b_bf16.safetensors vae/flux2-vae_small.safetensors
  [ -f "$PREFIX/ComfyUI/comfy_extras/nodes_moss_image.py" ] || die "this ComfyUI has no MOSS-Image nodes: run without --verify to move it to ${COMFY_COMMIT:0:8}"
  ok "MOSS-Image: a picture model (IMAGE_MODEL=moss-image)"
  if [ "$VERIFY" = 0 ] && bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see it: bash spark/comfy.sh stop (the next render starts it)"; fi
fi

if [ "$MUSIC" = 1 ]; then
  say "MiniMax Music 3 for ComfyUI: DiT (fp16), text encoder (pruned, bf16), decoder (22 GB) -> $MODELS"
  [ "$VERIFY" = 1 ] || { echo "  MiniMax Music 3 is under the MiniMax-Music3 Community License: commercial use is allowed; a commercial"
    echo "  product must show \"MiniMax-Music3\" in its interface; above US\$20M yearly revenue you need MiniMax's written"
    echo "  authorisation; a hosted service must keep misuse safeguards. Read it: https://huggingface.co/Comfy-Org/MiniMax-Music-3"; }
  fetch Comfy-Org/MiniMax-Music-3 MiniMax-Music3-Comfy diffusion_models/minimax_music3_dit_fp16.safetensors \
    text_encoders/minimax_music3_text_encoder_pruned_bf16.safetensors vae/minimax_music3_dav.safetensors
  [ -f "$PREFIX/ComfyUI/comfy_extras/nodes_minimax_music.py" ] || die "this ComfyUI has no MiniMax Music 3 nodes: run without --verify to move it to ${COMFY_COMMIT:0:8}"
  ok "MiniMax Music 3: the Music tab (MiniMax-Music3)"
  if [ "$VERIFY" = 0 ] && bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see them: bash spark/comfy.sh stop (the next render starts it)"; fi
  # TensorFold's CUDA engine for it (2.3x faster than ComfyUI's nodes, 18 GB instead of 29): the Music tab uses it
  # where it is built, ComfyUI's nodes where it is not (MUSIC3_BACKEND=cuda|comfy chooses)
  say "TensorFold's MiniMax Music 3 engine for CUDA -> $PREFIX/music3"
  if [ "$VERIFY" = 1 ]; then
    [ -x "$PREFIX/music3/bin/music3-generate" ] && ok "music3-generate at $(cut -c1-8 "$PREFIX/music3/commit" 2>/dev/null)" \
      || echo "  not built: songs run on ComfyUI's nodes (slower). bash spark/build-music3.sh builds it"
  elif PREFIX="$PREFIX" bash "$HERE/spark/build-music3.sh"; then
    ok "music3-generate (int8 weights made on the GPU as it loads; MUSIC3_WEIGHTS=bf16 for the checkpoint's own)"
  else
    echo "  the engine did not build: songs will run on ComfyUI's nodes (slower). Fix and rerun spark/build-music3.sh"
  fi
fi

if [ "$BOLT" = 1 ]; then  # NVIDIA Nemotron 3.5 Bolt, the brains' Studio-managed engine: weights and engine (scripts/setup-bolt.sh)
  if [ "$UPGRADE" = 1 ]; then PREFIX="$PREFIX" SPARK_MODELS="$MODELS" bash "$HERE/spark/build-bolt.sh" || echo "  ! the Bolt engine was not updated: bash spark/build-bolt.sh"
  else PREFIX="$PREFIX" SPARK_MODELS="$MODELS" bash "$HERE/scripts/setup-bolt.sh" $([ "$VERIFY" = 1 ] && echo --verify); fi
fi

if [ "$SOUND" = 1 ]; then
  # Sound effects and foley (app/sound.py, spark_generate.py sfx / foley): every render goes through the studio's
  # ComfyUI server and its memory guard, never as standalone model code; a kind renders only once memory_guard.py
  # has its measured estimate (sound_check).
  say "Sound effects and foley for ComfyUI: Stable Audio Open 1.0, MMAudio large_44k_v2 and its nodes (11 GB) -> $MODELS/Sound-Comfy"
  [ "$VERIFY" = 1 ] || { echo "  Stable Audio Open 1.0 is under the Stability AI Community License: free under US\$1M yearly revenue, a larger"
    echo "  business needs Stability's licence. Its Hugging Face repo is gated: accept the licence on"
    echo "  https://huggingface.co/stabilityai/stable-audio-open-1.0 with your own account and sign in (the studio's Install"
    echo "  guide, or hf auth login). MMAudio's weights are CC-BY-NC 4.0: non-commercial use only."; }
  SND="$MODELS/Sound-Comfy"
  # kijai's ComfyUI-MMAudio (MIT) at a pinned commit. Its requirements may only ADD packages to the Studio's environment:
  # every package already installed (PyTorch, numpy, ComfyUI's own and the Studio's) is held at its installed version,
  # so the install never moves one of them, and a later --upgrade finds nothing to do. A requirement that would need a
  # different version stops the setup with uv's reason instead. (Its pyproject's own list is not installed: it pins
  # numpy and much else.)
  MMA_NODES="$PREFIX/ComfyUI/custom_nodes/ComfyUI-MMAudio" MMA_COMMIT=8eaeb72edc3aaf2059b57f2d96a1f6f689f19ae2
  if [ "$(git -C "$MMA_NODES" rev-parse HEAD 2>/dev/null)" != "$MMA_COMMIT" ]; then
    [ "$VERIFY" = 0 ] || die "ComfyUI-MMAudio is missing or not at ${MMA_COMMIT:0:8}: run with --sound, without --verify"
    [ -d "$MMA_NODES/.git" ] || git clone -q https://github.com/kijai/ComfyUI-MMAudio "$MMA_NODES"
    git -C "$MMA_NODES" fetch -q origin "$MMA_COMMIT" 2>/dev/null || git -C "$MMA_NODES" fetch -q origin
    git -C "$MMA_NODES" checkout -q "$MMA_COMMIT"
  fi
  if [ "$VERIFY" = 0 ]; then
    HOLD="$(mktemp)"
    "$PY" -c "import importlib.metadata as m; print('\\n'.join(sorted({f\"{d.metadata['Name']}=={d.version}\" for d in m.distributions() if d.metadata['Name']})))" > "$HOLD"
    grep -qiE '^torch==' "$HOLD" || die "could not read the environment's packages to hold them"
    pipi -r "$MMA_NODES/requirements.txt" --constraint "$HOLD" || { rm -f "$HOLD"; die "ComfyUI-MMAudio needs a package at a version other than the one installed: not changed"; }
    rm -f "$HOLD"
  fi
  "$PY" -c "import open_clip, torchdiffeq, timm, librosa, accelerate, omegaconf" || die "ComfyUI-MMAudio's requirements are missing from $PREFIX/venv"
  ok "ComfyUI-MMAudio @ ${MMA_COMMIT:0:8} (kijai) and the Studio's sampler node (spark/studio_mmaudio, put beside it by spark/comfy.sh)"
  fetch Kijai/MMAudio_safetensors Sound-Comfy/mmaudio mmaudio_large_44k_v2_fp16.safetensors mmaudio_vae_44k_fp16.safetensors \
    mmaudio_synchformer_fp16.safetensors apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors
  # the vocoder the 44 kHz decoder loads by folder name (kijai's loader would download it at the first foley otherwise)
  fetch nvidia/bigvgan_v2_44khz_128band_512x Sound-Comfy/mmaudio/nvidia/bigvgan_v2_44khz_128band_512x bigvgan_generator.pt config.json
  ok "MMAudio large_44k_v2 (fp16 safetensors), its VAE, Synchformer, Apple's DFN5B CLIP and NVIDIA's BigVGAN v2: foley"
  # Stable Audio Open: the checkpoint (DiT and VAE) and its T5-base encoder, from the gated repo with the person's own
  # token (app/hf_gate.py keeps it where hf reads it), linked under the names ComfyUI's loaders list
  SAO_REPO=stabilityai/stable-audio-open-1.0 SAO="$SND/stable-audio-open-1.0"
  if [ ! -s "$SAO/model.safetensors" ] || [ ! -s "$SAO/text_encoder/model.safetensors" ]; then
    if [ "$VERIFY" = 1 ]; then echo "  ! Stable Audio Open 1.0 is not downloaded: sound effects wait for it"
    else
      ACCESS="$("$PY" -c "import sys; sys.path.insert(0, sys.argv[1]); import hf_gate as g; print(g.access(sys.argv[2], g.token()))" \
        "$HERE/app" "https://huggingface.co/$SAO_REPO/resolve/main/model_index.json" 2>/dev/null || echo unknown)"
      case "$ACCESS" in
        granted|unknown)
          "$PREFIX/venv/bin/hf" download "$SAO_REPO" model.safetensors text_encoder/model.safetensors LICENSE.md --local-dir "$SAO" >/dev/null \
            || echo "  ! Stable Audio Open 1.0 did not download: sound effects wait for it (run with --sound again)";;
        sign_in) echo "  ! Stable Audio Open 1.0 is gated: sign in to Hugging Face (the studio's Install guide, or hf auth login), accept the licence on https://huggingface.co/$SAO_REPO, then run with --sound again";;
        accept)  echo "  ! Stable Audio Open 1.0 is gated: accept its licence on https://huggingface.co/$SAO_REPO with your account, then run with --sound again";;
        pending) echo "  ! Stable Audio Open 1.0: your access request is waiting for the author's approval; run with --sound again once it is granted";;
      esac
    fi
  fi
  if [ -s "$SAO/model.safetensors" ] && [ -s "$SAO/text_encoder/model.safetensors" ]; then
    mkdir -p "$SND/checkpoints" "$SND/text_encoders"
    ln -sfn ../stable-audio-open-1.0/model.safetensors "$SND/checkpoints/stable_audio_open_1.0.safetensors"
    ln -sfn ../stable-audio-open-1.0/text_encoder/model.safetensors "$SND/text_encoders/stable_audio_open_t5_base.safetensors"
    ok "Stable Audio Open 1.0: the checkpoint and its T5-base encoder: sound effects"
  fi
  grep -q "Sound-Comfy/mmaudio" "$PREFIX/ComfyUI/extra_model_paths.yaml" 2>/dev/null || die "extra_model_paths.yaml does not list Sound-Comfy: run with --sound, without --verify"
  "$PY" "$HERE/spark/memory_guard.py" sound 2>/dev/null | sed 's/^/  /' || true
  if [ "$VERIFY" = 0 ] && bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see them: bash spark/comfy.sh stop (the next render starts it)"; fi
fi

if [ "$H3_BASE" = 1 ] && [ "$IMAGE_ONLY" = 0 ] && [ "$VERIFY" = 0 ]; then
  say "MiniMax H3's own first-and-last-frame transformer for ComfyUI (40 GB) -> $MODELS"
  fetch Comfy-Org/MiniMax-H3 MiniMax-H3-Comfy diffusion_models/minimax_h3_fl2va_pruned_bf16.safetensors
  ok "MiniMax H3 FL2VA transformer (bf16): SPARK_BASE=h3, and keyframe LoRAs from the hub"
  if bash "$HERE/spark/comfy.sh" status | grep -q running; then echo "  restart ComfyUI to see it: bash spark/comfy.sh stop (the next render starts it)"; fi
fi

# The Library's three actions (Turnaround sheet, Upscale, Remove a person) run their authors' own ComfyUI nodes on the
# Ref2VA model: wherever Ref2VA is installed, their node packs and two small models are too (spark/setup-workflows.sh).
if [ "$IMAGE_ONLY" = 0 ] && [ "$WORKFLOWS" = 1 ] && [ -s "$MODELS/MiniMax-H3-Comfy/diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors" ]; then
  say "Library actions: three node packs at pinned commits, SAM 3.1 and the H3 latent upscaler (2.5 GB) -> $PREFIX/ComfyUI, $MODELS/Workflows-Comfy"
  PREFIX="$PREFIX" SPARK_MODELS="$MODELS" bash "$HERE/spark/setup-workflows.sh" $([ "$VERIFY" = 0 ] || echo --verify) $([ "$UPGRADE" = 0 ] || echo --no-models) \
    || echo "  ! the Library actions are not fully set up: the Library says which one is missing what"
fi

if [ "$IMAGE_ONLY" = 0 ]; then
  say "TensorFold's H3 engine for CUDA -> $PREFIX/tf-h3"
  if [ "$VERIFY" = 1 ]; then
    [ -s "$PREFIX/tf-h3/libtf_h3.so" ] && ok "libtf_h3.so" || echo "  not built: clips render on ComfyUI's own blocks (slower)"
  elif PREFIX="$PREFIX" bash "$HERE/spark/build-engine.sh"; then
    ok "libtf_h3.so (FastH3's blocks in int8 on the tensor cores)"
  else
    echo "  the engine did not build: clips will render on ComfyUI's own blocks (slower). Fix and rerun spark/build-engine.sh"
  fi
fi

if [ "$MOTION" = 1 ] || [ -x "$PREFIX/motion-venv/bin/python" ]; then
  # Motion Previs (a Python port of Sam Wasserman's Motion Previs Studio analysis): its packages go into an environment
  # of their own, so nothing here can change the Studio's. A layer that cannot be installed on this machine is left out.
  say "Motion Previs: MediaPipe, OpenCV, ONNX Runtime and the pose and depth models (0.5 GB) -> $PREFIX/motion-venv"
  PREFIX="$PREFIX" bash "$HERE/scripts/setup-motion.sh" $([ "$VERIFY" = 1 ] && echo --verify) | sed 's/^/  /' \
    || echo "  ! Motion Previs is not fully set up: its tab says which layers are not available on this machine"
fi

if [ "$VOICE" = 1 ]; then
  # Fish Audio Drama 3 for the Voice tab (scripts/setup-voice.sh): TensorFold's CUDA engine (spark/build-drama3.sh),
  # Fish's checkpoint (linked when a copy is on this machine, else downloaded) and its codec converted here once, then a
  # test line. --verify checks only; --upgrade moves the engine to this version's commit and fetches no weights.
  say "Fish Audio Drama 3 for the Voice tab: TensorFold's CUDA engine -> $PREFIX/drama3, Fish's checkpoint (39 GB), the codec"
  if [ "$VERIFY" = 1 ]; then
    PREFIX="$PREFIX" bash "$HERE/scripts/setup-voice.sh" --verify | sed 's/^/  /' || echo "  ! the Voice tab is not ready: bash oneshot-setup-spark.sh --voice"
  elif [ "$UPGRADE" = 1 ]; then
    PREFIX="$PREFIX" bash "$HERE/scripts/setup-voice.sh" --upgrade | sed 's/^/  /' || echo "  ! the voice engine was not updated: bash spark/build-drama3.sh"
  else
    PREFIX="$PREFIX" bash "$HERE/scripts/setup-voice.sh" $([ "$RENDER" = 1 ] || echo --no-test) | sed 's/^/  /' \
      || echo "  ! the Voice tab is not fully set up (above): bash oneshot-setup-spark.sh --voice tries again"
  fi
fi

[ "$VERIFY" = 1 ] || cp "$HERE/VERSION" "$PREFIX/VERSION"
if [ "$UPGRADE" = 1 ]; then
  say "Upgraded: $INSTALLED -> $(cat "$PREFIX/VERSION"). Checking the install (restart the app to serve the new pages: scripts/app.sh)"
  exec bash "$HERE/oneshot-setup-spark.sh" --verify $([ "$IMAGE_ONLY" = 0 ] || echo --image-only) $([ "$REF2VA" = 0 ] || echo --ref2va) $([ "$FLUX3" = 0 ] || echo --flux3) $([ "$MOSS" = 0 ] || echo --moss) $([ "$MUSIC" = 0 ] || echo --music) $([ "$SOUND" = 0 ] || echo --sound) $([ "$BOLT" = 0 ] || echo --bolt) $([ "$VOICE" = 0 ] || echo --voice)
fi
# The essential LoRA bundle: every preset of the LoRA hub, fetched once from its author's page into one folder laid out
# by the hub's groups, then installed (oneshot-essential-bundle.sh). --loras=GROUPS takes some groups, --lora-bundle=PATH
# (or LORA_BUNDLE) takes the files from another copy of a bundle with no other network, --no-loras skips it.
if [ "$LORAS" != none ] && [ "$IMAGE_ONLY" = 0 ] && [ "$VERIFY" != 1 ]; then
  echo; echo "== Essential LoRA bundle${LORA_BUNDLE:+ from $LORA_BUNDLE}"
  PREFIX="$PREFIX" H3_MODEL_DIR="${H3_MODEL_DIR:-$HOME/h3-models/MiniMax-H3}" bash "$HERE/oneshot-essential-bundle.sh" \
    $([ -z "$LORAS" ] || [ "$LORAS" = all ] || echo "--categories=$LORAS") $([ -z "${LORA_BUNDLE:-}" ] || echo "--source=$LORA_BUNDLE") 2>&1 | sed 's/^/  /' \
    || echo "  ! some LoRAs could not be fetched (listed above): the rest are installed, and bash oneshot-essential-bundle.sh tries again"
fi
if [ "$RENDER" = 1 ]; then
  say "Test render"
  mkdir -p "$HERE/outputs"
  PREFIX="$PREFIX" bash "$HERE/scripts/image.sh" "A lighthouse on a rocky coast at sunrise, waves breaking, warm light." "$HERE/outputs/test.png"
  ok "outputs/test.png"
  if [ "$IMAGE_ONLY" = 0 ]; then
    PREFIX="$PREFIX" bash "$HERE/scripts/video.sh" 'A woman in a yellow raincoat stands on a pier, looks at the camera and says in a clear voice: "The tide is coming in." Gulls and waves.' "$HERE/outputs/test.mp4"
    ok "outputs/test.mp4 (864x480, 124 frames, 8 passes)"
  fi
fi
echo "DONE. Web app: bash $HERE/scripts/app.sh   (http://127.0.0.1:7870)"
echo "      Image:   bash $HERE/scripts/image.sh \"a prompt\" out.png"
echo "      Video:   bash $HERE/scripts/video.sh \"a prompt\" out.mp4"
echo "      Free the models' memory: bash $HERE/spark/comfy.sh stop"
