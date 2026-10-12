function dashboardAsArray(value) {
    /*
     * Return alerts from both old array responses and new paginated responses.
     */
    if (Array.isArray(value)) {
        return value;
    }
    if (value && Array.isArray(value.items)) {
        return value.items;
    }
    return [];
}

function dashboardDateValue(alert) {
    return alert.updated_at || alert.last_seen_at || alert.first_seen_at || alert.created_at || null;
}

function dashboardSortByActivity(alerts) {
    return alerts.slice().sort(function (left, right) {
        const leftDate = new Date(dashboardDateValue(left) || 0).getTime();
        const rightDate = new Date(dashboardDateValue(right) || 0).getTime();
        return rightDate - leftDate;
    });
}

function dashboardGroupCount(alerts, fieldName, fallback) {
    const result = {};
    alerts.forEach(function (alert) {
        const key = alert[fieldName] || fallback || "-";
        result[key] = (result[key] || 0) + 1;
    });
    return result;
}
function dashboardPrioritySlug(alert) {
    if (!alert) {
        return "p3";
    }

    if (alert.priority && alert.priority.slug) {
        return normalizeAlertValue(alert.priority.slug);
    }

    return normalizeAlertValue(alert.priority_slug || "p3");
}


function dashboardPriorityLabel(priority) {
    const slug = normalizeAlertValue(priority);
    const labels = {
        p1: "alerts.priority.p1",
        p2: "alerts.priority.p2",
        p3: "alerts.priority.p3",
        p4: "alerts.priority.p4",
        p5: "alerts.priority.p5",
    };

    return labels[slug]
        ? i18n.t(labels[slug])
        : (slug ? slug.toUpperCase() : i18n.t("alerts.priority.p3"));
}

function dashboardSeverityDisplayLabel(severity) {
    const normalized = normalizeAlertValue(severity);
    if (!normalized || normalized === "unknown") {
        return i18n.t("overview.labels.unknown");
    }
    return typeof severityLabel === "function" ? severityLabel(severity) : severity;
}

function dashboardAlertStatusLabel(status) {
    return typeof statusLabel === "function" ? statusLabel(status) : (status || "-");
}


function dashboardPriorityShortLabel(alert) {
    return dashboardPrioritySlug(alert).toUpperCase();
}


function dashboardPriorityCounts(alerts) {
    const result = {};

    alerts.forEach(function (alert) {
        const slug = dashboardPrioritySlug(alert);

        result[slug] = (result[slug] || 0) + 1;
    });

    return result;
}
function dashboardActiveAlerts(alerts) {
    return alerts.filter(function (alert) {
        return alert.status === "firing" || alert.status === "acknowledged";
    });
}
function dashboardEscalationText(alert) {
    if (alert.escalation_policy_name) {
        const rule = alert.escalation_rule_position
            ? i18n.t("overview.escalation.rule", {position: alert.escalation_rule_position})
            : "";
        return i18n.t("overview.escalation.policy", {
            name: alert.escalation_policy_name,
            rule: rule,
        });
    }
    return i18n.t("overview.escalation.rotation", {name: alert.rotation_name || "-"});
}
const DASHBOARD_FILTER_STORAGE_KEY = "incidentrelay.dashboard.filters.v1";
const DASHBOARD_INVOLVEMENTS = ["assigned", "responder", "stakeholder", "any"];
const DASHBOARD_FILTER_URL_PARAMS = ["involvement", "team_id", "service_id"];

let dashboardFilters = dashboardDefaultFilters();
let dashboardFiltersInitialized = false;
let dashboardFilterOptions = {key: null, teams: [], services: []};
let dashboardLoadSequence = 0;

function dashboardDefaultFilters() {
    return {involvement: "", teamIds: [], serviceIds: []};
}

function dashboardNormalizeFilters(raw) {
    raw = raw || {};

    return {
        involvement: DASHBOARD_INVOLVEMENTS.indexOf(raw.involvement) !== -1 ? raw.involvement : "",
        teamIds: normalizeTableFilterValues(raw.teamIds),
        serviceIds: normalizeTableFilterValues(raw.serviceIds),
    };
}

