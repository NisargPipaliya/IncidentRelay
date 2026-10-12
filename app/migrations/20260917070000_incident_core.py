"""Add first-class Incident core tables for IncidentRelay 2.3."""

import json

from app.db import init_database
from app.modules.db.models import (
    AlertGroup,
    Incident,
    IncidentAlertGroupLink,
    IncidentEvent,
)


db = init_database()


def _count_rows(model):
    table_name = model._meta.table_name
    if not db.table_exists(table_name):
        return None
    return model.select().count()


def reconciliation_report(*, alert_groups_before=None):
    """Return a compact, non-mutating reconciliation summary."""
    return {
        "alert_groups_before": alert_groups_before,
        "alert_groups_after": _count_rows(AlertGroup),
        "incidents_after": _count_rows(Incident),
        "incident_events_after": _count_rows(IncidentEvent),
        "incident_alert_group_links_after": _count_rows(IncidentAlertGroupLink),
        # 2.3 deliberately does not create historical Incidents just because
        # an AlertGroup exists. Later staged migrations may move operational
        # collaboration data only when an unambiguous mapping exists.
        "historical_incidents_backfilled": 0,
    }


def upgrade():
    """Create first-class Incident and AlertGroup link storage."""
    alert_groups_before = _count_rows(AlertGroup)
    db.create_tables([Incident, IncidentEvent, IncidentAlertGroupLink], safe=True)

    report = reconciliation_report(
        alert_groups_before=alert_groups_before,
    )
    print(
        "Incident core migration reconciliation: "
        + json.dumps(report, sort_keys=True)
    )
    return report


def downgrade():
    """Remove Incident core tables in dependency-safe order."""
    db.drop_tables([IncidentAlertGroupLink, IncidentEvent, Incident], safe=True)
