"""Two firing deliveries must not create two reopened groups/children."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from app.db import database_proxy
from app.modules.db.models import Alert, AlertGroup
from app.services.alerts.lifecycle import upsert_alert
from app.services.alerts import lifecycle
from app.services.orchestration.runtime import RuntimeResult
from tests.factories import create_channel, create_group, create_route, create_team, unique

pytestmark = pytest.mark.postgresql


def test_simultaneous_reopen_has_one_new_child(db, monkeypatch):
    if "postgres" not in db.__class__.__name__.lower():
        pytest.skip("requires PostgreSQL")

    g = create_group()
    team = create_team(g)
    route = create_route(team, source="alertmanager", group_by=["alertname"])
    monkeypatch.setattr(
        lifecycle, "run_event_orchestration",
        lambda data, trace=None: RuntimeResult(grouping_reopen_window_seconds=120),
    )
    data = {
        "source": "alertmanager", "forced_route_id": route.id,
        "external_id": unique("flap"), "dedup_key": unique("flap-dedup"),
        "title": "DiskFull", "severity": "critical", "status": "firing",
        "labels": {"alertname": "DiskFull"}, "payload": {},
    }
    first = upsert_alert(dict(data))
    resolved = dict(data, status="resolved")
    upsert_alert(resolved)
    assert AlertGroup.get_by_id(first.group.id).status == "resolved"

    barrier = Barrier(2)

    def fire(_):
        with database_proxy.obj.connection_context():
            barrier.wait(timeout=10)
            return upsert_alert(dict(data)).group.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(fire, (1, 2)))

    assert ids == [first.group.id, first.group.id]
    assert Alert.select().where(Alert.group == first.group.id).count() == 2
    assert AlertGroup.select().where(AlertGroup.id == first.group.id).count() == 1


def test_recovery_delivery_serializes_with_concurrent_reopen(db, monkeypatch):
    """Resolved delivery wins or is cancelled; it must not follow a reopen."""
    if "postgres" not in db.__class__.__name__.lower():
        pytest.skip("requires PostgreSQL")

    from threading import Event
    from app.modules.common import utc_now
    from app.services.alerts import notification_queue
    from app.settings import Config

    monkeypatch.setattr(Config, "RECOVERY_NOTIFICATION_DELAY_SECONDS", 60)
    group = create_group()
    team = create_team(group)
    route = create_route(team, source="alertmanager", group_by=["alertname"])
    monkeypatch.setattr(
        lifecycle,
        "run_event_orchestration",
        lambda event, trace=None: RuntimeResult(grouping_reopen_window_seconds=120),
    )
    data = {
        "source": "alertmanager", "forced_route_id": route.id,
        "external_id": unique("race"), "dedup_key": unique("race-dedup"),
        "title": "DiskFull", "severity": "critical", "status": "firing",
        "labels": {"alertname": "DiskFull"}, "payload": {},
    }
    first = upsert_alert(dict(data))
    original = AlertGroup.get_by_id(first.group.id)
    original.last_notification_at = utc_now()
    original.save(only=[AlertGroup.last_notification_at])
    upsert_alert(dict(data, status="resolved"))
    from app.modules.db import notifications_repo
    channel = create_channel(group, team)
    notifications_repo.save_notification(
        group_id=first.group.id, channel_id=channel.id,
        provider="webhook", event_type="notification", provider_status="sent",
    )

    AlertGroup.update(
        notification_due_at=utc_now() - timedelta(seconds=1)
    ).where(AlertGroup.id == first.group.id).execute()
    selected = AlertGroup.get_by_id(first.group.id)

    sending = Event()
    allow_send = Event()
    calls = []

    def fake_delivery(current, *, event_type, channel_ids, **kwargs):
        sending.set()
        assert allow_send.wait(timeout=10)
        calls.append((current.id, event_type))
        return {"sent": 1, "channels": {
            channel.id: {"status": "sent"},
        }}

    monkeypatch.setattr(notification_queue, "notify_alert", fake_delivery)

    def send_due():
        with database_proxy.obj.connection_context():
            return notification_queue._process_due_recovery(selected, now=utc_now())

    def reopen():
        with database_proxy.obj.connection_context():
            return upsert_alert(dict(data)).group.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        sending_future = executor.submit(send_due)
        assert sending.wait(timeout=10)
        reopening_future = executor.submit(reopen)
        allow_send.set()
        assert sending_future.result(timeout=20) == "sent"
        assert reopening_future.result(timeout=20) == first.group.id

    assert calls == [(first.group.id, "resolved")]
    assert AlertGroup.get_by_id(first.group.id).status == "firing"
    from app.modules.db.models import AlertEvent
    event_types = [
        event.event_type for event in AlertEvent.select()
        .where(AlertEvent.group == first.group.id)
        .order_by(AlertEvent.id.asc())
    ]
    assert event_types.index("recovery_notification_sent") < event_types.index("reopened")
