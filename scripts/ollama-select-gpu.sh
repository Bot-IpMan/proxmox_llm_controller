#!/bin/sh
set -eu

# Prefer a specific NVIDIA GPU for Ollama when multiple GPUs are available.
#
# Historically this script expected ``OLLAMA_PREFERRED_GPU_NAME`` to match the
# *exact* marketing name of the adapter (and docker-compose.gpu.yml ships a
# placeholder ``NVIDIA GeForce GTX 1050 Ti`` value).  On systems where the
# actual GPU name differs, the lookup would fail and no value would be exported
# to ``CUDA_VISIBLE_DEVICES``.  Instead of forcing contributors to keep the
# placeholder in sync with their local hardware, accept either a literal GPU
# index or a case-insensitive substring of the device name and fall back to the
# first adapter that ``nvidia-smi`` reports.
TARGET_NAME="${OLLAMA_PREFERRED_GPU_NAME:-}"

if command -v nvidia-smi >/dev/null 2>&1; then
  if [ -z "${CUDA_VISIBLE_DEVICES:-}" ] && [ -z "${NVIDIA_VISIBLE_DEVICES:-}" ]; then
    gpu_query="$(nvidia-smi --query-gpu=index,name --format=csv,noheader)"
    gpu_line=""
    if [ -n "$TARGET_NAME" ]; then
      if printf '%s' "$TARGET_NAME" | grep -Eq '^[0-9]+$'; then
        gpu_line=$(printf '%s\n' "$gpu_query" | grep -m1 "^${TARGET_NAME}[[:space:]]*," || true)
      else
        gpu_line=$(printf '%s\n' "$gpu_query" | grep -m1 -Fi "$TARGET_NAME" || true)
      fi
    fi
    if [ -z "$gpu_line" ]; then
      gpu_line=$(printf '%s\n' "$gpu_query" | head -n1)
    fi
    gpu_index=$(printf '%s' "$gpu_line" | cut -d',' -f1 | tr -d '[:space:]')
    if [ -n "$gpu_index" ]; then
      export CUDA_VISIBLE_DEVICES="$gpu_index"
      export NVIDIA_VISIBLE_DEVICES="$gpu_index"
      export OLLAMA_VISIBLE_DEVICES="$gpu_index"
    fi
  fi
fi

exec /bin/ollama "$@"
