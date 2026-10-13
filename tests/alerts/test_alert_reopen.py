"""IncidentRelay 2.4: resolved-group reuse is opt-in and non-destructive."""

from datetime import timedelta

import pytest

from app.modules.common import utc_now
from app.modules.db.models import (
    Alert, AlertEvent, AlertGroup, Incident, IncidentAlertGroupLink, IncidentEvent,
    UserNotificationDelivery,
)
from app.services.alerts.lifecycle import upsert_alert
from app.services.alerts import lifecycle
from app.services.orchestration.runtime import RuntimeResult
from tests.factories import create_group, create_route, create_team, create_service, create_user


def _setup(monkeypatch, *, window=300):
    group = create_group()
    team = create_team(group)
    route = create_route(team, source="alertmanager", group_by=["alertname"])
    monkeypatch.setattr(
        lifecycle, "run_event_orchestration",
        lambda alert_data, trace=None: RuntimeResult(
            grouping_reopen_window_seconds=window,
        ),
    )
    return team, route


def _data(route, key="reopen-key", *, status="firing"):
    return {
        "source": "alertmanager", "forced_route_id": route.id,
        "external_id": key, "dedup_key": key,
        "title": "Disk full", "message": "Disk alert",
        "status": status, "severity": "critical",
        "labels": {"alertname": "DiskFull"}, "payload": {},
    }


def _resolved_group(route):
    first = upsert_alert(_data(route))
    assert first.created_group is True
    resolved = upsert_alert(_data(route, status="resolved"))
    assert resolved.group.id == first.group.id
    assert resolved.group.status == "resolved"
    return first


def test_reopen_adds_child_and_keeps_old_child_resolved(db, monkeypatch):
    team, route = _setup(monkeypatch)
    first = _resolved_group(route)
    before = first.alert.first_seen_at
    result = upsert_alert(_data(route))
    assert result.created_group is False
    assert result.outcome == "reopened"
    assert result.group.id == first.group.id
    assert result.alert.id != first.alert.id
    assert result.group.status == "firing"
    assert result.group.resolved_at is None
    old = Alert.get_by_id(first.alert.id)
    assert old.status == "resolved"
    assert old.resolved_at is not None
    assert old.first_seen_at == before
    assert Alert.select().where(Alert.group == first.group.id).count() == 2
    assert AlertEvent.select().where(
        AlertEvent.group == first.group.id,
        AlertEvent.event_type == "reopened",
        AlertEvent.alert == result.alert.id,
    ).count() == 1


def test_reopen_is_disabled_when_grace_is_zero(db, monkeypatch):
    _, route = _setup(monkeypatch, window=0)
    first = _resolved_group(route)
    result = upsert_alert(_data(route))
    assert result.created_group is True
    assert result.group.id != first.group.id


def test_expired_grace_creates_new_group(db, monkeypatch):
    _, route = _setup(monkeypatch, window=60)
    first = _resolved_group(route)
    AlertGroup.update(resolved_at=utc_now() - timedelta(seconds=61)).where(
        AlertGroup.id == first.group.id,
    ).execute()
    result = upsert_alert(_data(route))
    assert result.created_group is True
    assert result.group.id != first.group.id


def test_resolved_payload_does_not_reopen(db, monkeypatch):
    _, route = _setup(monkeypatch)
    first = _resolved_group(route)
    result = upsert_alert(_data(route, status="resolved"))
    assert result.outcome == "ignored"
    assert AlertGroup.get_by_id(first.group.id).status == "resolved"
    assert Alert.select().where(Alert.group == first.group.id).count() == 1


def test_active_alert_is_not_hijacked_across_routes(db, monkeypatch):
    _, route = _setup(monkeypatch)
    first = upsert_alert(_data(route))
    other = create_route(route.team, source="alertmanager", group_by=["alertname"])
    second = upsert_alert(_data(other))
    assert second.created_group is True
    assert second.group.id != first.group.id
    assert second.group.route_id == other.id
    assert Alert.get_by_id(first.alert.id).route_id == route.id


def test_different_route_is_not_reused(db, monkeypatch):
    _, route = _setup(monkeypatch)
    first = _resolved_group(route)
    # Same source, team, grouping and dedup_key, but different intake route.
    other = create_route(route.team, source="alertmanager", group_by=["alertname"])
    result = upsert_alert(_data(other))
    assert result.created_group is True
    assert result.group.id != first.group.id


def test_different_service_is_not_reused(db, monkeypatch):
    team, route = _setup(monkeypatch)
    first_service = create_service(team)
    other_service = create_service(team)
    initial = _data(route)
    initial["_forced_service_id"] = first_service.id
    first = upsert_alert(dict(initial))
    upsert_alert(dict(initial, status="resolved"))
    incoming = _data(route)
    incoming["_forced_service_id"] = other_service.id
    later = upsert_alert(incoming)
    assert later.created_group is True
    assert later.group.id != first.group.id
    assert later.group.service_id == other_service.id


def test_reopen_cancels_pending_previous_cycle_deliveries(db, monkeypatch):
    team, route = _setup(monkeypatch)
    first = _resolved_group(route)
    user = create_user(group=team.group)
    old_resolution = UserNotificationDelivery.create(
        group=first.group, user=user, method="email", event_type="resolved",
        scheduled_at=utc_now() + timedelta(minutes=3),
    )
    result = upsert_alert(_data(route))
    assert result.outcome == "reopened"
    old_resolution = UserNotificationDelivery.get_by_id(old_resolution.id)
    assert old_resolution.status == "skipped"
    assert old_resolution.last_error == "alert_group_reopened"


@pytest.mark.parametrize("status", ["resolved", "monitoring"])
def test_reopen_resumes_linked_resolved_incident_not_closed(db, monkeypatch, status):
    team, route = _setup(monkeypatch)
    first = _resolved_group(route)
    incident = Incident.create(
        team=team, title="Disk outage", workflow_status=status,
        resolved_at=utc_now(),
    )
    IncidentAlertGroupLink.create(
        incident=incident, alert_group=first.group, relation_type="primary",
    )
    result = upsert_alert(_data(route))
    assert result.group.id == first.group.id
    current = Incident.get_by_id(incident.id)
    assert current.workflow_status == "investigating"
    assert current.resolved_at is None
    assert current.row_version == 2
    assert IncidentEvent.select().where(
        IncidentEvent.incident == incident.id,
        IncidentEvent.event_type == "incident_reopened_by_alert_group",
    ).count() == 1


def test_closed_incident_stays_closed(db, monkeypatch):
    team, route = _setup(monkeypatch)
    first = _resolved_group(route)
    incident = Incident.create(team=team, title="Closed outage", workflow_status="closed")
    IncidentAlertGroupLink.create(incident=incident, alert_group=first.group)
    upsert_alert(_data(route))
    assert Incident.get_by_id(incident.id).workflow_status == "closed"


def test_new_child_failure_rolls_back_reopen(db, monkeypatch):
    _, route = _setup(monkeypatch)
    first = _resolved_group(route)
    resolved_at = AlertGroup.get_by_id(first.group.id).resolved_at

    def fail_child(**kwargs):
        raise RuntimeError("child insert failed")

    from app.modules.db import alerts_repo
    monkeypatch.setattr(alerts_repo, "create_alert", fail_child)
    with pytest.raises(RuntimeError, match="child insert failed"):
        upsert_alert(_data(route))
    old = AlertGroup.get_by_id(first.group.id)
    assert old.status == "resolved"
    assert old.resolved_at == resolved_at
    assert Alert.select().where(Alert.group == old.id).count() == 1
