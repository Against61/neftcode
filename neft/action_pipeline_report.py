"""Self-contained summary for the one-command action pipeline."""
import html


def render(result):
    blocked = not result["calibration_started"]
    title = "Конвейер остановлен до калибровки" if blocked else "Калибровка завершена"
    action_link = ("<li><a href='calibration/report.html'>Отчёт калибровки</a></li>"
                   if result["calibration_started"] else "")
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Нефтекод · action pipeline</title><style>
body{{margin:0;background:#f3f7f6;color:#153f47;font:16px/1.55 system-ui}}
main{{max-width:880px;margin:auto;padding:30px}}section{{background:#fff;border:1px solid #d5e3e1;border-radius:14px;padding:22px;margin:20px 0}}
.pill{{display:inline-block;background:#e9d8bd;padding:5px 10px;border-radius:999px;font-weight:700}}a{{color:#176b75}}
</style><main><p class="pill">{html.escape(result['stage'])}</p><h1>{title}</h1>
<section><p><b>Решение:</b> {html.escape(str(result['decision']))}</p>
<p><b>Причина:</b> {html.escape(str(result.get('reason') or 'см. отчёт калибровки'))}</p>
<p><b>Источник команд:</b> {html.escape(str(result.get('command_source') or 'не найден'))}</p>
<p>Fit: {result['model_fits']}; holdout прочитан: {'да' if result['holdout_numeric_read'] else 'нет'};
2026 прочитан: {'да' if result['sealed_2026_numeric_read'] else 'нет'}.</p></section>
<ul><li><a href="intake/report.html">Отчёт приёмки</a></li>{action_link}<li><a href="pipeline.json">Pipeline JSON</a></li><li><a href="manifest.json">Manifest</a></li></ul>
</main></html>'''
