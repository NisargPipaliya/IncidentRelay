---
title: IncidentRelay 2.3 API and Upgrade Migration
description: Breaking AlertGroup/Incident API split, database upgrade checks and rollback boundary for IncidentRelay 2.3.
---

# IncidentRelay 2.3 API and Upgrade Migration

IncidentRelay 2.3 introduces a deliberate breaking separation between technical
alert processing and operational incident response:

```text
Alert -> AlertGroup -> Incident
```

The bundled UI, OpenAPI and server move to the new contract together. External
API clients must be updated before the application is upgraded.

## Breaking endpoint changes

| Before 2.3 | 2.3 contract | Meaning |
|---|---|---|
| `/api/incidents` for technical grouped alerts | `/api/alert-groups` | Technical grouping, ACK/resolve, child alerts, notifications, escalation and technical comments |
| No independent first-class Incident API | `/api/incidents` | Operational Incident lifecycle, priority, service, assignee and AlertGroup links |

There is no compatibility alias for the old `/api/incidents` AlertGroup
contract. Do not assume an Incident ID and an AlertGroup ID refer to the same
record. The two tables have independent ID namespaces and may legitimately
contain the same numeric ID.

## Creation semantics

`POST /api/alert-groups` creates a technical AlertGroup and exactly one initial
manual child Alert atomically.

`POST /api/incidents` creates only a first-class operational Incident. It does
not create an Alert or AlertGroup implicitly.

To declare an Incident from an existing AlertGroup use the AlertGroup
create-Incident action/API or create the Incident and link the AlertGroup
explicitly.

## Before upgrading

1. Back up the database and configuration.
2. Stop all web, scheduler and worker processes so no old code writes while the
   schema/API contract changes.
3. Record the current version and migration status:

   ```bash
   python manage.py migration-status
   ```

4. Verify external API clients have migrated from old technical
   `/api/incidents` calls to `/api/alert-groups`.
5. Verify application/database credentials can create and drop tables. This is
   required for the migration and for rollback before new Incident writes.

## Apply the 2.3 migrations

Run the normal migration command once from a single process:

```bash
python manage.py migrate
```

The Incident core migration creates:

```text
incident
incident_event
incident_alert_group_link
```

It does not rename or rebuild `alert_group` and it does not automatically create
historical Incidents for existing AlertGroups. Existing technical AlertGroup and
child Alert history therefore remains in its original tables.

Re-run the status check after migration:

```bash
python manage.py migration-status
```

`20260917070000_incident_core` must be reported as applied.

## Post-upgrade reconciliation

Before reopening traffic, verify:

1. `/api/alert-groups` returns existing technical groups.
2. `/api/incidents` returns first-class Incidents only.
3. Existing AlertGroup child-alert counts, status, technical assignee and
   comments are unchanged.
4. Creating a new Incident does not create an AlertGroup.
5. Creating a manual AlertGroup creates one initial child Alert.
6. A test Incident can link and unlink an AlertGroup without changing the
   AlertGroup technical lifecycle.
7. Team permissions prevent users from linking AlertGroups outside their scope.

For PostgreSQL deployments, also run the PostgreSQL release test set so row-lock
behavior is exercised, including concurrent attempts to link one AlertGroup to
two open Incidents.

## Rollback boundary

The schema migration itself has a downgrade and can remove the three new
Incident core tables:

Use `python manage.py migration-status` first and roll back every applied
migration after and including `20260917070000_incident_core` in reverse order.
In the 2.3 migration set currently shipped by this branch,
`20260925070000_alert_text_fields` follows Incident core, so crossing the
Incident-core boundary requires:

```bash
python manage.py rollback --count 2
```

Do not copy that count blindly after additional migrations are added; derive it
from `migration-status` for the exact build being rolled back.

However, rollback is safe only **before first-class Incident data that must be
preserved has been written**. Downgrading the Incident core migration drops the
Incident, Incident event and Incident/AlertGroup link tables.

Therefore:

- keep the pre-upgrade database backup until reconciliation succeeds;
- if rollback is required after operators have created Incident data, restore
  from an appropriate backup or export that data first;
- never treat removal of the new tables as a data-preserving downgrade after
  the new model is in active use.

AlertGroup technical data is not stored in those three tables and is not removed
by the Incident core downgrade.

## Client migration checklist

Update external clients so they distinguish the resources explicitly:

```text
technical alert-group ID -> /api/alert-groups/{id}
operational incident ID  -> /api/incidents/{id}
```

Do not persist a generic `incident_id` and later use it against both endpoint
families. Store the resource type together with its ID.

## Release verification

Before declaring the upgrade complete, run the normal test suite against the
same database engines used in production. For PostgreSQL, run the dedicated
PostgreSQL tests explicitly because that directory is excluded from the default
pytest recursion:

```bash
pytest -q
pytest -q tests/postgresql
```

Also verify the generated OpenAPI documentation does not expose the legacy
AlertGroup-shaped `/api/incidents` contract.
