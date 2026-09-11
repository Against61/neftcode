"""Small, self-contained report for the action/outcome calibration protocol."""
import html
import json


def _table(headers, rows):
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render(result, config, inventory):
    gate = result["readiness"]
    missing = gate["status"] == "MISSING_COMMAND_LOG"
    title = ("Нужен журнал фактических команд" if missing else
             "Калибровка прошла временной holdout" if result["decision"] == "accept" else
             "Калибровка не прошла зарегистрированные критерии")
    checklist = [
        ["1", "issued/executed P8/T11/F19",
         "ожидает источник" if missing else "проверено адаптером"],
        ["2", "Независимые пробы качества после execution",
         "не строились" if missing else "связаны без повторного sample"],
        ["3", "Фактический лаг 0–3 ч",
         "не оценён" if result["models"] is None else "выбран только на train"],
        ["4", "Коэффициенты и неопределённость",
         "0 fit" if result["models"] is None else f'{result["model_fits"]} fit'],
        ["5", "Временной holdout 2025",
         "численно не открыт" if not result["holdout_numeric_read"] else result["holdout"]["status"]],
        ["6", "ПАК",
         "исключён: метаданные не подтверждены" if not result["pac_gate"]["eligible"]
         else "допущен только к отдельной валидации"],
    ]
    source_rows = [
        [name, item["role"], "да" if item["sha256_verified"] else "нет",
         "да" if item["registered_as_command_log"] else "нет"]
        for name, item in inventory["registered_sources"].items()
    ]
    model_rows = []
    for control, model in (result.get("models") or {}).items():
        holdout = (result.get("holdout") or {}).get("controls", {}).get(control, {})
        model_rows.append([
            control, model["selected_bin_hours"], round(model["slope"], 6),
            [round(value, 6) for value in model["slope_interval_90"]],
            model["train_pairs"], holdout.get("events", "—"), holdout.get("passed", "—"),
        ])
    model_section = (_table(
        ["Control", "Лаг, ч", "Slope", "90% bootstrap", "Train pairs", "Holdout", "Gate"],
        model_rows) if model_rows else
        '<p class="notice"><b>Численные лаги и коэффициенты не публикуются.</b> '
        'Без source-recorded issued/executed times воздействие нельзя отличить от наблюдаемого изменения режима.</p>')
    contract = html.escape(json.dumps({
        "command_columns": inventory["required_command_columns"],
        "time_contract": config["time_contract"],
        "acceptance": config["holdout_acceptance"],
    }, ensure_ascii=False, indent=2))
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Нефтекод · EXP-0019</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f2f6f5;color:#173e47;font:16px/1.55 system-ui}}
main{{max-width:1120px;margin:auto;padding:28px}}h1{{font-size:34px;line-height:1.15}}h2{{font-size:22px}}
.card{{background:#fff;border:1px solid #d2e1df;border-radius:14px;padding:22px;margin:20px 0}}
.notice{{background:#fff0da;border-left:4px solid #b57932;padding:16px}}.muted{{color:#57747a;font-size:13px}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;text-align:left;border-bottom:1px solid #d7e2e0;vertical-align:top}}th{{background:#eaf2f0}}
a{{color:#08717a}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}@media(max-width:650px){{main{{padding:15px 11px}}.card{{padding:15px}}h1{{font-size:27px}}}}
</style><main><p class="muted">НЕФТЕКОД / EXP-0019 / ACTION→OUTCOME</p><h1>{html.escape(title)}</h1>
<p>Протокол готов принимать реальный журнал команд, связывать execution с ЛИМС-пробами и открывать 2025 только после достаточной опоры на 2023–2024. Текущий результат: <b>{html.escape(result["decision"].upper())}</b>.</p>
<section class="card"><h2>Шесть шагов</h2>{_table(["#", "Задача", "Фактический статус"], checklist)}</section>
<section class="card"><h2>Инвентаризация зарегистрированных источников</h2>{_table(["Источник", "Роль", "SHA совпал", "Command log"], source_rows)}
<p class="muted">Отсутствие command log относится к зарегистрированному файловому пакету. HT CSV остаётся диагностической телеметрией.</p></section>
<section class="card"><h2>Лаги, коэффициенты и holdout</h2>{model_section}</section>
<section class="card"><h2>Что нужно добавить</h2><p>Один CSV с уникальной строкой на команду: реальное время выдачи, подтверждённое время исполнения, уставка до/после, единица, статус и исходная система. После этого тот же frozen протокол сначала проверит train-support, затем выберет лаг и только потом прочитает holdout.</p>
<details><summary>Машиночитаемый контракт</summary><pre>{contract}</pre></details>
<p><a href="results.json">Результат</a> · <a href="decision.json">Решение</a> · <a href="source_inventory.json">Источники</a> · <a href="pac_gate.json">ПАК gate</a> · <a href="config.json">Протокол</a> · <a href="manifest.json">Манифест</a> · <a href="preregistration.md">Регистрация</a> · <a href="../../../wiki/experiments/EXP-0019-action-outcome-calibration.md">Память</a></p></section>
<footer class="muted">LIMS timestamp используется как reference/sample time результата. Он не считается временем доступности online feature. 2026 остаётся sealed.</footer></main></html>'''
