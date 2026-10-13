"""Two firing deliveries must not create two reopened groups/children."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.db import database_proxy
from app.modules.db.models import Alert, AlertGroup
from app.services.alerts.lifecycle import upsert_alert
from app.services.alerts import lifecycle
from app.services.orchestration.runtime import RuntimeResult
from tests.factories import create_group, create_route, create_team, unique

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
