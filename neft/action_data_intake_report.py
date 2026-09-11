"""Self-contained Russian HTML report for action-data intake."""
import html
import json


def _table(headers, rows):
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join("<tr>" + "".join(
        f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render(inventory, readiness, lims_profile):
    found = inventory["status"] == "READY_COMMAND_SOURCE"
    title = "Журнал команд найден" if found else "Журнал команд пока не найден"
    file_rows = [[item["relative_path"], item["classification"],
                  ", ".join(item["headers"]), ", ".join(item["missing_columns"])]
                 for item in inventory["files"]]
    cadence = "не проверена"
    cadence_rows = []
    if lims_profile:
        median = lims_profile["median_interval_hours"]
        cadence = (f'{lims_profile["samples"]} проб; медианный интервал ' +
                   (f'{median:.2f} ч' if median is not None else 'не вычисляется'))
        cadence_rows = [[f"≤{hours} ч", value["count"],
                         (f'{100 * value["fraction"]:.1f}%'
                          if value["fraction"] is not None else "—")]
                        for hours, value in lims_profile["short_interval_counts"].items()]
    missing = "".join(f"<li>{html.escape(item)}</li>" for item in readiness["missing"])
    contract = html.escape(json.dumps({
        "columns": readiness["required_command_columns"],
        "minimum_request": readiness["minimum_request"],
    }, ensure_ascii=False, indent=2))
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Нефтекод · приёмка action data</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f7f6;color:#153f47;font:16px/1.55 system-ui}}
main{{max-width:1120px;margin:auto;padding:28px}}h1{{font-size:34px;line-height:1.15}}
.card{{background:#fff;border:1px solid #d4e2e0;border-radius:14px;padding:22px;margin:20px 0}}
.warn{{border-left:5px solid #b9782f;background:#fff5e6}}.ok{{border-left:5px solid #287e68}}
.pill{{display:inline-block;padding:5px 10px;border-radius:999px;background:#dbeae7;font-weight:700}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border-bottom:1px solid #dce7e5;text-align:left;vertical-align:top}}
pre{{white-space:pre-wrap;background:#102f36;color:#eaf7f4;padding:16px;border-radius:10px}}
a{{color:#176b75}}.muted{{color:#5c777c;font-size:13px}}
</style><main><p class="pill">{html.escape(readiness["status"])}</p><h1>{title}</h1>
<p>Doctor просмотрел {inventory["scanned_files"]} файла(ов) только по заголовкам. Данные строк не декодировались.</p>
<section class="card {'ok' if found else 'warn'}"><h2>Решение</h2>
<p><b>Готовность к fit: {'да' if readiness['ready_for_action_outcome_fit'] else 'нет'}.</b>
 Выполнено {readiness['model_fits']} fit; числа holdout 2025 и sealed 2026 не читались.</p><ul>{missing}</ul></section>
<section class="card"><h2>Файлы в папке</h2>{_table(['Файл','Класс','Заголовки','Не хватает'], file_rows)}</section>
<section class="card"><h2>Частота независимых проб LIMS</h2><p>{cadence}.</p>
{_table(['Интервал','Количество','Доля'], cadence_rows) if cadence_rows else ''}
<p>По одной частоте нельзя подтвердить число пар в часовых окнах: это станет
измеримо только после появления recorded execution time. Наблюдаемые 24 часа
между пробами указывают, что для исследования 0–3 ч, вероятно, понадобится
более плотная независимая выборка.</p></section>
<section class="card"><h2>Что запросить</h2><p>Готовый текст лежит в <a href="data_request.md">data_request.md</a>.</p><pre>{contract}</pre></section>
<p class="muted">Артефакты: <a href="inventory.json">inventory.json</a> · <a href="readiness.json">readiness.json</a> · <a href="lims_profile.json">lims_profile.json</a> · <a href="manifest.json">manifest.json</a></p>
</main></html>'''
