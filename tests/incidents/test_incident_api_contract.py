from app.modules.db.models import AlertGroup, Incident
from tests.factories import create_group, create_team, unique


def test_alert_group_and_incident_ids_are_independent_api_namespaces(
    client,
    admin_headers,
    db,
):
    group = create_group()
    team = create_team(group)

    shared_id = 424242
    key = unique("api-id-collision")

    alert_group = AlertGroup.create(
        id=shared_id,
        team=team,
        source="manual",
        group_key_hash=key,
        group_key=key,
        title="Technical alert group",
    )
    incident = Incident.create(
        id=shared_id,
        team=team,
        title="Operational incident",
    )

    alert_response = client.get(
        f"/api/alert-groups/{shared_id}",
        headers=admin_headers,
    )
    incident_response = client.get(
        f"/api/incidents/{shared_id}",
        headers=admin_headers,
    )

    assert alert_response.status_code == 200, alert_response.get_json()
    assert incident_response.status_code == 200, incident_response.get_json()

    alert_payload = alert_response.get_json()
    incident_payload = incident_response.get_json()

    assert alert_payload["id"] == alert_group.id == shared_id
    assert alert_payload["type"] == "alert_group"
    assert alert_payload["title"] == "Technical alert group"
    assert "workflow_status" not in alert_payload

    assert incident_payload["id"] == incident.id == shared_id
    assert incident_payload["type"] == "incident"
    assert incident_payload["title"] == "Operational incident"
    assert incident_payload["workflow_status"] == "declared"
    assert "status" not in incident_payload
