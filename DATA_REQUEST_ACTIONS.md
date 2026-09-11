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
