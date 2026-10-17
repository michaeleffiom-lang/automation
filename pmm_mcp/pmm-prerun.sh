#!/bin/sh
set -eu

service_name="${PMM_MONGODB_SERVICE_NAME:-mflix-rs}"
service_address="${PMM_MONGODB_SERVICE_ADDRESS:-mongodb7-rs1:27017}"
replication_set="${PMM_MONGODB_REPLICATION_SET:-rs0}"
cluster="${PMM_MONGODB_CLUSTER:-mflix-lab}"

if ! pmm-admin list | awk -v service="$service_name" '$1 == "MongoDB" && $2 == service { found = 1 } END { exit !found }'; then
  until pmm-admin add mongodb "$service_name" "$service_address" \
    --username="${MONGO_ROOT_USERNAME}" \
    --password="${MONGO_ROOT_PASSWORD}" \
    --authentication-database=admin \
    --replication-set="$replication_set" \
    --cluster="$cluster" \
    --enable-all-collectors; do
    sleep 5
  done
fi
