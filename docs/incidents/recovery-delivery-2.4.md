---
title: Delayed recovery delivery hardening (2.4)
description: Durable per-recipient recovery retries and operational constraints.
---

# Delayed recovery notification delivery in 2.4

When `[alerts] recovery_notification_delay_seconds` is greater than zero,
technical resolution happens immediately, but external recovery messages wait
until the configured due time. The notification scheduler records a separate
`RecoveryNotificationDelivery` row for each previously notified channel, each
eligible stakeholder email/push target, and the user notification fan-out.

A failure of one target never causes a successful target to be resent on the
next scheduler run. Failed targets retry with exponential delays of 30, 60,
120, 240 seconds (maximum five attempts), with individual terminal status.
If some targets cannot be reached, the technical AlertGroup stays resolved and
its timeline records `recovery_notification_partial` or
`recovery_notification_failed` instead of claiming full success.

Recovery recipients are frozen on the first due processing. Reopening an
AlertGroup cancels outstanding targets for the old resolved occurrence. A
new technical occurrence is unaffected. User-level notification rules use
existing `UserNotificationDelivery` persistence and are enqueued once per
recovery cycle; this patch does not change their own retry semantics.

## Operational notes

- Requires DB migration `20261014090000_recovery_notification_deliveries`.
- Only affects nonzero delayed recovery; default immediate delivery is unchanged.
- Delivery is **at-least-once**, not exactly-once. A provider may accept a
  message before the scheduler loses its DB transaction. A crash at that point
  can cause a duplicate; HTTP and SMTP cannot share a DB transaction.
- PostgreSQL is recommended for production. The existing recovery worker
  holds its grouping transaction lock during outbound sends to prevent stale
  recovery delivery after a committed reopen. On SQLite this serializes
  writers and can block intake while slow providers respond.
- Before rolling back to code without this delivery ledger, drain or explicitly
  reconcile pending recovery jobs. Older workers cannot read individual
  target states and may resend a previously successful recovery message.
- Exhausted deliveries remain in the DB for incident response and audit.
  Reconciliation or explicit operator retry needs a separate API/workflow;
  a retry cannot be inferred simply from editing an alert.
- The user-rule fanout remains one-shot per resolved cycle. The rule worker's
  existing failure statuses are not requeued by this delivery ledger.