function dashboardFiltersFromUrl() {
    const params = new URLSearchParams(window.location.search || "");
    const hasFilterParams = DASHBOARD_FILTER_URL_PARAMS.some(function (name) {
        return params.has(name);
    });

    if (!hasFilterParams) {
        return null;
    }

    return dashboardNormalizeFilters({
        involvement: params.get("involvement") || "",
        teamIds: params.getAll("team_id"),
        serviceIds: params.getAll("service_id"),
    });
}

function readStoredDashboardFilters() {
    try {
        const raw = localStorage.getItem(DASHBOARD_FILTER_STORAGE_KEY);
        return raw ? dashboardNormalizeFilters(JSON.parse(raw)) : null;
    } catch (error) {
        return null;
    }
}

function writeStoredDashboardFilters(filters) {
    try {
        localStorage.setItem(DASHBOARD_FILTER_STORAGE_KEY, JSON.stringify(filters));
    } catch (error) {
        // Ignore storage errors, the filters still work for the current page.
    }
}

function writeDashboardFiltersToUrl(filters) {
    if (!window.history || !window.history.replaceState) {
        return;
    }

    if (normalizeAppRoutePath(window.location.pathname || "/") !== "/") {
        return;
    }

    const url = new URL(window.location.href);

    DASHBOARD_FILTER_URL_PARAMS.forEach(function (name) {
        url.searchParams.delete(name);
    });

    if (filters.involvement) {
        url.searchParams.set("involvement", filters.involvement);
    }

    appendTableFilterParams(url.searchParams, "team_id", filters.teamIds);
    appendTableFilterParams(url.searchParams, "service_id", filters.serviceIds);

    const nextUrl = url.pathname + url.search + url.hash;

    if (nextUrl !== currentAppUrl()) {
        const state = Object.assign({}, window.history.state || {}, {path: nextUrl});
        window.history.replaceState(state, "", nextUrl);
    }
}

function dashboardGlobalTeamId() {
    return typeof selectedTeamId === "function" && selectedTeamId()
        ? String(selectedTeamId())
        : "";
}

function dashboardEffectiveTeamIds() {
    const globalTeamId = dashboardGlobalTeamId();
    return globalTeamId ? [globalTeamId] : dashboardFilters.teamIds;
}

function dashboardMemberTeamIds() {
    return dashboardFilterOptions.teams
        .filter(function (team) {
            return team.is_member === true;
        })
        .map(function (team) {
            return String(team.id);
        });
}

function dashboardFilterOptionsKey() {
    return String((currentUser && currentUser.active_group_id) || "");
}

function loadDashboardFilterOptions(callback) {
    const key = dashboardFilterOptionsKey();

    if (dashboardFilterOptions.key === key) {
        callback(true);
        return;
    }

    let pending = 2;
    let failed = false;
    const result = {teams: [], services: []};

    function done() {
        pending -= 1;

        if (pending > 0) {
            return;
        }

        if (!failed) {
            dashboardFilterOptions = {key: key, teams: result.teams, services: result.services};
        }

        renderDashboardFilterOptions();
        callback(!failed);
    }

    function fail() {
        failed = true;
        done();
    }

    apiGet("/api/teams", function (teams) {
        result.teams = dashboardAsArray(teams);
        done();
    }, fail);

    apiGet("/api/services", function (services) {
        result.services = dashboardAsArray(services).filter(function (service) {
            return service.enabled !== false;
        });
        done();
    }, fail);
}

function renderDashboardFilterOptions() {
    replaceTableSelectOptions(
        "#dashboard-team-filter",
        dashboardFilterOptions.teams.map(function (team) {
            return {value: String(team.id), text: team.name || team.slug || String(team.id)};
        }),
        dashboardFilters.teamIds
    );

    replaceTableSelectOptions(
        "#dashboard-service-filter",
        dashboardFilterOptions.services.map(function (service) {
            return {
                value: String(service.id),
                text: [
                    service.team_name || service.team_slug || null,
                    service.name || service.slug || service.id
                ].filter(Boolean).join(" / ")
            };
        }),
        dashboardFilters.serviceIds
    );
}

