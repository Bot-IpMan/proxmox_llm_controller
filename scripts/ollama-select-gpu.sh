#!/bin/sh
set -eu

# Prefer a specific NVIDIA GPU for Ollama when multiple GPUs are available.
TARGET_NAME="${OLLAMA_PREFERRED_GPU_NAME:-}"

if command -v nvidia-smi >/dev/null 2>&1; then
  if [ -z "${CUDA_VISIBLE_DEVICES:-}" ] && [ -z "${NVIDIA_VISIBLE_DEVICES:-}" ]; then
    gpu_query="$(nvidia-smi --query-gpu=index,name --format=csv,noheader)"
    gpu_line=""
    if [ -n "$TARGET_NAME" ]; then
      gpu_line=$(printf '%s\n' "$gpu_query" | grep -m1 "$TARGET_NAME" || true)
    fi
    if [ -z "$gpu_line" ]; then
      gpu_line=$(printf '%s\n' "$gpu_query" | head -n1)
    fi
    gpu_index=$(printf '%s' "$gpu_line" | cut -d',' -f1 | tr -d ' ')
    if [ -n "$gpu_index" ]; then
      export CUDA_VISIBLE_DEVICES="$gpu_index"
      export NVIDIA_VISIBLE_DEVICES="$gpu_index"
      export OLLAMA_VISIBLE_DEVICES="$gpu_index"
    fi
  fi
fi

exec /bin/ollama "$@"
