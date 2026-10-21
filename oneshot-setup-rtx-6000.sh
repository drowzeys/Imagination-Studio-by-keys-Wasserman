#!/bin/bash
# Imagination Studio on an NVIDIA RTX Pro 6000 Blackwell.
# This chooses the card, then runs oneshot-setup-cuda.sh, the same install
# oneshot-setup-spark.sh runs. Neither entry contains the install.
#   bash oneshot-setup-rtx-6000.sh --all                 # one compute card: that card
#   STUDIO_GPU=1 bash oneshot-setup-rtx-6000.sh --all    # that nvidia-smi index
# With two or more compute GPUs, STUDIO_GPU is required. Indexes are this machine's.
# The same variable, set again, chooses the card when the app starts (scripts/app.sh).
# A second card is another process after this install, with its own STUDIO_GPU, PORT,
# SPARK_COMFY_PORT, STUDIO_HOME and STUDIO_GPU_LOCK. One install is shared.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/spark/gpu.sh"
studio_pin_gpu
# --all fetches FLUX3-K before the later steps, and a refused licence stops the install there.
# Check it now, before anything is downloaded.
need_flux=0
for arg in "$@"; do
  case "$arg" in --all|--flux3) need_flux=1;; esac
done
if [ "$need_flux" = 1 ]; then
  token="$(hf auth token 2>/dev/null || true)"
  flux_url="https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy/resolve/main/diffusion_models/flux3-image-klein-bf16.safetensors"
  if [ -n "$token" ]; then
    code="$(curl -sI -o /dev/null -w '%{http_code}' --max-redirs 0 -H "Authorization: Bearer $token" "$flux_url" || true)"
  else
    code="$(curl -sI -o /dev/null -w '%{http_code}' --max-redirs 0 "$flux_url" || true)"
  fi
  case "$code" in
    200|302|307) ;;
    *) echo "FLUX3-K is gated and this Hugging Face account cannot download it (HTTP ${code:-none})." >&2
       echo "Accept the licence, then rerun: https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy" >&2
       exit 1;;
  esac
fi
# The CUDA installer reads nvidia-smi's first row. This directory's nvidia-smi
# answers those queries for STUDIO_GPU.
export PATH="$HERE/spark/smi-pin:$PATH"
exec bash "$HERE/oneshot-setup-cuda.sh" "$@"
