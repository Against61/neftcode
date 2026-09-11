# Запрос данных для калибровки действий

Проверка структуры выполняется командой:

```bash
python scripts/check_action_data_readiness.py \
  --data-root /path/to/archive \
  --lims "/path/to/ЛИМСы.xlsx" \
  --output output/action-data-intake
```

Doctor рекурсивно ищет CSV/XLSX, читает только строки заголовков и принимает
журнал команд лишь при точном наборе колонок:

```text
command_id,control,issued_time,executed_time,value_before,value_after,unit,status,source_system
```

Полный готовый текст запроса формируется как `data_request.md`. Минимальный
объём и правила независимого связывания определены в
`configs/action_data_intake_v1.json`. Текущие HT/AVT CSV являются
телеметрией и не содержат подтверждений выдачи и исполнения команд.

После получения выгрузки выполнить полный intake→calibration путь:

```bash
python scripts/run_action_pipeline.py \
  --data-root /path/to/archive \
  --quality-source "/path/to/ЛИМСы.xlsx"
```

Первая версия автоматически исполняет только exact CSV. Найденный XLSX
останавливается с `COMMAND_FORMAT_NOT_EXECUTABLE`, чтобы преобразование не
меняло источник и не подменяло типы времени неявно.

Для более частых независимых проб принимается canonical CSV:

```text
sample_id,sample_time,available_time,value_numeric,unit,quality_status,source_system,source_record_id
```

`sample_time` — время отбора. `available_time` можно оставить пустым, но его
нельзя выводить из sample time. Готовые шаблоны и 90 слотов сбора создаёт:

```bash
python scripts/build_action_collection_kit.py \
  --source-root /path/to/source-package \
  --output output/action-collection-kit
```

Подробности: [ACTION_COLLECTION.md](ACTION_COLLECTION.md).
