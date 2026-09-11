"""HTML rendering for action collection coverage gaps."""
import html


def _table(headers, rows):
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>"
                                     for value in row) + "</tr>" for row in rows)
    return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def render(result):
    rows = []
    for control, item in result["controls"].items():
        for lag in item["bins"]:
            rows.append([control, f'{lag["bin_hours"][0]}–{lag["bin_hours"][1]}',
                         item["isolated_executed"], item["command_shortfall"],
                         item["directions"]["up"], item["directions"]["down"],
                         lag["pairs"], lag["pair_shortfall"], len(lag["quarters"]),
                         lag["quarter_shortfall"]])
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Нефтекод · сбор action data</title><style>
body{{margin:0;background:#f3f7f6;color:#153f47;font:16px/1.55 system-ui}}main{{max-width:1120px;margin:auto;padding:28px}}
.pill{{display:inline-block;padding:5px 10px;border-radius:999px;background:#ead9bd;font-weight:700}}section{{background:#fff;border:1px solid #d4e2e0;border-radius:14px;padding:22px;margin:20px 0}}
.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border-bottom:1px solid #dce7e5;text-align:left}}a{{color:#176b75}}
</style><main><p class="pill">{html.escape(result['status'])}</p><h1>Покрытие команд и проб</h1>
<p>Fit:0. Holdout2025 и sealed2026 не прочитаны.</p><section>{_table(
['Control','Лаг,ч','Изолир.команды','Не хватает','Up','Down','Пары','Не хватает пар','Кварталы','Не хватает кварталов'], rows)}</section>
<p><a href="coverage.json">JSON</a> · <a href="gaps.md">Точный список дефицитов</a> · <a href="manifest.json">Manifest</a></p></main></html>'''