function dashboardDropUnknownFilterIds(filters) {
    /*
     * Drop saved or shared ids that the user can no longer see, so the
     * alerts API is never asked for an unreadable team.
     */
    const teamIds = dashboardFilterOptions.teams.map(function (team) {
        return String(team.id);
    });
    const serviceIds = dashboardFilterOptions.services.map(function (service) {
        return String(service.id);
    });

    return {
        involvement: filters.involvement,
        teamIds: filters.teamIds.filter(function (id) {
            return teamIds.indexOf(String(id)) !== -1;
        }),
        serviceIds: filters.serviceIds.filter(function (id) {
            return serviceIds.indexOf(String(id)) !== -1;
        }),
    };
}

function syncDashboardFilterControls() {
    const globalTeamId = dashboardGlobalTeamId();
    const teamSelect = $("#dashboard-team-filter");
    const teamSelectControl = teamSelect.length ? teamSelect[0].tomselect : null;

    $("#dashboard-involvement-filter").val(dashboardFilters.involvement);

    setTableFilterValues(teamSelect, dashboardEffectiveTeamIds());

    if (teamSelectControl) {
        if (globalTeamId) {
            teamSelectControl.disable();
        } else {
            teamSelectControl.enable();
        }
    }
    teamSelect.prop("disabled", !!globalTeamId);

    $("#dashboard-team-filter-hint").toggleClass("is-hidden", !globalTeamId);
    $("#dashboard-my-teams").prop(
        "disabled",
        !!globalTeamId || !dashboardMemberTeamIds().length
    );

    setTableFilterValues("#dashboard-service-filter", dashboardFilters.serviceIds);
}

function updateDashboardViewAllLinks() {
    /*
     * /alerts understands service_id and assigned_to_me. Team scope reaches it
     * through the global team selector, so a dashboard-only team selection is
     * not carried over.
     */
    const params = new URLSearchParams();

    appendTableFilterParams(params, "service_id", dashboardFilters.serviceIds);

    if (dashboardFilters.involvement === "assigned") {
        params.set("assigned_to_me", "true");
    }

    const query = params.toString();
    $("[data-dashboard-view-all]").attr("href", "/alerts" + (query ? "?" + query : ""));
}

function applyDashboardFilters() {
    writeStoredDashboardFilters(dashboardFilters);
    writeDashboardFiltersToUrl(dashboardFilters);
    updateDashboardViewAllLinks();
    fetchDashboardData();
}

function bindDashboardFilterEvents() {
    $(document)
        .off(".dashboardFilters")
        .on("change.dashboardFilters", "#dashboard-involvement-filter", function () {
            dashboardFilters.involvement = dashboardNormalizeFilters({
                involvement: $(this).val(),
            }).involvement;
            applyDashboardFilters();
        })
        .on("change.dashboardFilters", "#dashboard-team-filter", function () {
            if (isTableFilterSilent(this) || dashboardGlobalTeamId()) {
                return;
            }
            dashboardFilters.teamIds = getTableFilterValues(this);
            applyDashboardFilters();
        })
        .on("change.dashboardFilters", "#dashboard-service-filter", function () {
            if (isTableFilterSilent(this)) {
                return;
            }
            dashboardFilters.serviceIds = getTableFilterValues(this);
            applyDashboardFilters();
        })
        .on("click.dashboardFilters", "#dashboard-my-teams", function () {
            dashboardFilters.teamIds = dashboardMemberTeamIds();
            syncDashboardFilterControls();
            applyDashboardFilters();
        })
        .on("click.dashboardFilters", "#dashboard-filters-reset", function () {
            dashboardFilters = dashboardDefaultFilters();
            syncDashboardFilterControls();
            applyDashboardFilters();
        });
}

function loadDashboard() {
    initTableMultiSelects("#view-dashboard");
    bindDashboardFilterEvents();

    loadDashboardFilterOptions(function (optionsLoaded) {
        const urlFilters = dashboardFiltersFromUrl();

        if (urlFilters) {
            dashboardFilters = urlFilters;
        } else if (!dashboardFiltersInitialized) {
            dashboardFilters = readStoredDashboardFilters() || dashboardDefaultFilters();
        }

        dashboardFiltersInitialized = true;

        if (optionsLoaded) {
            dashboardFilters = dashboardDropUnknownFilterIds(dashboardFilters);
        }

        syncDashboardFilterControls();
        applyDashboardFilters();
    });
}

