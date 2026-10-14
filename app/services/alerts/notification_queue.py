import logging
from datetime import timedelta

from app import Config
from app.db import database_proxy as db
from app.modules.db.models import AlertGroup
from app.services.alerts.reopen import lock_grouping_identity
from app.services.incidents.stakeholders import notify_stakeholders
from app.modules.db import alerts_repo
from app.services.notifications.delivery import notify_alert
from app.services.alerts.shelving import is_alert_group_shelved
from app.services.alerts.maintenance_state import (
    is_notification_lifecycle_suppressed,
    pause_notification_lifecycle,
    resume_notification_lifecycle,
)
from app.modules.common import utc_now

logger = logging.getLogger("oncall.alerts")

RECOVERY_NOTIFICATION_REASON = "recovery"
MAX_RECOVERY_DELAY_SECONDS = 86400


def recovery_notification_delay_seconds():
    """Opt-in recovery grace, independent of grouping reopen policy."""
    try:
        configured = int(getattr(Config, "RECOVERY_NOTIFICATION_DELAY_SECONDS", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(configured, MAX_RECOVERY_DELAY_SECONDS))


def schedule_recovery_notification(group, *, now=None):
    """Persist one delayed recovery after a real technical resolution.

    The conditional UPDATE cannot resurrect a group reopened by another intake
    or move an already queued recovery deadline after repeated resolved events.
    """
    delay = recovery_notification_delay_seconds()
    if not delay or group.status != "resolved" or not group.resolved_at:
        return False

    now = now or utc_now()
    due_at = now + timedelta(seconds=delay)

    updated = (
        AlertGroup
        .update(
            notification_pending=True,
            notification_due_at=due_at,
            notification_reason=RECOVERY_NOTIFICATION_REASON,
            updated_at=now,
        )
        .where(
            (AlertGroup.id == group.id)
            & (AlertGroup.status == "resolved")
            & (AlertGroup.resolved_at == group.resolved_at)
            & (AlertGroup.merged_into.is_null(True))
            & (
                (AlertGroup.notification_pending == False)  # noqa: E712
                | AlertGroup.notification_reason.is_null(True)
                | (AlertGroup.notification_reason != RECOVERY_NOTIFICATION_REASON)
            )
        )
        .execute()
    )
    if not updated:
        return False

    group.notification_pending = True
    group.notification_due_at = due_at
    group.notification_reason = RECOVERY_NOTIFICATION_REASON
    alerts_repo.create_alert_event(
        group_id=group.id,
        event_type="recovery_notification_scheduled",
        message=(
            f"Recovery notification deferred for {delay}s "
            f"until {due_at.isoformat()} UTC"
        ),
    )
    return True


def _process_due_recovery(selected_group, *, now):
    """Serialize recovery delivery with a concurrent resolved-group reopen.

    Delivery intentionally holds the grouping lock until the external call has
    finished: a reopen cannot commit while a stale recovery is being sent.
    This prevents the scheduler from sending a resolution *after* a committed
    reopen. External providers cannot retract a message already delivered.
    """
    database = db.obj
    transaction = (
        db.atomic(lock_type="IMMEDIATE")
        if database.__class__.__name__ == "SqliteDatabase"
        else db.atomic()
    )

    with transaction:
        lock_grouping_identity(
            source=selected_group.source,
            group_key=selected_group.group_key,
            team_id=selected_group.team_id,
            route_id=selected_group.route_id,
            service_id=selected_group.service_id,
        )
        query = AlertGroup.select().where(AlertGroup.id == selected_group.id)
        if database.__class__.__name__ != "SqliteDatabase":
            query = query.for_update()
        group = query.first()

        if (
            group is None
            or group.merged_into_id is not None
            or group.status != "resolved"
            or not group.notification_pending
            or group.notification_reason != RECOVERY_NOTIFICATION_REASON
            or group.notification_due_at is None
            or group.notification_due_at > now
            or group.notification_due_at != selected_group.notification_due_at
        ):
            return "skipped"

        sent_count = 0
        if group.last_notification_at:
            sent_count = notify_alert(group, event_type="resolved")

        stakeholder_result = notify_stakeholders(
            group, "resolved", old_value=group.previous_status,
        ) or {}
        stakeholder_count = int(stakeholder_result.get("sent", 0) or 0)

        # Do not use mark_alert_group_notification_sent here: it would mark an
        # empty recovery as a successful notification even without a target.
        alerts_repo.clear_alert_group_notification(group)
        delivered = bool(sent_count or stakeholder_count)
        alerts_repo.create_alert_event(
            group_id=group.id,
            event_type=(
                "recovery_notification_sent"
                if delivered else "recovery_notification_skipped"
            ),
            message=(
                f"Delayed recovery processed: route/user deliveries={sent_count}, "
                f"stakeholder deliveries={stakeholder_count}"
            ),
        )
        return "sent" if delivered else "skipped"


def _alert_group_wait_seconds():
    return int(getattr(Config, "ALERT_GROUP_WAIT_SECONDS", 30) or 0)


def _alert_group_interval_seconds():
    return int(getattr(Config, "ALERT_GROUP_INTERVAL_SECONDS", 300) or 0)


def schedule_group_notification(group, reason="notification", now=None):
    """Schedule group notification according to group_wait/group_interval."""

    now = now or utc_now()

    if is_alert_group_shelved(group, now=now):
        alerts_repo.clear_alert_group_notification(group)
        if group.next_escalation_at is not None:
            group.next_escalation_at = None
            group.updated_at = now
            group.save()
        return group

    if is_notification_lifecycle_suppressed(group, now=now):
        pause_notification_lifecycle(group)
        return group

    resume_notification_lifecycle(group, now=now)

    if group.status != "firing":
        alerts_repo.clear_alert_group_notification(group)
        return group

    if not group.last_notification_at:
        due_at = now + timedelta(seconds=_alert_group_wait_seconds())
    else:
        next_allowed_at = (
            group.last_notification_at
            + timedelta(seconds=_alert_group_interval_seconds())
        )

        due_at = now if next_allowed_at <= now else next_allowed_at

    return alerts_repo.schedule_alert_group_notification(
        group,
        due_at=due_at,
        reason=reason,
    )


def process_due_alert_group_notifications(limit=100):
    """Send due alert group notifications."""

    now = utc_now()
    sent = 0
    skipped = 0
    failed = 0

    groups = alerts_repo.list_due_alert_group_notifications(
        now=now,
        limit=limit,
    )

    for selected_group in groups:
        group = selected_group

        try:
            if selected_group.notification_reason == RECOVERY_NOTIFICATION_REASON:
                outcome = _process_due_recovery(selected_group, now=now)
                if outcome == "sent":
                    sent += 1
                else:
                    skipped += 1
                continue

            # The due-list query can race with an ACK handled by another
            # worker. Reload before recalculation so a stale firing object does
            # not overwrite the persisted acknowledged state.
            group = alerts_repo.get_alert_group(selected_group.id)

            if (
                not group.notification_pending
                or group.status != "firing"
                or group.notification_due_at is None
                or group.notification_due_at > now
                or group.notification_due_at != selected_group.notification_due_at
                or group.notification_reason != selected_group.notification_reason
            ):
                if (
                    group.notification_pending
                    and group.status != "firing"
                    and group.notification_reason == selected_group.notification_reason
                    and group.notification_reason != RECOVERY_NOTIFICATION_REASON
                ):
                    alerts_repo.clear_alert_group_notification(group)
                skipped += 1
                continue

            group = alerts_repo.recalculate_alert_group(group)

            if is_alert_group_shelved(group, now=now):
                alerts_repo.clear_alert_group_notification(group)
                skipped += 1
                continue

            if is_notification_lifecycle_suppressed(group, now=now):
                pause_notification_lifecycle(group)
                skipped += 1
                continue

            resume_notification_lifecycle(group, now=now)

            if group.status != "firing":
                alerts_repo.clear_alert_group_notification(group)
                skipped += 1
                continue

            event_type = (
                "notification"
                if not group.last_notification_at
                else "update"
            )

            sent_count = notify_alert(group, event_type=event_type)

            if not sent_count:
                alerts_repo.clear_alert_group_notification(group)

                alerts_repo.create_alert_event(
                    group_id=group.id,
                    event_type=f"{event_type}_skipped",
                    message="Due alert group notification skipped: no delivery target",
                )

                skipped += 1
                continue

            alerts_repo.mark_alert_group_notification_sent(group, now=now)

            alerts_repo.create_alert_event(
                group_id=group.id,
                event_type=f"{event_type}_sent",
                message="Due alert group notification sent",
            )

            sent += 1

        except Exception as exc:
            failed += 1

            logger.exception(
                "failed to send due alert group notification",
                extra={
                    "extra": {
                        "alert_group_id": getattr(group, "id", None),
                        "error": str(exc),
                    }
                },
            )

    return {
        "processed": len(groups),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
    }
