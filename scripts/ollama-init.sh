#!/bin/sh
set -euo pipefail

: "${OLLAMA_HOST:=http://ollama:11434}"
export OLLAMA_HOST

printf '>> Waiting for Ollama API at %s...\n' "$OLLAMA_HOST"
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
