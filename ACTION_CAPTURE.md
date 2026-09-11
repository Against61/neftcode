# Локальный приёмник action events

Приёмник записывает фактические события P8/T11/F19 и независимые пробы LIMS.
Он не рассчитывает и не исполняет управляющие команды. Каталог `--store`
принадлежит серверу и содержит append-only `events.jsonl`, canonical
`commands.csv`, `quality_samples.csv` и текущий `status.json`.

## Запуск

```bash
python scripts/action_capture.py --store output/action-capture init
python scripts/serve_action_capture.py --store output/action-capture
```

Сервер слушает только loopback. Контракт доступен в
`GET http://127.0.0.1:8770/api/v1/contract`, состояние и точные дефициты — в
`GET http://127.0.0.1:8770/api/v1/status`.

## Запись команды

Сначала передаётся source-recorded событие выдачи:

```bash
curl -X POST http://127.0.0.1:8770/api/v1/commands/issued \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id":"dcs-issue-0001",
    "command_id":"command-0001",
    "control":"P8",
    "issued_time":"2024-03-15T10:00:00",
    "value_before":300.0,
    "unit":"degC",
    "source_system":"DCS"
  }'
```

После source-recorded подтверждения исполнения:

```bash
curl -X POST http://127.0.0.1:8770/api/v1/commands/terminal \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id":"dcs-execution-0001",
    "command_id":"command-0001",
    "status":"executed",
    "executed_time":"2024-03-15T10:05:00",
    "value_after":302.0,
    "source_system":"DCS"
  }'
```

Допустимы `executed`, `cancelled`, `failed`. Для двух последних статусов
`executed_time` и значения исполнения не передаются. Terminal может прийти
раньше issue: он будет виден как orphan и не попадёт в CSV до полной пары.

## Запись независимой пробы

```bash
curl -X POST http://127.0.0.1:8770/api/v1/quality-samples \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id":"lims-event-0001",
    "sample_id":"sample-0001",
    "sample_time":"2024-03-15T10:35:00",
    "available_time":"2024-03-15T12:10:00",
    "value_numeric":8.4,
    "unit":"mg/kg",
    "quality_status":"valid",
    "source_system":"LIMS",
    "source_record_id":"lims-row-0001"
  }'
```

`sample_time` — время отбора/reference time. `available_time` можно не
передавать, если в источнике его нет; приёмник его не выводит и онлайн-признак
не разрешает. ПАК этим API не принимается.

Для файловой интеграции тот же JSON принимает CLI:

```bash
python scripts/action_capture.py --store output/action-capture record \
  --type quality_sample --input event.json
```

## Повторы и целостность

Точный повтор `event_id` возвращает `idempotent: true`. Тот же `event_id` с
другим содержимым, повтор command/sample id, неправильная единица или время
отвергаются без изменения файлов. События связаны SHA-256 цепочкой, а CSV
пересобираются атомарно.

Текущие события 2026+ сохраняются и отмечаются как sealed. EXP-0019 и coverage
их не читают; раскрытие будущего периода требует нового зарегистрированного
эксперимента.

Когда train-покрытие станет достаточным, canonical файлы передаются в основной
конвейер:

```bash
python scripts/run_action_pipeline.py \
  --data-root output/action-capture \
  --quality-source output/action-capture/quality_samples.csv
```

Воспроизводимая проверка журнала, replay и materialization на явно
синтетических событиях:

```bash
python scripts/check_action_capture_protocol.py \
  --output output/action-capture-protocol
```

Независимая проверка SHA, hash chain, canonical CSV и semantic gates:

```bash
python scripts/check_action_capture_artifact.py \
  --run output/action-capture-protocol \
  --output output/action-capture-protocol-qa
```

## Импорт существующей train-части LIMS

Зарегистрированную пару CQ:CR исходного XLSX можно пакетно загрузить в store:

```bash
python scripts/import_lims_to_action_capture.py \
  --lims /path/to/ЛИМСы.xlsx \
  --store output/action-capture \
  --output output/action-capture-lims-seed
```

Импортёр читает только sample time 2023–2024 и останавливается на первом
timestamp 2025 до чтения его числового значения. `available_time` остаётся
пустым. SHA файла, sheet, row и value column входят в детерминированные ids;
повторный запуск не создаёт новых событий. Исходный XLSX не изменяется.

Проверить frozen import artifact можно отдельно:

```bash
python scripts/check_action_capture_lims_seed.py \
  --run output/action-capture-lims-seed \
  --output output/action-capture-lims-seed-qa
```

Если есть ранее построенная `quality.sqlite`, параметр `--reference-sqlite`
добавляет точное сравнение timestamp, value и source row.
