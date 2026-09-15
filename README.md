# Neftecode: локальный модельный помощник оператора

Репозиторий содержит воспроизводимый прототип расчётного цикла
АВТ → гидроочистка → смешение. Он умеет читать локальный исторический архив,
показывать состояние на выбранный момент и отдельно считать модельный сценарий
по полному набору явно заданных входов.

Python перебирает планы на конечной сетке допущений, независимо проверяет
ограничения и возвращает модельную рекомендацию либо явный отказ. Обязательный
предел товарной серы `≤10 мг/кг` нельзя ослабить через JSON или интерфейс.
В репозитории есть готовый hash-pinned prediction package из EXP-0020:
прогноз серы на 15–180 минут и исторически типичные значения controls.

Это демонстрационная модель. Она не подключена к АСУ ТП, не записывает уставки
и не доказывает промышленный или причинный эффект. Историческая телеметрия
остаётся диагностическим контекстом. Из архива к модели может привязываться
только входной T95 ЛИМС через явно включённую границу sample+4ч.

**Конкурсная цель проекта — архитектура советующего агента.** Для её запуска
не нужны дополнительные производственные данные или журнал реальных команд.
Они относятся к будущей промышленной валидации. Полный маршрут и схема:
[COMPETITION_GUIDE.md](COMPETITION_GUIDE.md).

## Конкурсный прогон одной командой

После установки `requirements-agent.txt`:

```bash
python scripts/run_competition_demo.py --output output/competition-demo
```

Откройте `output/competition-demo/report.html`. Прогон использует настоящий
MCP client/server, проверяет план, `NO_CHANGE`, обоснованные отказы, жёсткий
предел серы и SHA-256 аудита. Он полностью офлайн и не требует LLM/API-ключа.

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

## Агент с готовыми prediction-моделями

После клонирования укажите только каталог, в котором лежат
`242000_tags.csv` и один `ЛИМС*.xlsx`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-historical.txt
python scripts/agent_connection.py --data-root /path/to/archive --launch
```

`agent_connection.py` сам находит два источника, создаёт локальный
hash-pinned конфиг в игнорируемом `output/`, проверяет SHA-256
модели из `models/manifest.json` и подключает пять MCP-инструментов.
Производственные CSV/XLSX в GitHub не нужны: они остаются read-only на машине
команды. Без архива конкурсный агент и офлайн-демо всё равно работают.
Состав, валидация и порядок пересборки: [MODEL_PACKAGE.md](MODEL_PACKAGE.md).

## Опциональное расширение: промышленная калибровка

Отдельный read-only контур связывает подтверждённые времена исполнения
P8/T11/F19 с независимыми пробами серы ЛИМС в окнах0–1/1–2/2–3ч.
Лаг и коэффициенты выбираются на2023–2024; числа2025 читаются только
после прохождения train-gate. Телеметрические переходы не считаются командами.
Этот контур не требуется для конкурсного запуска агентной архитектуры.

```bash
python scripts/run_action_outcome.py \
  --command-log /path/to/commands.csv \
  --quality-source "/path/to/ЛИМСы 01.01.2023 - н.в_ (2).xlsx"
```

Точный CSV-контракт, критерии и ПАК gate:
[ACTION_OUTCOME.md](ACTION_OUTCOME.md).

Сначала можно проверить новую папку данных без чтения строк неизвестных
CSV/XLSX:

```bash
python scripts/check_action_data_readiness.py \
  --data-root /path/to/archive \
  --lims "/path/to/ЛИМСы.xlsx" \
  --output output/action-data-intake
```

Doctor ищет точный заголовок журнала команд, отдельно профилирует только
train-часть LIMS и формирует `data_request.md` с недостающими полями и
минимальным покрытием. Подробнее: [DATA_REQUEST_ACTIONS.md](DATA_REQUEST_ACTIONS.md).

После получения новой выгрузки весь путь выполняется одной командой:

```bash
python scripts/run_action_pipeline.py \
  --data-root /path/to/archive \
  --quality-source "/path/to/ЛИМСы.xlsx"
```

Конвейер запускает калибровку только при одном exact command CSV. Отсутствие
источника, несколько кандидатов или XLSX дают проверяемый business refusal;
holdout остаётся закрытым до прохождения train-gate.

Для подготовки недостающих данных можно сразу собрать проверяемый комплект:

```bash
python scripts/build_action_collection_kit.py \
  --source-root /path/to/source-package \
  --output output/action-collection-kit
