# Pin this process to one NVIDIA GPU. Source this, then call studio_pin_gpu.
# STUDIO_GPU is an nvidia-smi index. It is required when more than one GPU is not driving a display:
# indexes differ by machine, and two equal cards are not a choice this script makes.
# One compute card behind a display GPU is used on its own. One GPU (a DGX Spark) stays on the default device.
# CUDA_DEVICE_ORDER=PCI_BUS_ID makes the index match CUDA. nvidia-smi ignores CUDA_VISIBLE_DEVICES,
# so readers pass -i "$STUDIO_GPU" (studio_smi does).

studio_smi() {
  if [ -n "${STUDIO_GPU:-}" ]; then
    nvidia-smi -i "$STUDIO_GPU" "$@"
  else
    nvidia-smi "$@"
  fi
}

studio_export_gpu() {
  case "${STUDIO_GPU:-}" in
    ''|*[!0-9]*) return 0;;
  esac
  export STUDIO_GPU
  export CUDA_DEVICE_ORDER=PCI_BUS_ID
  export CUDA_VISIBLE_DEVICES="$STUDIO_GPU"
}

studio_compute_indexes() {
  # nvidia-smi indexes whose memory is their own and which are not driving a display, one per line.
  nvidia-smi --query-gpu=index,memory.total,display_active --format=csv,noheader,nounits 2>/dev/null \
    | awk -F',' '
      function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
      {
        idx = trim($1); mem = trim($2); disp = tolower(trim($3))
        if (idx ~ /^[0-9]+$/ && mem ~ /^[0-9]+$/ && disp != "enabled") print idx
      }
    '
}

studio_pin_gpu() {
  if [ -n "${STUDIO_GPU:-}" ]; then
    studio_export_gpu
    return 0
  fi
  case "${CUDA_VISIBLE_DEVICES:-}" in
    ""|*,*|*[!0-9]*) ;;
    *) STUDIO_GPU="$CUDA_VISIBLE_DEVICES"; studio_export_gpu; return 0;;
  esac
  command -v nvidia-smi >/dev/null 2>&1 || return 0
  local indexes count pick
  indexes="$(studio_compute_indexes || true)"
  count=$(printf '%s\n' "$indexes" | sed '/^$/d' | wc -l | tr -d ' ')
  if [ "${count:-0}" -ge 2 ]; then
    echo "This machine has ${count} compute GPUs. Set STUDIO_GPU to the nvidia-smi index this process should use:" >&2
    nvidia-smi --query-gpu=index,name,memory.total,display_active --format=csv >&2 || true
    echo "A row with display_active Enabled is a monitor card. It is not used unless STUDIO_GPU names it." >&2
    echo "One studio uses one card. A second studio is another process with its own STUDIO_GPU, PORT, SPARK_COMFY_PORT, STUDIO_HOME and STUDIO_GPU_LOCK." >&2
    return 1
  fi
  pick=$(printf '%s\n' "$indexes" | sed '/^$/d' | head -1)
  # Index 0 is already the default device. Pin only when the only compute card is another one.
  if [ -n "$pick" ] && [ "$pick" != 0 ]; then
    STUDIO_GPU="$pick"
    studio_export_gpu
  fi
}
