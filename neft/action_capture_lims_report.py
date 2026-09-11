"""Standalone report for a train-only LIMS capture seed."""
from html import escape
import json


def render(result):
    audit = result["source_audit"]
    batch = result["batch"]
    status = result["capture"]
    controls = "".join(
        f"<tr><th>{escape(name)}</th><td>{item['isolated_executed']}</td>"
        f"<td>{item['command_shortfall']}</td><td>{sum(row['pairs'] for row in item['bins'])}</td></tr>"
        for name, item in status["coverage"]["controls"].items())
    raw = escape(json.dumps(result, ensure_ascii=False, indent=2))
    return f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>LIMS train seed</title><style>
body{{font:15px system-ui;margin:0;background:#f5f7fa;color:#18232d}}main{{max-width:1050px;margin:auto;padding:32px}}
.tag{{display:inline-block;background:#dff5e5;color:#175b2c;padding:5px 10px;border-radius:999px}}
.warning{{background:#fff3cd;border-left:4px solid #d39e00;padding:12px;margin:18px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card{{background:white;border:1px solid #dfe6ee;border-radius:10px;padding:15px}}.metric{{font-size:28px;font-weight:700}}
table{{width:100%;border-collapse:collapse;background:white}}th,td{{text-align:left;border:1px solid #dfe6ee;padding:9px}}
pre{{overflow:auto;background:#101820;color:#d9e2ec;padding:14px;border-radius:8px}}</style></head>
<body><main><span class='tag'>TRAIN-ONLY IMPORT: {escape(result['status'])}</span><h1>LIMS → action store</h1>
<p>Реальные лабораторные пробы Mg.Sulfur точки 2 ГТ, зарегистрированная пара CQ:CR.</p>
<div class='warning'><strong>Ограничение:</strong> это лабораторные observations без command log.
Action→outcome пары и эффект команд не установлены. `available_time` неизвестен.</div>
<div class='grid'><div class='card'><div class='metric'>{audit['valid_unique']}</div>valid unique train samples</div>
<div class='card'><div class='metric'>{batch['new_events']}</div>новых событий</div>
<div class='card'><div class='metric'>{result['replay']['idempotent_events']}</div>idempotent replay</div>
<div class='card'><div class='metric'>{status['complete_commands']}</div>команд</div></div>
<h2>Временная граница</h2><p>Reader остановился на <code>{escape(str(audit['stopping_timestamp_only']))}</code>;
later numeric parsed: <strong>{str(audit['later_numeric_values_parsed']).lower()}</strong>.</p>
<h2>Coverage</h2><table><thead><tr><th>Control</th><th>Исполнения</th><th>Не хватает команд</th><th>Связанные пары</th></tr></thead><tbody>{controls}</tbody></table>
<details><summary>Полный import JSON</summary><pre>{raw}</pre></details></main></body></html>"""
