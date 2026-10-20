from app.login import create_access_token
from app.modules.db import incident_core_repo
from app.modules.db.models import AlertGroup
from app.services.incidents import core
from tests.factories import add_user_to_team, create_group, create_team, create_user, unique


def _headers(user):
    token, _ = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


def _alert_group(team):
    key = unique("incident-link-api")
    return AlertGroup.create(
        team=team,
        source="manual",
        group_key_hash=key,
        group_key=key,
        title="Linked technical signal",
    )


def test_link_is_visible_from_incident_and_alert_group_api(client, db):
    group = create_group()
    team = create_team(group)
    user = create_user(group=group)
    add_user_to_team(team, user, role="responder")
    headers = _headers(user)

    alert_group = _alert_group(team)
    incident = core.create_incident(
        team_id=team.id,
        title="Operational incident",
        user_id=user.id,
    )

    linked = client.post(
        f"/api/incidents/{incident.id}/alert-groups",
        json={"alert_group_id": alert_group.id, "relation_type": "related"},
        headers=headers,
    )
    assert linked.status_code == 201, linked.get_json()
    assert linked.get_json()["alert_group_id"] == alert_group.id

    incident_links = client.get(
        f"/api/incidents/{incident.id}/alert-groups",
        headers=headers,
    )
    assert incident_links.status_code == 200, incident_links.get_json()
    assert len(incident_links.get_json()) == 1
    assert incident_links.get_json()[0]["alert_group"]["id"] == alert_group.id

    alert_link = client.get(
        f"/api/alert-groups/{alert_group.id}/incident",
        headers=headers,
    )
    assert alert_link.status_code == 200, alert_link.get_json()
    payload = alert_link.get_json()
    assert payload["relation_type"] == "related"
    assert payload["incident"]["id"] == incident.id
    assert payload["incident"]["title"] == "Operational incident"


def test_alert_group_incident_endpoint_returns_null_after_unlink(client, db):
    group = create_group()
    team = create_team(group)
    user = create_user(group=group)
    add_user_to_team(team, user, role="responder")
    headers = _headers(user)

    alert_group = _alert_group(team)
    incident = core.create_incident(
        team_id=team.id,
        title="Operational incident",
        user_id=user.id,
    )
    core.link_alert_group(incident.id, alert_group.id, user_id=user.id)
    core.unlink_alert_group(incident.id, alert_group.id, user_id=user.id)

    response = client.get(
        f"/api/alert-groups/{alert_group.id}/incident",
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json() is None


def test_link_rejects_alert_group_outside_user_team_scope(client, db):
    own_group = create_group()
    own_team = create_team(own_group)
    foreign_group = create_group()
    foreign_team = create_team(foreign_group)

    user = create_user(group=own_group)
    add_user_to_team(own_team, user, role="responder")
    headers = _headers(user)

    incident = core.create_incident(
        team_id=own_team.id,
        title="Operational incident",
        user_id=user.id,
    )
    foreign_alert_group = _alert_group(foreign_team)

    response = client.post(
        f"/api/incidents/{incident.id}/alert-groups",
        json={
            "alert_group_id": foreign_alert_group.id,
            "relation_type": "related",
        },
        headers=headers,
    )

    assert response.status_code == 403
    assert incident_core_repo.active_link(
        incident.id,
        foreign_alert_group.id,
    ) is None


def test_foreign_team_user_cannot_read_or_unlink_incident_links(client, db):
    own_group = create_group()
    own_team = create_team(own_group)
    foreign_group = create_group()
    foreign_team = create_team(foreign_group)

    owner = create_user(group=own_group)
    add_user_to_team(own_team, owner, role="responder")

    foreign_user = create_user(group=foreign_group)
    add_user_to_team(foreign_team, foreign_user, role="responder")
    foreign_headers = _headers(foreign_user)

    alert_group = _alert_group(own_team)
    incident = core.create_incident(
        team_id=own_team.id,
        title="Operational incident",
        user_id=owner.id,
    )
    core.link_alert_group(
        incident.id,
        alert_group.id,
        user_id=owner.id,
    )

    list_response = client.get(
        f"/api/incidents/{incident.id}/alert-groups",
        headers=foreign_headers,
    )
    assert list_response.status_code == 403

    unlink_response = client.delete(
        f"/api/incidents/{incident.id}/alert-groups/{alert_group.id}",
        headers=foreign_headers,
    )
    assert unlink_response.status_code == 403

    assert incident_core_repo.active_link(
        incident.id,
        alert_group.id,
    ) is not None
