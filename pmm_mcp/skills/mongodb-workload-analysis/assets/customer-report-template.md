# MongoDB Workload Assessment — <Customer / Deployment>

**Reporting window (UTC):** <start UTC> – <end UTC><br>
**Deployment:** <cluster; environment; standalone/replica set/sharded><br>
**MongoDB / PSMDB version:** <known value or Not available><br>
**PMM version:** <known value or Not available><br>
**Data provenance:** PMM metrics; QAN <enabled / unavailable / unknown><br>
**Coverage:** <nodes, missing periods, resolution and important gaps>

## Executive summary
<2–5 concise evidence-based sentences on the requested review window, most material substantiated risks, and immediate decision/follow-up. No invented root cause or claims of downtime.>

## Workload overview
<Operation mix; volume/latency trend; meaningful sustained peaks with exact date/time, scope, units; brief normal/variable patterns. Reference E- IDs.>

## Key findings

### F-001 — <short specific title>
**Finding ID:** F-001<br>
**Evidence:** E-001, E-002<br>
**Scope / time:** <node/role, exact UTC time or range><br>
**Observation:** <measurements + units + aggregation and duration><br>
**Correlation:** <independent metric checked; overlap or lack of overlap><br>
**Interpretation:** <supported MongoDB mechanism and conditional explanation><br>
**Optimization layer:** <Application/query | Schema/index | Engine/storage | Replication/topology | Capacity | Investigation only><br>
**Alternative explanation:** <what else could explain this; tested or not tested><br>
**Status:** <Confirmed | Supported hypothesis | Unverified hypothesis | Insufficient data><br>
**Confidence:** <High | Medium | Low><br>
**Customer impact:** <Observed | Possible | Not assessed; provide evidence if Observed><br>
**Recommendation:** <specific next action and evidence-supported expected benefit; state trade-offs><br>
**Validation / completion:** <what would prove or falsify claim, how to compare before/after, and safe next test>

<Repeat findings as warranted; omit fabricated sections/findings.>

## Recommendations and follow-up
Prioritize changes that address proven or supported high-contribution application/query work *when that evidence exists*. Explain when immediate engine/HA or capacity risks justify acting first. No customer-facing named-author attribution is required.

| Priority | Related finding | Suggested action | Evidence needed / validation | Change control |
|---|---|---|---|---|
| <Critical/High/Medium/Low> | F-001 | <concrete read-only investigation or justified proposal> | <measurable check> | <No change / Customer approval required> |

## Monitoring gaps and limitations
- <Metric gaps, absent QAN, nodes not monitored, resolution/retention limits, source scope, unknown configurations.>
- <Explicitly state what cannot be concluded because of these gaps.>

## Evidence register
| Evidence ID | PMM panel / actual metric source | Node / role | UTC time range | Value and unit / method | Coverage |
|---|---|---|---|---|---|
| E-001 | <actual PMM panel URL or precise source/query> | <node + role> | <UTC range> | <observation, unit, rate/aggregation and resolution> | <coverage> |
| E-002 | <actual independent series> | <node + role> | <UTC range> | <value + unit> | <coverage> |

## Technical references
- <MongoDB documentation URLs actually used to interpret findings>
- <Percona PMM documentation URLs actually used>

## Report scope and limitations
This review is based on monitoring evidence available for the stated period. It assesses observed MongoDB workload patterns and potential investigation areas. It is **not** a confirmed root-cause analysis. Hypotheses and recommended changes require validation with query plans, logs, application context, and/or controlled testing as appropriate. Not all operational or application-impact data may be represented in PMM.
