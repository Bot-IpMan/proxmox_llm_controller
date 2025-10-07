#!/bin/sh
set -eu

# Enable pipefail only when supported by the running shell (e.g., bash, zsh).
# BusyBox /dash based shells do not recognise the option and would print
# warnings during container start-up, so gate the call behind a shell-specific
# variable check.
if [ -n "${BASH_VERSION:-}" ] || [ -n "${ZSH_VERSION:-}" ] || [ -n "${KSH_VERSION:-}" ]; then
  set -o pipefail
fi

DEFAULT_OLLAMA_HOST="ollama:11434"
if [ -z "${OLLAMA_HOST:-}" ]; then
  export OLLAMA_HOST="$DEFAULT_OLLAMA_HOST"
  printf '>> OLLAMA_HOST not set, defaulting to %s.\n' "$OLLAMA_HOST"
fi

printf '>> Waiting for Ollama API at %s...\n' "$OLLAMA_HOST"
until ollama list >/dev/null 2>&1; do
  sleep 1
done

models_default="qwen2.5-coder:7b nomic-embed-text"
models_raw="${OLLAMA_AUTO_PULL_MODELS:-$models_default}"

# Normalise the list of requested models to support comma/newline separated
# values, ignore blank/commented lines and remove duplicates while preserving
# order.  The implementation sticks to POSIX shell features so the script keeps
# working when executed by BusyBox / dash.
normalised_models=""
while IFS= read -r line; do
  # Drop inline comments (everything after '#') and trim leading/trailing
  # whitespace by relying on shell word splitting below.
  line=${line%%#*}
  # Treat commas as whitespace so values can be provided either as
  # "model-a,model-b" or "model-a model-b".
  line=$(printf '%s' "$line" | tr ',' ' ')
  # ``set --`` performs the trimming and splits the normalised whitespace into
  # individual tokens.
  set -- $line
  for token in "$@"; do
    [ -z "$token" ] && continue
    case " $normalised_models " in
      *" $token "*)
        # Skip duplicates while keeping the first occurrence.
        continue
        ;;
    esac
    if [ -z "$normalised_models" ]; then
      normalised_models=$token
    else
      normalised_models="$normalised_models $token"
    fi
  done
done <<EOF
$models_raw
EOF

if [ -z "$normalised_models" ]; then
  printf '>> OLLAMA_AUTO_PULL_MODELS is empty, skipping downloads.\n'
  exit 0
fi

for model in $normalised_models; do
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
