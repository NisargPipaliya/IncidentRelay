---
title: IncidentRelay 2.3 Release Notes
description: Breaking Incident Management Core release notes and operator checklist.
---

# IncidentRelay 2.3 Release Notes

IncidentRelay 2.3 introduces the first production slice of Incident Management
v2 and intentionally changes the public API contract for technical AlertGroups
and operational Incidents.

## Highlights

- first-class operational `Incident` records independent from AlertGroup lifecycle;
- explicit `Alert -> AlertGroup -> Incident` ownership boundary;
- `/api/alert-groups` for technical grouped alerts;
- `/api/incidents` for operational Incidents only;
- manual Create Alert Group and Create Incident workflows with independent side effects;
- Incident priority, team, service and operational assignee;
- manual AlertGroup and Incident reassignment with independent ownership;
- Incident/AlertGroup link and unlink support;
- optimistic concurrency for Incident updates and lifecycle transitions;
- audit and Incident timeline events for core mutations;
- separate Alerts and Incidents UI surfaces;
- common Notification Policy filters for priority, severity, source and service attributes.

## Breaking API change

The old AlertGroup-shaped `/api/incidents` contract is removed. External clients
must migrate technical operations to `/api/alert-groups` before upgrading.
There is no runtime compatibility alias.

Incident IDs and AlertGroup IDs are independent. The same numeric ID may exist
in both tables and identifies different resources.

See [IncidentRelay 2.3 API and Upgrade Migration](api-migration-2.3.md) before
upgrading a production deployment.

## Database migration

The 2.3 Incident core migration adds:

```text
incident
incident_event
incident_alert_group_link
```

It preserves the existing `alert_group` and child `alert` tables and does not
backfill historical Incidents solely because an AlertGroup exists.

Keep a database backup until post-upgrade reconciliation is complete. The
Incident core downgrade removes the new Incident tables and is not a
preserving rollback after operators have written first-class Incident data.

## Security and consistency hardening

2.3 enforces team scope for Incident mutations and Incident/AlertGroup links.
AlertGroups outside the caller's team scope cannot be linked through an
accessible Incident.

Incident mutable state uses row-version compare-and-swap semantics. PostgreSQL
row locks serialize competing Incident/AlertGroup link attempts so one
AlertGroup cannot become actively linked to two open Incidents.

## Upgrade checklist

Before enabling traffic after upgrade:

1. run `python manage.py migration-status` and confirm all 2.3 migrations are applied;
2. verify `/api/alert-groups` and `/api/incidents` return their distinct resource shapes;
3. verify existing technical AlertGroup history and comments remain available;
4. create a test Incident and confirm no AlertGroup is created implicitly;
5. create a manual AlertGroup and confirm one initial child Alert is created;
6. verify link/unlink and team-scope permissions;
7. run the full test suite on the database engines used in production;
8. for PostgreSQL, explicitly run `pytest -q tests/postgresql`.

## Deferred to later Incident Management v2 releases

2.3 does not attempt to complete the entire Incident Management v2 roadmap.
Flapping/reopen resilience, richer Incident collaboration, merge/split,
role-aware automation, ITSM integrations, analytics and postmortems remain in
the staged 2.4-2.10 workstreams.