```

Он создаёт точные шаблоны команд и независимых проб, план на 90 действий
(60 train и 30 исторических holdout) и отчёт с дефицитами по каждому
P8/T11/F19, направлению и окну 0–1/1–2/2–3 ч. Заполненные canonical CSV
напрямую принимает основной action pipeline. Подробнее:
[ACTION_COLLECTION.md](ACTION_COLLECTION.md).

Для постоянной регистрации есть локальный observe-only API:

```bash
python scripts/serve_action_capture.py --store output/action-capture
```

Он принимает JSON-события выдачи и исполнения команд, отдельно пробы LIMS,
ведёт append-only журнал и после каждой записи обновляет canonical CSV и
coverage. Точные повторы идемпотентны; конфликтующие повторы отвергаются.
Подробнее: [ACTION_CAPTURE.md](ACTION_CAPTURE.md).

Существующую train-часть зарегистрированной пары LIMS CQ:CR можно загрузить
пакетно без чтения holdout2025:

```bash
python scripts/import_lims_to_action_capture.py \
  --lims /path/to/ЛИМСы.xlsx \
  --store output/action-capture \
  --output output/action-capture-lims-seed
```

## MCP-инструменты для агента

```bash
python scripts/agent_connection.py --format codex
python scripts/agent_connection.py \
  --data-root /path/to/archive \
  --launch
```

Без архива сервер предоставляет два инструмента:

- `get_refinery_contract`;
- `recommend_refinery_plan`.

При операторской настройке архива добавляются:

- `get_refinery_history` — read-only срез и причины отказа;
- `evaluate_refinery_scenario_with_history` — отдельный модельный сценарий
  с provenance всех входов.
- `get_refinery_historical_intelligence` — прогноз серы с диапазоном и
  исторически типичные P8/T11/F19; всегда без команды. Пять аналогов
  добавляются при локальной пересборке full bundle.

Агент не задаёт пути, модель, таймаут, обязательный предел или каталог аудита.
Расчёт запускается в ограниченном дочернем Python-процессе. Подробнее:
[AGENT_TOOL.md](AGENT_TOOL.md).

Чтобы обучить исторический компонент прямо из локальных HT CSV и ЛИМС XLSX,
следуйте [HISTORICAL_INTELLIGENCE.md](HISTORICAL_INTELLIGENCE.md). Состояние
всех 12 направлений собрано в [DEVELOPMENT_ROADMAP.md](DEVELOPMENT_ROADMAP.md).

## Проверка

```bash
python -m unittest -v \
  test_agent_tool \
  test_decision_cycle \
  test_expert_contracts \
  test_scenario_sensitivity \
  test_history_adapter \
  test_operator_console \
  test_runtime_v5 \
  test_action_outcome \
  test_action_data_intake \
  test_action_pipeline \
  test_action_collection \
  test_action_capture \
  test_historical_intelligence \
  test_historical_intelligence_tool \
  test_historical_training \
  test_model_package

python scripts/check_agent_protocol.py --output /tmp/neft-protocol
python scripts/check_agent_raw_protocol.py --output /tmp/neft-raw-protocol
python scripts/check_history_protocol.py --output /tmp/neft-history-protocol
python scripts/check_action_capture_protocol.py --output /tmp/neft-capture-protocol
python scripts/check_action_capture_artifact.py \
  --run /tmp/neft-capture-protocol --output /tmp/neft-capture-protocol-qa
python scripts/check_runtime_smoke.py
```

Набор включает 144 unit-теста, настоящий MCP stdio-клиент и live HTTP smoke
изолированного runtime. Синтетические fixtures создаются самими тестами;
производственные CSV/XLSX для CI не нужны.

## Границы данных

В репозитории нет производственных CSV/XLSX, строк и временных меток обучения,
внутренних отчётов, закрытых периодов и журналов реальных агентских сессий.
Есть один проверенный prediction-only bundle; он не содержит индекс
исторических аналогов. Локальные
`*.local.json`, данные, результаты и журналы исключены через `.gitignore`.
Локальная команда обучения может создать full bundle с аналогами из файлов команды
и оставляет его в исключённом каталоге `output/`.

ПАК не используется: смысл его timestamp, время доступности и статусы
исправности/калибровки неизвестны. HT P8/T11/F19 показываются как контекст,
но их единицы и онлайн-доступность не подтверждены. `crude_sulfur`,
`crude_flow`, `feed_cn`, свойства/запасы резервуаров, заказ и спецификации
вводятся как явные модельные допущения. Числовые коэффициенты и диапазоны в
`configs/` не являются утверждёнными промышленными уставками.