function fetchDashboardData() {
    const sequence = ++dashboardLoadSequence;
    const params = [];

    appendTableFilterParams(params, "team_id", dashboardEffectiveTeamIds());
    appendTableFilterParams(params, "service_id", dashboardFilters.serviceIds);

    if (dashboardFilters.involvement) {
        params.push("involvement=" + encodeURIComponent(dashboardFilters.involvement));
    }

    params.push("page=1");
    params.push("page_size=100");
    params.push("sort=activity");
    params.push("order=desc");

    apiGet("/api/alerts?" + params.join("&"), function (response) {
        if (sequence !== dashboardLoadSequence) {
            return;
        }

        const alerts = dashboardAsArray(response);
        const activeAlerts = dashboardActiveAlerts(alerts);
        const sortedAlerts = dashboardSortByActivity(alerts);
        const sortedActiveAlerts = dashboardSortByActivity(activeAlerts);

        renderAlertsSummaryGrid("#overview-alerts-summary", alerts);
        renderDashboardAlertsTable(sortedActiveAlerts.slice(0, 15));
        renderDashboardRecentAlerts(sortedAlerts.slice(0, 5));
        renderDashboardTeamsNow(activeAlerts);
        renderDashboardSeveritySplit(alerts);
        renderDashboardPrioritySplit(alerts);
        renderDashboardTeamSummary(alerts);
        renderDashboardSystemStatus(alerts, activeAlerts);
    });
    loadDashboardServiceImpact(sequence);
}

function renderDashboardAlertsTable(alerts) {
    const tbody = $("#dashboard-alerts");
    tbody.empty();

    if (!alerts.length) {
        tbody.append(
            $("<tr>").append(
                $("<td>")
                    .attr("colspan", "9")
                    .addClass("empty-table-cell")
                    .text(i18n.t("overview.empty.no_active_incidents"))
            )
        );
        return;
    }

    alerts.forEach(function (alert) {
        tbody.append(renderDashboardAlertRow(alert));
    });
}

function renderDashboardAlertRow(alert) {
    const row = $("<tr>");

    row.append(
        $("<td>").append(
            $("<button>")
                .attr("type", "button")
                .attr("title", i18n.t("overview.alert.show_details"))
                .addClass("overview-id-link")
                .text("#" + alert.id)
                .on("click", function () {
                    if (typeof showAlertDetails === "function") {
                        showAlertDetails(alert.id);
                    }
                })
        )
    );
    row.append(
        $("<td>")
            .addClass("overview-alert-title-cell")
            .append($("<div>").addClass("overview-alert-title").text(alert.title || "-"))
            .append($("<div>").addClass("overview-alert-meta").text((alert.source || alert.route_name || i18n.t("overview.alert.fallback")) + " · " + dashboardEscalationText(alert)))
    );
    row.append(
        $("<td>").append(
            makeAlertBadge(
                dashboardSeverityDisplayLabel(alert.severity),
                severityBadgeClass(alert.severity)
            )
        )
    );

    row.append(
        $("<td>").append(
            makeAlertBadge(
                dashboardPriorityShortLabel(alert),
                priorityBadgeClass(alert)
            ).attr("title", dashboardPriorityLabel(dashboardPrioritySlug(alert)))
        )
    );

    row.append(
        $("<td>").append(
            makeAlertBadge(dashboardAlertStatusLabel(alert.status), statusBadgeClass(alert.status))
        )
    );
    row.append($("<td>").text(alert.team_name || alert.team_slug || "-"));
    row.append($("<td>").addClass("overview-duration-cell").text(alertDuration(alert)));
    row.append($("<td>").text(formatDateTimeMinutes(dashboardDateValue(alert))));

    const actionsCell = $("<td>").addClass("actions-cell");
    const actions = $("<div>").addClass("table-actions");

    if (canRespondObject(alert)) {
        if (alert.status === "firing") {
            actions.append(
                $("<button>")
                    .attr("type", "button")
                    .addClass("btn btn-warning btn-small")
                    .text(i18n.t("overview.actions.ack"))
                    .on("click", function () {
                        apiPost("/api/alerts/" + alert.id + "/ack", {}, loadDashboard);
                    })
            );
        }
        if (alert.status !== "resolved") {
            actions.append(
                $("<button>")
                    .attr("type", "button")
                    .addClass("btn btn-resolve btn-small")
                    .text(i18n.t("overview.actions.resolve"))
                    .on("click", function () {
                        apiPost("/api/alerts/" + alert.id + "/resolve", {}, loadDashboard);
                    })
            );
        }
    }

    actionsCell.append(actions);
    row.append(actionsCell);
    return row;
}

