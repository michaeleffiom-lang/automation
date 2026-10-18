# Recommendation guidance

Recommendations should identify evidence, customer impact to confirm, and a safe next investigation. Avoid prescriptive changes without workload context and an agreed objective.

- **CPU:** correlate sustained peaks with operation rate, query latency, and workload changes; check whether pressure is isolated to one member.
- **Filesystem:** review growth rate, retained data, logs, and backup use; estimate remaining capacity time before proposing expansion or cleanup.
- **Connections:** compare peaks with application pool settings and connection errors; distinguish idle connections from saturation.
- **WiredTiger:** review dirty-page behavior with eviction, queues, tickets, and latency; cache occupancy alone does not establish pressure.
- **Replication:** inspect member lag and role coverage together; confirm whether delayed members are intentional and whether the observation window includes a transient event.
- **Telemetry gaps:** name the missing metric and member, explain which conclusion cannot be drawn, and suggest checking PMM collection or service labels.

Do not present a recommendation as a health-check result. State which evidence supports it and what should be validated before changing topology or capacity.
