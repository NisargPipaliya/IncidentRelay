from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.db import database_proxy
from app.modules.db import incident_core_repo
from app.modules.db.models import AlertGroup, IncidentAlertGroupLink
from app.services.incidents import core
from tests.factories import add_user_to_team, create_group, create_team, create_user, unique


pytestmark = pytest.mark.postgresql


def _alert_group(team):
    key = unique("incident-link-race")
    return AlertGroup.create(
        team=team,
        source="manual",
        group_key_hash=key,
        group_key=key,
        title="Concurrent link target",
    )


def test_concurrent_link_allows_only_one_open_incident(db):
    if "postgres" not in db.__class__.__name__.lower():
        pytest.skip("PostgreSQL row-lock concurrency test")

    group = create_group()
    team = create_team(group)
    actor = create_user(group=group)
    add_user_to_team(team, actor, role="responder")

    alert_group = _alert_group(team)
    first = core.create_incident(
        team_id=team.id,
        title="First incident",
        user_id=actor.id,
    )
    second = core.create_incident(
        team_id=team.id,
        title="Second incident",
        user_id=actor.id,
    )

    barrier = Barrier(2)

    def attempt(incident_id):
        database = database_proxy.obj
        with database.connection_context():
            barrier.wait(timeout=10)
            try:
                link = core.link_alert_group(
                    incident_id,
                    alert_group.id,
                    user_id=actor.id,
                )
                return ("linked", link.incident_id)
            except core.IncidentConflictError:
                return ("conflict", incident_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (first.id, second.id)))

    assert sorted(result[0] for result in results) == ["conflict", "linked"]

    active_links = list(
        IncidentAlertGroupLink.select().where(
            IncidentAlertGroupLink.alert_group == alert_group.id,
            IncidentAlertGroupLink.removed_at.is_null(True),
        )
    )
    assert len(active_links) == 1

    active = incident_core_repo.active_link_for_group(alert_group.id)
    assert active is not None
    assert active.incident_id in {first.id, second.id}