function renderDashboardRecentAlerts(alerts) {
    const target = $("#dashboard-recent-alerts");
    target.empty();

    if (!alerts.length) {
        target.append($("<div>").addClass("overview-empty").text(i18n.t("overview.empty.no_alerts")));
        return;
    }

    alerts.forEach(function (alert) {
        const item = $("<button>")
            .attr("type", "button")
            .addClass("overview-list-item overview-list-button")
            .on("click", function () {
                if (typeof showAlertDetails === "function") {
                    showAlertDetails(alert.id);
                }
            });

        item.append(
            $("<span>")
                .addClass("overview-list-dot")
                .addClass("overview-dot-" + normalizeAlertValue(alert.status))
        );
        item.append(
            $("<div>")
                .addClass("list-main")
                .append($("<div>").addClass("list-title").text(alert.title || "-"))
                .append(
                    $("<div>")
                        .addClass("list-subtitle")
                        .text(
                            (alert.team_name || alert.team_slug || "-")
                            + " · "
                            + dashboardSeverityDisplayLabel(alert.severity)
                            + " · "
                            + dashboardPriorityShortLabel(alert)
                            + " · "
                            + dashboardEscalationText(alert)
                        )
                )
        );
        item.append(
            $("<span>")
                .addClass("overview-list-time")
                .text(formatDateTimeMinutes(dashboardDateValue(alert)))
        );
        target.append(item);
    });
}

function renderDashboardTeamsNow(activeAlerts) {
    const target = $("#dashboard-teams-now");
    target.empty();

    if (!activeAlerts.length) {
        target.append($("<div>").addClass("overview-empty").text(i18n.t("overview.empty.no_teams")));
        return;
    }

    const counts = dashboardGroupCount(activeAlerts, "team_slug", i18n.t("overview.labels.unknown_team"));
    const items = Object.keys(counts)
        .map(function (team) {
            return { team: team, count: counts[team] };
        })
        .sort(function (left, right) {
            return right.count - left.count;
        });

    items.slice(0, 6).forEach(function (item) {
        target.append(
            $("<div>")
                .addClass("list-item")
                .append(
                    $("<span>")
                        .addClass("avatar")
                        .text(item.team.slice(0, 2).toUpperCase())
                )
                .append(
                    $("<span>")
                        .addClass("list-main")
                        .append($("<span>").addClass("list-title").text(item.team))
                        .append($("<span>").addClass("list-subtitle").text(i18n.t("overview.teams.active_alerts")))
                )
                .append(
                    $("<span>")
                        .addClass("overview-team-count")
                        .text(item.count)
                )
        );
    });
}

function renderDashboardSeveritySplit(alerts) {
    const target = $("#dashboard-severity-split");
    target.empty();
    const counts = dashboardGroupCount(alerts, "severity", "unknown");
    const order = ["critical", "high", "medium", "low", "unknown"];
    renderDashboardBars(target, counts, order, alerts.length, dashboardSeverityDisplayLabel);
}
function renderDashboardPrioritySplit(alerts) {
    const target = $("#dashboard-priority-split");

    target.empty();

    const counts = dashboardPriorityCounts(alerts);
    const order = ["p1", "p2", "p3", "p4", "p5"];

    renderDashboardBars(
        target,
        counts,
        order,
        alerts.length,
        dashboardPriorityLabel
    );
}
function renderDashboardTeamSummary(alerts) {
    const target = $("#dashboard-team-summary");
    target.empty();
    const counts = dashboardGroupCount(alerts, "team_slug", i18n.t("overview.labels.unknown_team"));
    const order = Object.keys(counts).sort(function (left, right) {
        return counts[right] - counts[left];
    });
    renderDashboardBars(target, counts, order.slice(0, 8), alerts.length, function (value) {
        return value;
    });
}

