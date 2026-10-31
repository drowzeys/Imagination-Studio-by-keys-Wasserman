# Pin this process to one NVIDIA GPU. Source this, then call studio_pin_gpu.
# STUDIO_GPU is an nvidia-smi index. It is required when more than one GPU is not driving a display:
# indexes differ by machine, and two equal cards are not a choice this script makes.
# One compute card beside a display GPU is used on its own, pinned. One GPU (a DGX Spark) stays on the default device.
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
  if [ -n "${STUDIO_LOCAL_GPU_UUID:-}" ]; then
    # Managed workers pin CUDA by physical identity. The guard still uses the
    # numeric nvidia-smi index, so refuse a stale guard rather than another GPU.
    if [[ ! "$STUDIO_LOCAL_GPU_UUID" =~ ^GPU-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
      echo "The local GPU identity is invalid; restart its Studio." >&2
      return 1
    fi
    local current
    current=$(timeout 5 nvidia-smi -i "$STUDIO_LOCAL_GPU_UUID" --query-gpu=index --format=csv,noheader,nounits 2>/dev/null) || {
      echo "The selected local GPU is unavailable; reconnect it and restart its Studio." >&2
      return 1
    }
    if [ "$current" != "$STUDIO_GPU" ]; then
      echo "The local GPU index changed; restart its Studio so the memory guard follows the same card." >&2
      return 1
    fi
    export CUDA_VISIBLE_DEVICES="$STUDIO_LOCAL_GPU_UUID"
  elif studio_paired_gpus >/dev/null; then
    # Paired cards (STUDIO_GPUS, spark/gpus.py): one Studio renders on all of them, the head's card first, so CUDA's
    # device 0 is still STUDIO_GPU and the second card is device 1 (text encoders, decoders, rank 1 of a pass).
    export STUDIO_GPUS
    export CUDA_VISIBLE_DEVICES="$STUDIO_GPUS"
  else
    export CUDA_VISIBLE_DEVICES="$STUDIO_GPU"
  fi
}

studio_paired_gpus() {
  # STUDIO_GPUS: two or more nvidia-smi indexes, comma separated, the head's card (STUDIO_GPU) first. Prints the list
  # and succeeds when it is valid and STUDIO_GPU_SPLIT is not off; a managed local worker (a UUID pin) never pairs.
  local list="${STUDIO_GPUS:-}" first
  [ -n "$list" ] && [ -z "${STUDIO_LOCAL_GPU_UUID:-}" ] || return 1
  case "${STUDIO_GPU_SPLIT:-auto}" in 0|off|no|false) return 1;; esac
  [[ "$list" =~ ^[0-9]+(,[0-9]+)+$ ]] || { echo "STUDIO_GPUS must be two or more nvidia-smi indexes, e.g. 0,1" >&2; return 1; }
  first="${list%%,*}"
  if [ -n "${STUDIO_GPU:-}" ] && [ "$STUDIO_GPU" != "$first" ]; then
    echo "STUDIO_GPUS ($list) must start with STUDIO_GPU ($STUDIO_GPU), the head's own card" >&2
    return 1
  fi
  printf '%s\n' "$list"
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

studio_gpu_rows() {
  # every GPU nvidia-smi lists, display cards and a GB10 included, one per line
  nvidia-smi --query-gpu=index,memory.total,display_active --format=csv,noheader,nounits 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '
}

studio_pin_gpu() {
  if [ -z "${STUDIO_GPU:-}" ] && [ -n "${STUDIO_GPUS:-}" ] && [ -z "${STUDIO_LOCAL_GPU_UUID:-}" ]; then
    STUDIO_GPU="${STUDIO_GPUS%%,*}"  # paired cards: the head's is the first
  fi
  if [ -n "${STUDIO_GPU:-}" ]; then
    studio_export_gpu
    return $?
  fi
  case "${CUDA_VISIBLE_DEVICES:-}" in
    ""|*,*|*[!0-9]*) ;;
    *) if [ "${CUDA_DEVICE_ORDER:-}" = PCI_BUS_ID ]; then
         STUDIO_GPU="$CUDA_VISIBLE_DEVICES"; studio_export_gpu; return $?
       fi;;
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
  # Index 0 is already CUDA's default device, but nvidia-smi and the memory guard read every card listed: beside a
  # display card the only compute card is pinned wherever it is, so they read it alone. One GPU is left as it is.
  if [ -n "$pick" ] && { [ "$pick" != 0 ] || [ "$(studio_gpu_rows)" -gt 1 ]; }; then
    STUDIO_GPU="$pick"
    studio_export_gpu
  fi
}
