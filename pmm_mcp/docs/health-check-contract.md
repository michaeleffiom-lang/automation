# Contrato do health check padrão

O relatório padrão será retornado como JSON com esta forma:

```json
{
  "schema_version": "0.1.0",
  "report_type": "standard_health_check",
  "standard": true,
  "status": "not_configured",
  "rules_version": "draft",
  "generated_at": "2026-01-01T00:00:00+00:00",
  "client": {},
  "scope": {
    "service_name": "",
    "node_id": "",
    "cluster": "",
    "replication_set": ""
  },
  "environment": {
    "mongodb_services": [],
    "mongodb_service_count": 0
  },
  "checks": [],
  "summary": {
    "total": 0,
    "passed": 0,
    "warning": 0,
    "critical": 0
  },
  "next_step": "Definir as métricas e limites oficiais do health check."
}
```

Cada check futuro deverá conter pelo menos:

```json
{
  "check_id": "replication_lag",
  "name": "Replication lag",
  "category": "availability",
  "status": "pass|warning|critical|unknown",
  "metric": "metric_name",
  "observed": 0,
  "thresholds": {},
  "scope": {},
  "evidence": {}
}
```

As regras serão versionadas e iguais para todos os clientes. Consultas adicionais feitas pelo modo investigação não entram automaticamente em `checks`.