function renderDashboardBars(target, counts, order, total, labelFunction) {
    if (!total) {
        target.append($("<div>").addClass("overview-empty").text(i18n.t("overview.empty.no_data")));
        return;
    }

    order.forEach(function (key) {
        const count = counts[key] || 0;
        if (!count) {
            return;
        }
        const percent = Math.round((count / total) * 100);
        target.append(
            $("<div>")
                .addClass("overview-bar-row")
                .append(
                    $("<div>")
                        .addClass("overview-bar-meta")
                        .append($("<span>").text(labelFunction(key)))
                        .append($("<span>").text(count))
                )
                .append(
                    $("<div>")
                        .addClass("overview-bar-track")
                        .append(
                            $("<div>")
                                .addClass("overview-bar-fill")
                                .attr("style", "width: " + percent + "%;")
                        )
                )
        );
    });
}

function renderDashboardSystemStatus(alerts, activeAlerts) {
    const target = $("#dashboard-system-status");
    const firing = activeAlerts.filter(function (alert) {
        return alert.status === "firing";
    }).length;

    if (!alerts.length) {
        target.text(i18n.t("overview.system.no_alerts"));
        return;
    }
    if (firing > 0) {
        target.text(i18n.t("overview.system.firing", {count: firing}));
        return;
    }
    if (activeAlerts.length > 0) {
        target.text(i18n.t("overview.system.acknowledged", {count: activeAlerts.length}));
        return;
    }
    target.text(i18n.t("overview.system.resolved"));
}

$(document).on("click", "#reload-dashboard", function () {
    fetchDashboardData();
});
function dashboardSelectedTeamQuery() {
    if (typeof selectedTeamId === "function" && selectedTeamId()) {
        return "?team_id=" + encodeURIComponent(selectedTeamId());
    }

    return "";
}

function dashboardDisplayName(name, slug, fallback) {
    return name || slug || fallback || "-";
}

function dashboardImpactStatusRank(status) {
    const ranks = {
        disabled: 0,
        operational: 1,
        unknown: 2,
        maintenance: 3,
        degraded: 4,
        partial_outage: 5,
        major_outage: 6,
    };

    return ranks[status || "unknown"] || 0;
}

function dashboardImpactStatusLabel(status) {
    const normalized = String(status || "unknown").toLowerCase();
    const key = "overview.impact.status." + normalized;
    return i18n.t(key, {}, normalized.replace(/_/g, " "));
}

function dashboardImpactStatusCssClass(status) {
    const normalized = status || "unknown";

    return {
        major_outage: "impact-status-major",
        partial_outage: "impact-status-partial",
        degraded: "impact-status-degraded",
        maintenance: "impact-status-maintenance",
        operational: "impact-status-operational",
        disabled: "impact-status-neutral",
        unknown: "impact-status-neutral"
    }[normalized] || "impact-status-neutral";
}

function dashboardImpactBadge(status) {
    const normalized = status || "unknown";

    return $("<span>")
        .addClass("status-pill impact-status-pill")
        .addClass(dashboardImpactStatusCssClass(normalized))
        .text(dashboardImpactStatusLabel(normalized));
}

function dashboardImpactIssueRootCause(issue) {
    return dashboardDisplayName(
        issue.root_cause_service_name,
        issue.root_cause_service_slug,
        issue.root_cause_service_display
    );
}

function dashboardImpactIssuePath(issue) {
    const path = dashboardAsArray(issue.path);

    if (!path.length) {
        return dashboardDisplayName(
            issue.service_name,
            issue.service_slug,
            issue.service_display
        );
    }

    return path.map(function (node) {
        return dashboardDisplayName(
            node.service_name,
            node.service_slug,
            node.service_display
        );
    }).join(" → ");
}

function dashboardBestImpactIssue(row) {
    const issues = dashboardImpactIssues(row);

    if (!issues.length) {
        return null;
    }

    return issues
        .slice()
        .sort(function (left, right) {
            return dashboardImpactStatusRank(right.impact_status || right.status)
                - dashboardImpactStatusRank(left.impact_status || left.status);
        })[0];
}

