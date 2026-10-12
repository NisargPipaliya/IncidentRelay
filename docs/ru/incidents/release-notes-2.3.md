---
title: Release Notes IncidentRelay 2.3
description: Breaking-релиз Incident Management Core и checklist для операторов.
---

# Release Notes IncidentRelay 2.3

IncidentRelay 2.3 выпускает первый production-slice Incident Management v2 и
намеренно меняет публичный API-контракт технических AlertGroups и операционных
Incidents.

## Основные изменения

- first-class operational `Incident`, независимый от lifecycle AlertGroup;
- явная граница `Alert -> AlertGroup -> Incident`;
- `/api/alert-groups` для технических групп алертов;
- `/api/incidents` только для операционных Incidents;
- независимые Create Alert Group и Create Incident workflows;
- priority, team, service и operational assignee Incident;
- независимое ручное переназначение AlertGroup и Incident;
- link/unlink Incident с AlertGroup;
- optimistic concurrency для изменений и lifecycle Incident;
- audit и Incident timeline для core mutations;
- отдельные UI для Alerts и Incidents;
- common filters Notification Policy по priority, severity, source и атрибутам service.

## Breaking API change

Старый AlertGroup-shaped контракт `/api/incidents` удалён. До обновления внешние
клиенты должны перенести технические операции на `/api/alert-groups`.
Runtime compatibility alias отсутствует.

Incident ID и AlertGroup ID независимы. Одинаковое числовое значение может
существовать в обеих таблицах и обозначать разные ресурсы.

Перед production-обновлением прочитайте
[Миграцию API и обновление IncidentRelay 2.3](api-migration-2.3.md).

## Миграция БД

Incident core migration добавляет:

```text
incident
incident_event
incident_alert_group_link
```

Существующие `alert_group` и дочерние `alert` сохраняются. Исторические Incidents
не создаются автоматически только из-за наличия AlertGroup.

Храните backup БД до завершения post-upgrade reconciliation. Downgrade Incident
core удаляет новые Incident tables и не является data-preserving rollback после
начала записи first-class Incident данных.

## Security и consistency hardening

2.3 проверяет team scope для Incident mutations и Incident/AlertGroup links.
AlertGroup вне доступного пользователю team scope нельзя связать через доступный
Incident.

Изменяемое состояние Incident использует row-version compare-and-swap.
PostgreSQL row locks сериализуют конкурирующие link attempts, поэтому один
AlertGroup не может одновременно получить активную связь с двумя открытыми
Incidents.

## Checklist обновления

1. Выполните `python manage.py migration-status` и проверьте все миграции 2.3.
2. Проверьте разные resource shapes `/api/alert-groups` и `/api/incidents`.
3. Убедитесь, что technical AlertGroup history и comments сохранены.
4. Создайте тестовый Incident и убедитесь, что AlertGroup не создаётся неявно.
5. Создайте manual AlertGroup и убедитесь, что создаётся один initial child Alert.
6. Проверьте link/unlink и team-scope permissions.
7. Запустите полный suite на СУБД, используемых production.
8. Для PostgreSQL отдельно выполните `pytest -q tests/postgresql`.

## Что остаётся на следующие релизы

2.3 не закрывает весь Incident Management v2 roadmap. Flapping/reopen,
расширенная Incident collaboration, merge/split, role-aware automation, ITSM,
analytics и postmortems остаются в staged workstreams 2.4-2.10.
