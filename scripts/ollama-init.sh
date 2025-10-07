#!/bin/sh
set -eu

# Enable pipefail where supported (e.g., bash); ignore errors in POSIX sh.
if (set -o pipefail >/dev/null 2>&1); then
  set -o pipefail
fi

printf '>> Waiting for Ollama API...\n'
until ollama list >/dev/null 2>&1; do
  sleep 1
done

models_default="qwen2.5-coder:7b nomic-embed-text"
models="${OLLAMA_AUTO_PULL_MODELS:-$models_default}"

if [ -z "${models}" ]; then
  printf '>> OLLAMA_AUTO_PULL_MODELS is empty, skipping downloads.\n'
  exit 0
fi

for model in $models; do
  if ollama show "$model" >/dev/null 2>&1; then
    printf '>> Model %s already present, skipping.\n' "$model"
    continue
  fi
  printf '>> Pull %s\n' "$model"
  if ! ollama pull "$model"; then
    printf '!! Failed to pull %s\n' "$model" >&2
    exit 1
  fi
done

printf '>> Done.\n'