function dashboardImpactRows(rows) {
    return dashboardAsArray(rows)
        .filter(function (row) {
            return row.effective_status
                && row.effective_status !== "operational"
                && row.effective_status !== "disabled";
        })
        .sort(function (left, right) {
            return dashboardImpactStatusRank(right.effective_status)
                - dashboardImpactStatusRank(left.effective_status)
                || Number(right.critical_open_alerts || 0) - Number(left.critical_open_alerts || 0)
                || Number(right.open_alerts || 0) - Number(left.open_alerts || 0);
        });
}

function dashboardFilterImpactRows(rows) {
    /*
     * Service impact follows the team and service filters only; involvement
     * describes personal work, not service health.
     */
    const teamIds = dashboardEffectiveTeamIds().map(String);
    const serviceIds = dashboardFilters.serviceIds.map(String);

    return dashboardAsArray(rows).filter(function (row) {
        if (teamIds.length && teamIds.indexOf(String(row.team_id)) === -1) {
            return false;
        }

        return !serviceIds.length || serviceIds.indexOf(String(row.service_id)) !== -1;
    });
}

function loadDashboardServiceImpact(sequence) {
    apiGet("/api/services/impact" + dashboardSelectedTeamQuery(), function (rows) {
        if (sequence !== undefined && sequence !== dashboardLoadSequence) {
            return;
        }

        renderDashboardServiceImpact(dashboardImpactRows(dashboardFilterImpactRows(rows)));
    });
}

function renderDashboardServiceImpact(rows) {
    const target = $("#dashboard-service-impact");
    target.empty();

    $("#dashboard-impacted-services-count").text(rows.length);

    if (!rows.length) {
        target.append(
            $("<div>")
                .addClass("overview-empty")
                .text(i18n.t("overview.impact.none"))
        );
        return;
    }

    rows.slice(0, 5).forEach(function (row) {
        target.append(renderDashboardServiceImpactItem(row));
    });

    if (rows.length > 5) {
        target.append(
            $("<button>")
                .attr("type", "button")
                .addClass("overview-list-item overview-list-button dashboard-impact-more")
                .text(i18n.t("overview.impact.more", {count: rows.length - 5}))
                .on("click", function () {
                    navigate("/services", true);
                })
        );
    }
}

function renderDashboardServiceImpactItem(row) {
    const serviceName = dashboardDisplayName(
        row.service_name,
        row.service_slug,
        i18n.t("overview.impact.service_number", {id: row.service_id})
    );

    const teamName = dashboardDisplayName(row.team_name, row.team_slug);
    const issue = dashboardBestImpactIssue(row);

    const item = $("<button>")
        .attr("type", "button")
        .addClass("overview-list-item overview-list-button dashboard-impact-item")
        .addClass(
            "dashboard-impact-item-"
            + String(row.effective_status || "unknown").replace(/_/g, "-")
        )
        .on("click", function () {
            navigate("/services", true);
        });

    item.append(
        $("<span>")
            .addClass("overview-list-dot")
            .addClass("overview-dot-" + String(row.effective_status || "unknown").replace(/_/g, "-"))
    );

    const main = $("<div>").addClass("list-main");

    main.append(
        $("<div>")
            .addClass("overview-list-title dashboard-impact-title")
            .text(serviceName)
    );

    const subtitleParts = [
        teamName,
        i18n.t("overview.impact.open", {count: Number(row.open_alert_groups || 0)}),
    ];

    if (Number(row.critical_open_alert_groups || 0) > 0) {
        subtitleParts.push(i18n.t("overview.impact.critical", {count: Number(row.critical_open_alert_groups || 0)}));
    }

    main.append(
        $("<div>")
            .addClass("list-subtitle")
            .text(subtitleParts.join(" · "))
    );

    if (issue) {
        main.append(
            $("<div>")
                .addClass("dashboard-impact-root")
                .text(i18n.t("overview.impact.root_cause", {
                    root: dashboardImpactIssueRootCause(issue),
                    path: dashboardImpactIssuePath(issue),
                }))
        );
    } else if (dashboardHasAlertImpact(row)) {
        main.append(
            $("<div>")
                .addClass("dashboard-impact-root")
                .text(i18n.t("overview.impact.caused_by_alerts"))
        );
    }

    item.append(main);

    item.append(
        $("<div>")
            .addClass("dashboard-impact-status")
            .append(dashboardImpactBadge(row.effective_status))
    );

    return item;
}
function dashboardHasAlertImpact(row) {
    return !!(
        row &&
        row.alert_impact_status &&
        row.alert_impact_status !== "operational"
    );
}


