"""Small standalone report for the action-capture protocol artifact."""
from html import escape
import json


def render(protocol, status):
    rows = []
    for control, item in status["coverage"]["controls"].items():
        bins = ", ".join(
            f"{hours[0]}–{hours[1]} ч: −{row['pair_shortfall']} пар, "
            f"−{row['quarter_shortfall']} кварталов"
            for row in item["bins"] for hours in [row["bin_hours"]])
        rows.append(
            f"<tr><th>{escape(control)}</th><td>{item['isolated_executed']}</td>"
            f"<td>{item['command_shortfall']}</td><td>{escape(bins)}</td></tr>")
    checks = "".join(
        f"<li class='{('ok' if passed else 'bad')}'>{'✓' if passed else '✗'} "
        f"{escape(name.replace('_', ' '))}</li>"
        for name, passed in protocol["checks"].items())
    raw = escape(json.dumps(status, ensure_ascii=False, indent=2))
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Action capture protocol</title><style>
body{{font:15px system-ui;margin:0;background:#f5f7fa;color:#17202a}}
main{{max-width:1050px;margin:auto;padding:32px}}h1{{margin-bottom:6px}}
.tag{{display:inline-block;padding:5px 10px;border-radius:999px;background:#dff5e5;color:#175b2c}}
.warning{{background:#fff3cd;border-left:4px solid #d39e00;padding:12px;margin:18px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.card{{background:white;border:1px solid #dfe6ee;border-radius:10px;padding:15px}}
.metric{{font-size:28px;font-weight:700}}table{{width:100%;border-collapse:collapse;background:white}}
th,td{{text-align:left;border:1px solid #dfe6ee;padding:9px;vertical-align:top}}
ul{{columns:2;background:white;padding:18px 38px;border-radius:10px}}li{{margin:5px}}
.ok{{color:#175b2c}}.bad{{color:#8b1e1e}}details{{margin-top:18px}}pre{{overflow:auto;background:#101820;color:#d9e2ec;padding:14px;border-radius:8px}}
</style></head><body><main><span class='tag'>protocol: {'PASS' if protocol['passed'] else 'FAIL'}</span>
<h1>Приёмник action events</h1><p>Проверка append-only регистрации команд и независимых LIMS-проб.</p>
<div class='warning'><strong>{escape(protocol['fixture'])}</strong><br>
Этот запуск проверяет код и контракт. Он не содержит производственных наблюдений и не доказывает эффект команд.</div>
<div class='grid'><div class='card'><div class='metric'>{status['events']}</div>события</div>
<div class='card'><div class='metric'>{status['complete_commands']}</div>полные команды</div>
<div class='card'><div class='metric'>{status['quality_samples']}</div>пробы</div>
<div class='card'><div class='metric'>{status['sealed_2026_plus_events']}</div>sealed 2026+</div></div>
<h2>Train coverage</h2><p>Статус: <strong>{escape(status['coverage']['status'])}</strong>; model fits: 0.</p>
<table><thead><tr><th>Control</th><th>Изолированные исполнения</th><th>Не хватает команд</th><th>Дефициты по лагам</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Проверки</h2><ul>{checks}</ul>
<details><summary>Полный status JSON</summary><pre>{raw}</pre></details>
</main></body></html>"""
