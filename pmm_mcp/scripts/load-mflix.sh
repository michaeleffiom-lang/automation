#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
container_cli="${CONTAINER_CLI:-docker}"
env_file="${PMM_ENV_FILE:-$project_dir/.env}"
container_name="${MONGO_CONTAINER_NAME:-mongodb7-rs1}"
archive_path="$(mktemp "${TMPDIR:-/tmp}/sampledata.XXXXXX.archive")"
trap 'rm -f "$archive_path"' EXIT

if [[ ! -f "$env_file" ]]; then
  echo "Environment file not found: $env_file" >&2
  exit 1
fi

set -a
. "$env_file"
set +a

"$container_cli" inspect "$container_name" >/dev/null
curl --fail --location https://atlas-education.s3.amazonaws.com/sampledata.archive --output "$archive_path"
"$container_cli" cp "$archive_path" "$container_name:/tmp/sampledata.archive"
"$container_cli" exec "$container_name" mongorestore \
  --host=mongodb7-rs1:27017 \
  --archive=/tmp/sampledata.archive \
  --nsInclude='sample_mflix.*' \
  --username="$MONGO_ROOT_USERNAME" \
  --password="$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase=admin
