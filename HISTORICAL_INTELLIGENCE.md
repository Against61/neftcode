# Исторический интеллект агента

Компонент строит три вида read-only контекста по истории ГТ:

- прогноз серы продукта на 15, 30, 60, 120 и 180 минут;
- исторически типичные будущие значения P8/T11/F19;
- пять похожих периодов с временем, расстоянием, наблюдавшейся серой и
  значениями P8/T11/F19.

Прогноз P8/T11/F19 описывает историю телеметрии. Это не оптимальная команда и
не подтверждение фактического действия оператора. Единицы этих трёх каналов в
архиве не подтверждены для передачи в сценарный оптимизатор, поэтому числа не
конвертируются. ПАК не читается.

## Обучение из локальных файлов

Нужны Python 3.12 и зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-historical.txt
```

Сначала создайте hash-pinned конфигурацию файлов по
[инструкции адаптера](HISTORY_ADAPTER.md), затем обучите компонент:

```bash
python scripts/configure_local_data.py \
  --data-root /path/to/archive \
  --output history_sources.local.json

python scripts/train_historical_intelligence.py \
  --data-root /path/to/archive \
  --sources history_sources.local.json \
  --output output/historical-intelligence
```

Команда читает HT CSV и зарегистрированную пару ЛИМС CQ:CR. Для текущего
протокола используются только 2023–2024: fit до 1 июля 2024, calibration в
III квартале, закрытый holdout в IV квартале. Числа после границы 2025 года не
разбираются. ЛИМС служит target по времени пробы. В baseline последняя проба
допускается только по явно зарегистрированному консервативному правилу
`sample_time + 4h`; фактическая публикация остаётся неизвестной.

Результат содержит `input_audit.json`, подготовленные источники, метрики,
selection, holdout-предсказания, HTML-отчёт, bundle и manifest с SHA-256.
Bundle находится в `output/historical-intelligence/model/bundle.joblib`, его
хеш — в корневом `manifest.json`.

## Подключение к агенту

```bash
python scripts/agent_connection.py \
  --data-root /path/to/archive \
  --history-sources history_sources.local.json \
  --historical-intelligence-bundle output/historical-intelligence/model/bundle.joblib \
  --historical-intelligence-sha256 SHA256_ИЗ_MANIFEST \
  --launch
```

Добавляется MCP-инструмент `get_refinery_historical_intelligence`. Агент может
передать только `as_of`, один из пяти горизонтов и предел серы не выше
10 мг/кг. Пути и bundle задаёт оператор, bundle проверяется по SHA перед
каждым вызовом. Ответ всегда имеет `recommendation=null` и
`industrial_command=false`. Исторический прогноз может поддержать основную
рекомендацию только если весь возвращённый диапазон укладывается в предел.

## Проверенный результат

EXP-0020 использовал 792 независимые пробы и 105 264 строки телеметрии
2023–2024. На holdout 2024Q4 HGB победил baseline последнего допустимого ЛИМС
на всех горизонтах: MAE улучшилась на 15,8–28,2%. Эмпирический 90% диапазон
покрыл 89,2–93,1% holdout. Точечный прогноз пропустил 10 событий выше
10 мг/кг, поэтому основная рекомендация проверяет верхнюю границу диапазона;
при таком gate небезопасных пропусков было 0.

Прогноз типичных controls прошёл порог только для F19 на пяти горизонтах.
Для P8 и T11 выбран честный `NO_CHANGE`; общий control-компонент отклонён
как 5/15 вместо требуемых 9/15. Пять аналогов доступны как диагностическое
обоснование без причинных выводов.

## Эталонный набор агента

```bash
python scripts/run_agent_benchmark_v2.py \
  --output output/agent-benchmark-v2 \
  --data-root /path/to/archive \
  --history-sources history_sources.local.json \
  --bundle output/historical-intelligence/model/bundle.joblib
```

Набор содержит 8 development и 5 heldout случаев: контракт, `MODEL_PLAN`,
`NO_CHANGE`, отказы по данным/запасам, запрет лимита 30 мг/кг, строгую историю
и исторический контекст. Reference replay запускает настоящий MCP client/server
и проверяет JSON и аудит. Выбор инструмента языковой моделью оценивается
отдельным `--agent-trace` и не приписывается этому replay.
