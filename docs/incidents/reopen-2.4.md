---
title: AlertGroup Reopen (2.4)
description: Opt-in flapping-safe reuse of resolved technical AlertGroups.
---

# AlertGroup reopen (IncidentRelay 2.4)

A resolved child Alert is terminal. An active Event Orchestration can opt into
reusing a recently resolved **AlertGroup** if another firing occurrence matches
the same technical identity. Reuse creates a **new child Alert**; no old child
is changed from `resolved` back to `firing`.

## Opt in

Add a `set_grouping` action to a published **active** Event Orchestration:

```json
{
  "type": "set_grouping",
  "reopen_window_seconds": 300
}
```

The default is **disabled**. Missing or `0` means that a firing signal after
resolution creates a new AlertGroup. Values must be integers from 0 to 86400.
Shadow and legacy orchestration runs cannot activate this behavior.

`window_seconds` is the older grouping/deduplication configuration; it is **not**
the resolved-group reopen grace period. Do not use it to configure flapping.

## Matching and concurrency

Only a NEW firing occurrence is eligible. Group selection requires the same:
`source`, effective `group_key`, owning team, route, and service (including
absence of a service). Merged groups are never reopened. The grace period is
measured from `AlertGroup.resolved_at`, not from the last alert intake.

PostgreSQL uses a transaction-scoped advisory lock on this exact identity;
MySQL locks the route row for the transaction; SQLite reserves the writer
before looking up a candidate. Two simultaneous
firing events for one identity cannot create two independent reopened groups.
The new child and group state transition share one transaction.

On reopen, the group starts a new ACK/escalation/reminder cycle. Previous
resolved children and the group's original `first_seen_at` are preserved. An
AlertGroup `reopened` timeline event names the new child Alert. The Explain
trace records the effective reopen window and whether reuse occurred.

If the AlertGroup links to a non-closed first-class Incident in `resolved` or
`monitoring`, that Incident returns to `investigating`, with its own audit and
timeline event. Incidents in `closed` or `cancelled` remain unchanged. Technical
recovery never implies automatic Incident closure.

The policy **does not** infer recovery from missing webhook traffic and does not
introduce a technical `closed` AlertGroup state.

## Notification behavior

On reopen, stale queued technical notification work is cleared and the
notification/escalation cycle starts again. Queued user-level `resolved`
notifications for the previous occurrence are skipped if the group is now open.

**Recovery notification hysteresis is a separate follow-up**, not enabled by
`reopen_window_seconds`: resolved notifications still follow the existing
immediate-delivery behavior. A durable, retriable delayed-recovery outbox and
safe cancellation must be added before claiming this part of #85 complete.

## Compatibility

`/api/alert-groups` and `/api/incidents` do not change their ownership contract.
No database migration is required for the core reopen behavior. Existing
installations retain their 2.3 behavior unless an active orchestration
explicitly enables the reopen grace period.
