#!/bin/bash
# DGX Spark entry. The install is oneshot-setup-cuda.sh, which oneshot-setup-rtx-6000.sh also runs.
#   bash oneshot-setup-spark.sh --all
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$HERE/oneshot-setup-cuda.sh" "$@"