function dashboardImpactIssues(row) {
    const rootCauses = dashboardAsArray(row.root_causes);
    const paths = row.explanation ? dashboardAsArray(row.explanation.paths) : [];

    if (paths.length) {
        return paths.map(function (path, index) {
            const nodes = dashboardNormalizeImpactPath(path);
            const rootCause = dashboardFindRootCauseForPath(rootCauses, nodes) ||
                rootCauses[index] ||
                rootCauses[0] ||
                {};

            return dashboardBuildImpactIssue(row, rootCause, nodes, index);
        });
    }

    return rootCauses.map(function (rootCause, index) {
        const path = dashboardNormalizeImpactPath(rootCause.path);

        return dashboardBuildImpactIssue(
            row,
            rootCause,
            path.length ? path : [dashboardRootCauseToPathNode(rootCause)],
            index
        );
    });
}


function dashboardNormalizeImpactPath(path) {
    return dashboardAsArray(path).map(function (node) {
        node = node || {};

        return {
            service_id: node.service_id,
            service_name: node.service_name,
            service_slug: node.service_slug,
            service_display: node.service_name || node.service_slug,
            status: node.effective_status || node.status || "unknown",
            dependency_type: node.dependency_type || null,
            criticality: node.dependency_criticality || node.criticality || null,
        };
    });
}


function dashboardFindRootCauseForPath(rootCauses, nodes) {
    if (!rootCauses.length || !nodes.length) {
        return null;
    }

    const lastNode = nodes[nodes.length - 1];

    return rootCauses.find(function (cause) {
        return Number(cause.service_id) === Number(lastNode.service_id);
    }) || null;
}


function dashboardRootCauseToPathNode(rootCause) {
    rootCause = rootCause || {};

    return {
        service_id: rootCause.service_id,
        service_name: rootCause.service_name,
        service_slug: rootCause.service_slug,
        service_display: rootCause.service_name || rootCause.service_slug,
        status: rootCause.effective_status || rootCause.status || "unknown",
        dependency_type: null,
        criticality: null,
    };
}


function dashboardBuildImpactIssue(row, rootCause, path, index) {
    path = dashboardNormalizeImpactPath(path);
    rootCause = rootCause || {};

    const directNode = path.length ? path[0] : dashboardRootCauseToPathNode(rootCause);

    return {
        service_id: directNode.service_id || rootCause.service_id || row.service_id,
        service_name: directNode.service_name || rootCause.service_name || row.service_name,
        service_slug: directNode.service_slug || rootCause.service_slug || row.service_slug,
        service_display: directNode.service_display ||
            directNode.service_name ||
            directNode.service_slug ||
            rootCause.service_name ||
            rootCause.service_slug,

        status: rootCause.effective_status ||
            rootCause.status ||
            directNode.status ||
            row.effective_status ||
            "unknown",

        impact_status: row.dependency_impact_status ||
            row.effective_status ||
            "unknown",

        dependency_type: directNode.dependency_type || "dependency",
        criticality: directNode.criticality || "important",

        root_cause_service_id: rootCause.service_id || directNode.service_id || row.service_id,
        root_cause_service_name: rootCause.service_name || directNode.service_name || row.service_name,
        root_cause_service_slug: rootCause.service_slug || directNode.service_slug || row.service_slug,
        root_cause_service_display: rootCause.service_name || rootCause.service_slug,

        path: path,
        depth: Math.max(path.length - 1, 0),

        cycle_detected: !!row.cycle_detected,
        depth_limited: !!row.depth_limited,
        contributes_to_impact: row.primary_reason !== "none",

        description: row.explanation ? row.explanation.message : null,
        _index: index,
    };
}
