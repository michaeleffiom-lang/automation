#!/bin/sh
set -eu

primary_host="${MONGO_RS_PRIMARY_HOST:-mongodb7-rs1}"

mongosh_auth() {
  mongosh --host "$primary_host" \
    --username "$MONGO_ROOT_USERNAME" \
    --password "$MONGO_ROOT_PASSWORD" \
    --authenticationDatabase admin "$@"
}

until mongosh_auth --quiet --eval 'db.adminCommand({ ping: 1 }).ok' | grep -q 1; do
  sleep 2
done

if ! mongosh_auth --quiet --eval 'rs.conf()._id' >/dev/null 2>&1; then
  mongosh_auth --quiet --eval '
    rs.initiate({
      _id: "rs0",
      members: [
        { _id: 0, host: "mongodb7-rs1:27017" },
        { _id: 1, host: "mongodb7-rs2:27017" },
        { _id: 2, host: "mongodb7-rs3:27017" }
      ]
    })
  '
fi

until mongosh_auth --quiet --eval '
  const status = rs.status();
  print(status.members.length === 3 && status.members.every(member =>
    member.health === 1 && (member.stateStr === "PRIMARY" || member.stateStr === "SECONDARY")
  ));
' | grep -q true; do
  sleep 2
done
