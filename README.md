# Neftecode: модельный помощник оператора

Репозиторий содержит воспроизводимый прототип расчётного цикла
АВТ → гидроочистка → смешение. Программа принимает состояние в JSON,
перебирает планы на конечной сетке модельных допущений, независимо проверяет
ограничения и возвращает основную рекомендацию либо явный отказ.

К тому же расчёту можно подключить LLM-агента через локальный MCP-сервер.
Агент формирует JSON и объясняет ответ, а численный расчёт, обязательный предел
товарной серы `≤10 мг/кг` и финальный допуск остаются в Python.

Это демонстрационная модель на синтетических входах. Она не подключена к АСУ ТП,
не записывает уставки и не доказывает промышленный или причинный эффект.
Исторический режим намеренно работает только на чтение и возвращает
`ABSTAIN_HISTORICAL_SCOPE`, пока не создан проверенный адаптер данных.

## Быстрый запуск

Требуется Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-cycle.txt
python scripts/run_decision_cycle.py --preset bridge --output output/bridge
```

Результат появится в `output/bridge/decision.json`, а карточка для просмотра —
в `output/bridge/report.html`. Каталог результата не перезаписывается.

Основной Python API:

```python
from neft.decision_cycle import example_request, run_cycle

request = example_request("bridge")
decision = run_cycle(request)
print(decision["status"])
print(decision["recommendation"])
```

Доступные примеры: `base`, `bridge`, `limited-stock`, `no-clean`,
`sulfur-rise`, `cetane`, `missing`. Полный контракт входа описан в
[PYTHON_CYCLE.md](PYTHON_CYCLE.md).

## MCP-инструмент для агента

```bash
python -m pip install -r requirements-agent.txt
python scripts/agent_connection.py --format codex
python scripts/agent_connection.py --launch
```

Сервер предоставляет два инструмента:

- `get_refinery_contract` возвращает схему и явно синтетический пример;
- `recommend_refinery_plan` принимает полный JSON и запускает тот же Python-цикл
  в ограниченном дочернем процессе.

Каждый вызов сохраняет вход, ответ, HTML-карточку и SHA-256-манифест в
серверном каталоге аудита. Агент не может менять модель, обязательный предел,
таймаут, команду запуска или путь журнала через аргументы инструмента.
Подробнее: [AGENT_TOOL.md](AGENT_TOOL.md).

## Проверка

```bash
python -m unittest -v \
  test_agent_tool \
  test_decision_cycle \
  test_expert_contracts \
  test_scenario_sensitivity

python scripts/check_agent_protocol.py --output /tmp/neft-protocol
python scripts/check_agent_raw_protocol.py --output /tmp/neft-raw-protocol
```

Набор включает 51 тест: контракт времени лабораторных данных, неизвестную
доступность ПАК, конечность JSON, ограничения процесса, независимую проверку
планов, отказ при `sulfur_limit=30`, ошибки и таймаут MCP.

## Границы данных

В репозитории нет производственных CSV/XLSX, обученных моделей, результатов
закрытых периодов, внутренних отчётов или журналов реальных агентских сессий.
Числовые коэффициенты и диапазоны в `configs/` относятся к иллюстративной
модели и не являются утверждёнными промышленными уставками.
