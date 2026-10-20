---
title: Миграция API и обновление IncidentRelay 2.3
description: Breaking-разделение AlertGroup/Incident, проверка миграции БД и граница rollback для IncidentRelay 2.3.
---

# Миграция API и обновление IncidentRelay 2.3

IncidentRelay 2.3 намеренно разделяет техническую обработку алертов и
операционную работу с инцидентами:

```text
Alert -> AlertGroup -> Incident
```

Bundled UI, OpenAPI и backend переходят на новый контракт одновременно. Внешние
API-клиенты необходимо обновить до обновления приложения.

## Breaking-изменения endpoint'ов

| До 2.3 | Контракт 2.3 | Назначение |
|---|---|---|
| `/api/incidents` для технических групп алертов | `/api/alert-groups` | Группировка, ACK/resolve, дочерние alerts, notifications, escalation и technical comments |
| Независимого first-class Incident API не было | `/api/incidents` | Операционный lifecycle Incident, priority, service, assignee и связи с AlertGroup |

Совместимого alias для старого AlertGroup-контракта `/api/incidents` нет. Нельзя
считать, что Incident ID и AlertGroup ID обозначают одну запись. У таблиц
независимые пространства ID, поэтому одинаковое числовое значение допустимо.

## Семантика создания

`POST /api/alert-groups` атомарно создаёт технический AlertGroup и ровно один
начальный manual Alert.

`POST /api/incidents` создаёт только first-class operational Incident и не
создаёт Alert или AlertGroup неявно.

## Перед обновлением

1. Сделайте backup БД и конфигурации.
2. Остановите web, scheduler и worker процессы, чтобы старый код не выполнял
   записи во время смены схемы/API.
3. Зафиксируйте текущую версию и статус миграций:

   ```bash
   python manage.py migration-status
   ```

4. Переведите внешние API-клиенты со старых технических вызовов
   `/api/incidents` на `/api/alert-groups`.
5. Убедитесь, что пользователь БД может создавать и удалять таблицы.

## Применение миграций 2.3

Из одного процесса выполните:

```bash
python manage.py migrate
```

Core-миграция Incident создаёт таблицы:

```text
incident
incident_event
incident_alert_group_link
```

Она не переименовывает и не перестраивает `alert_group` и не создаёт
исторические Incidents автоматически. Техническая история AlertGroup и child
Alert остаётся в существующих таблицах.

После миграции снова выполните:

```bash
python manage.py migration-status
```

`20260917070000_incident_core` должна иметь статус applied.

## Проверка после обновления

Перед открытием трафика проверьте:

1. `/api/alert-groups` возвращает существующие технические группы.
2. `/api/incidents` возвращает только first-class Incidents.
3. Child-alert counts, status, technical assignee и comments существующих
   AlertGroups не изменились.
4. Создание Incident не создаёт AlertGroup.
5. Создание manual AlertGroup создаёт один начальный child Alert.
6. Incident можно связать/отвязать с AlertGroup без изменения технического
   lifecycle AlertGroup.
7. Team permissions не позволяют связывать AlertGroups вне доступного scope.

Для PostgreSQL также запустите PostgreSQL release tests, включая конкурентную
попытку связать один AlertGroup с двумя открытыми Incidents.

## Граница rollback

У core-миграции есть downgrade, который удаляет три новые таблицы:

Сначала выполните `python manage.py migration-status` и откатывайте все
применённые миграции после и включая `20260917070000_incident_core` в обратном
порядке. В текущем наборе миграций ветки 2.3 после Incident core идёт
`20260925070000_alert_text_fields`, поэтому для перехода через границу Incident
core требуется:

```bash
python manage.py rollback --count 2
```

Не копируйте это число вслепую после добавления новых миграций: вычисляйте его
по `migration-status` для конкретной сборки.

Но rollback безопасен только **до появления first-class Incident данных, которые
необходимо сохранить**. Downgrade удаляет Incident, Incident event и
Incident/AlertGroup link tables.

Поэтому:

- храните pre-upgrade backup до успешного завершения reconciliation;
- если rollback нужен после начала работы операторов с Incidents, сначала
  экспортируйте новые данные или восстановите подходящий backup;
- не считайте удаление новых таблиц data-preserving rollback после начала
  эксплуатации новой модели.

Технические данные AlertGroup не хранятся в этих трёх таблицах и downgrade core
Incident migration их не удаляет.

## Checklist миграции клиентов

Разделяйте тип ресурса и ID:

```text
technical alert-group ID -> /api/alert-groups/{id}
operational incident ID  -> /api/incidents/{id}
```

Не сохраняйте универсальный `incident_id` с последующим использованием в обоих
семействах endpoint'ов.

## Проверка релиза

Запустите обычный test suite на тех же СУБД, что используются production. Для
PostgreSQL каталог тестов запускается явно, потому что исключён из обычной
pytest-рекурсии:

```bash
pytest -q
pytest -q tests/postgresql
```

Также проверьте, что сгенерированный OpenAPI больше не содержит старый
AlertGroup-shaped контракт `/api/incidents`.
