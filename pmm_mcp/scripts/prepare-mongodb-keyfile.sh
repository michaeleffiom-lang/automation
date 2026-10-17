#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
keyfile="$project_dir/.pmm-cache/mongodb-keyfile"
container_cli="${CONTAINER_CLI:-}"
if [[ -z "$container_cli" ]]; then
  if command -v docker >/dev/null 2>&1; then
    container_cli=docker
  elif command -v podman >/dev/null 2>&1; then
    container_cli=podman
  else
    echo "Neither docker nor podman is available." >&2
    exit 1
  fi
fi

if [[ ! -s "$keyfile" ]]; then
  mkdir -p "$(dirname -- "$keyfile")"
  umask 077
  head -c 756 /dev/urandom | base64 -w 0 > "$keyfile"
  echo "Created local MongoDB replica-set keyfile: $keyfile"
else
  echo "Using existing local MongoDB replica-set keyfile: $keyfile"
fi

chmod 600 "$keyfile" 2>/dev/null || true

case "$container_cli" in
  podman)
    podman unshare chown 999:999 "$keyfile"
    ;;
  docker)
    docker run --rm --user 0:0 \
      -v "$keyfile:/keyfile" \
      docker.io/library/mongo:7.0 \
      chown 999:999 /keyfile
    ;;
  *)
    echo "Unsupported CONTAINER_CLI: $container_cli (use docker or podman)." >&2
    exit 1
    ;;
esac
