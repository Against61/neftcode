# Сбор данных для проверки эффекта команд

Пакет превращает запрос данных в проверяемый план. Система не выдаёт команды:
фиксируются только штатные действия, уже разрешённые производственным процессом,
и независимые лабораторные пробы.

## Создать комплект

```bash
python scripts/build_action_collection_kit.py \
  --source-root /path/to/source-package \
  --output output/action-collection-kit
```

Комплект содержит:

- `commands_template.csv` — точный журнал P8/T11/F19;
- `quality_samples_template.csv` — независимые пробы;
- `collection_plan.csv` — 60 train и 30 holdout слотов;
- `empty_coverage/` — исходные дефициты по каждому control и lag bin;
- `source_inventory.json` и SHA manifest.

Quality CSV имеет точный заголовок:

```csv
sample_id,sample_time,available_time,value_numeric,unit,quality_status,source_system,source_record_id
```

`sample_time` — reference time отбора. `available_time` можно оставить пустым;
оно никогда не выводится из sample time и не используется как онлайн-признак.

## Минимальное покрытие

Train 2023–2024: 20 изолированных исполненных команд на каждый P8/T11/F19,
не менее 5 каждого направления, минимум 10 независимых пар в каждом окне
0–1/1–2/2–3 ч и три разных квартала. Для 30 полностью наблюдаемых train-действий
нужно минимум 120 проб: baseline и три post-bin.

Holdout — исторический 2025 после прохождения train-gate: минимум 10 действий
на control и 60 проб после заморозки выбранного лага. Данные 2026+ этим
протоколом не открываются и требуют нового заранее зарегистрированного split.

## Проверить заполнение

```bash
python scripts/check_action_collection.py \
  --commands commands.csv \
  --quality-samples quality_samples.csv \
  --output output/coverage
```

Validator выполняет 0 fit и выдаёт точные shortfalls. После готовности canonical
CSV передаются в `scripts/run_action_pipeline.py`; тот же quality CSV уже
поддерживается strict action→outcome reader.
