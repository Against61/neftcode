# Neftecode: локальный модельный помощник оператора

Репозиторий содержит воспроизводимый прототип расчётного цикла
АВТ → гидроочистка → смешение. Он умеет читать локальный исторический архив,
показывать состояние на выбранный момент и отдельно считать модельный сценарий
по полному набору явно заданных входов.

Python перебирает планы на конечной сетке допущений, независимо проверяет
ограничения и возвращает модельную рекомендацию либо явный отказ. Обязательный
предел товарной серы `≤10 мг/кг` нельзя ослабить через JSON или интерфейс.

Это демонстрационная модель. Она не подключена к АСУ ТП, не записывает уставки
и не доказывает промышленный или причинный эффект. Историческая телеметрия
остаётся диагностическим контекстом. Из архива к модели может привязываться
только входной T95 ЛИМС через явно включённую границу sample+4ч.

## Быстрый запуск модели

Требуется Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-cycle.txt
python scripts/run_decision_cycle.py --preset bridge --output output/bridge
```

Результат появится в `output/bridge/decision.json`, карточка — в
`output/bridge/report.html`. Полный контракт: [PYTHON_CYCLE.md](PYTHON_CYCLE.md).

## Локальная консоль с историей

Для архива нужны HT CSV `242000_tags.csv` и книга `ЛИМС*.xlsx`. Данные можно
хранить рядом с клоном или в отдельном каталоге; в Git они не добавляются.

```bash
python -m pip install -r requirements-agent.txt
python scripts/start_operator_console.py --data-root /path/to/archive
```

Откройте `http://127.0.0.1:8765`. Консоль слушает только loopback, сохраняет
аудит каждого вызова и не принимает файловые пути через браузер. Команда сама
создаёт hash-pinned локальную конфигурацию, выполняет doctor, затем запускает
сервер. Каждый HTTP-расчёт работает в отдельном процессе с таймаутом 20 с;
одновременно допускаются два запроса. Кнопка
демонстрационных допущений заполняет учебный пример явным действием; эти числа
не считаются архивными. Подробнее: [OPERATOR_CONSOLE.md](OPERATOR_CONSOLE.md)
и [HISTORY_ADAPTER.md](HISTORY_ADAPTER.md).

Проверить данные и runtime без запуска сервера:

```bash
python scripts/start_operator_console.py \
  --data-root /path/to/archive \
  --check-only
```

## MCP-инструменты для агента

```bash
python scripts/agent_connection.py --format codex
python scripts/agent_connection.py \
  --data-root /path/to/archive \
  --history-sources history_sources.local.json \
  --launch
```

Без архива сервер предоставляет два инструмента:

- `get_refinery_contract`;
- `recommend_refinery_plan`.

При операторской настройке архива добавляются:

- `get_refinery_history` — read-only срез и причины отказа;
- `evaluate_refinery_scenario_with_history` — отдельный модельный сценарий
  с provenance всех входов.

Агент не задаёт пути, модель, таймаут, обязательный предел или каталог аудита.
Расчёт запускается в ограниченном дочернем Python-процессе. Подробнее:
[AGENT_TOOL.md](AGENT_TOOL.md).

## Проверка

```bash
python -m unittest -v \
  test_agent_tool \
  test_decision_cycle \
  test_expert_contracts \
  test_scenario_sensitivity \
  test_history_adapter \
  test_operator_console \
  test_runtime_v5

python scripts/check_agent_protocol.py --output /tmp/neft-protocol
python scripts/check_agent_raw_protocol.py --output /tmp/neft-raw-protocol
python scripts/check_history_protocol.py --output /tmp/neft-history-protocol
python scripts/check_runtime_smoke.py
```

Набор включает 89 unit-тестов, настоящий MCP stdio-клиент и live HTTP smoke
изолированного runtime. Синтетические fixtures создаются самими тестами;
производственные CSV/XLSX для CI не нужны.

## Границы данных

В репозитории нет производственных CSV/XLSX, обученных моделей, внутренних
отчётов, закрытых периодов и журналов реальных агентских сессий. Локальные
`*.local.json`, данные, результаты и журналы исключены через `.gitignore`.

ПАК не используется: смысл его timestamp, время доступности и статусы
исправности/калибровки неизвестны. HT P8/T11/F19 показываются как контекст,
но их единицы и онлайн-доступность не подтверждены. `crude_sulfur`,
`crude_flow`, `feed_cn`, свойства/запасы резервуаров, заказ и спецификации
вводятся как явные модельные допущения. Числовые коэффициенты и диапазоны в
`configs/` не являются утверждёнными промышленными уставками.
