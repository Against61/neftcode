# Пакет prediction-моделей

В `models/historical_intelligence_v1.joblib` лежит проверенный артефакт
EXP-0020 размером 4 395 780 байт (4,19 MiB). Это обычный Git-файл;
Git LFS не нужен. Его SHA-256 и ожидаемый runtime зафиксированы в
`models/manifest.json` и проверяются до загрузки joblib.

Пакет содержит:

- HGB-модели серы на горизонтах 15, 30, 60, 120 и 180 минут;
- HGB-модели исторически типичного F19 на пяти горизонтах;
- явный `NO_CHANGE` для P8 и T11, поскольку они не прошли calibration gate;
- эмпирические диапазоны неопределённости.

В пакете нет CSV/XLSX, матриц признаков, таргетов и временных меток
обучения. Индекс аналогов сознательно исключён. При его отсутствии MCP
возвращает явный `analogs_status`, а прогноз и controls продолжают работать.

На holdout 2024Q4 прогноз серы превзошёл baseline по MAE на 15,8–28,2%
на всех пяти горизонтах. После gate по верхней границе диапазона небезопасных
пропусков серы выше 10 мг/кг было 0. Control-компонент в целом отклонён:
порог прошли 5 из 15 пар, все они относятся к F19.

Запуск агента с моделями:

```bash
python -m pip install -r requirements-historical.txt
python scripts/agent_connection.py --data-root /path/to/archive --launch
```

Для повторной публичной упаковки после нового прошедшего holdout bundle:

```bash
python scripts/package_prediction_models.py \
  --bundle output/historical-intelligence/model/bundle.joblib \
  --output /tmp/model-package/historical_intelligence_v1.joblib \
  --manifest /tmp/model-package/manifest.json
```

Скрипт откажется упаковывать модель, если для какого-либо горизонта
выбран метод аналогов: такая модель без локального индекса неработоспособна.
