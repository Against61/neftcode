"""Static operator card rendered from the canonical Python decision JSON."""
import html
import json


def esc(x): return html.escape(str(x),quote=True)

def page(title,body):
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+esc(title)+'''</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;overflow-wrap:anywhere;background:#f3f6f2;color:#1b3037;font:16px/1.55 system-ui,sans-serif}main{max-width:1120px;margin:auto;padding:40px 24px 70px}h1{font-size:clamp(30px,5vw,48px);line-height:1.15;letter-spacing:-1px}h2{margin:34px 0 16px;font-size:25px}h3{font-size:18px}a{color:#00675e;overflow-wrap:anywhere}.eyebrow{color:#566b70;font-size:12px;letter-spacing:1.1px;text-transform:uppercase}.panel{background:white;border:1px solid #cedbd6;padding:22px;border-radius:10px;margin:16px 0}.status{background:#e1eee6;border-left:5px solid #247451;padding:20px}.refusal{background:#fff0e5;border-color:#b9542d}.conditional{background:#fff6d7;border-left:5px solid #947215;padding:18px}.muted,small{color:#566b70}.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:10px;text-align:left;border-bottom:1px solid #dce4df;vertical-align:top}th{background:#edf2ee}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:450px;overflow:auto;background:#eef2ef;padding:14px}summary{cursor:pointer;color:#00675e;font-weight:600}details{margin:14px 0}.pills{display:flex;flex-wrap:wrap;gap:10px}.pills span{background:white;border:1px solid #cfddd5;padding:10px 16px;border-radius:6px}.actions{display:flex;gap:18px;flex-wrap:wrap;margin:20px 0}footer{border-top:1px solid #cedbd6;margin-top:35px;padding-top:20px;color:#566b70}code{overflow-wrap:anywhere}@media(max-width:640px){main{padding:24px 14px 50px}.panel{padding:16px}td,th{padding:8px}h1{letter-spacing:-.5px}}
</style><main>'''+body+'''</main></html>'''


def plan_table(plan):
    if plan is None:return '<p>Допустимого плана нет.</p>'
    rows=''
    for t in plan['timeline']:
        r=t['recipe'];q=t['quality']
        rows+=f'<tr><td>{t["start"]:g}–{t["end"]:g}</td><td>{r["temperature"]:g} / {r["pressure"]:g} / {r["feed"]:g}</td><td>{r["clean"]*100:g} / {r["heavy"]*100:g} / {r["additive"]*100:g}</td><td>{q["sulfur_max"]:.3f}</td><td>{q["t95_max"]:.2f}</td><td>{q["cn_min"]:.2f}</td><td>{t["mass"]:g}</td><td>{t["stocks_after"]["clean"]:g} / {t["stocks_after"]["heavy"]:g}</td></tr>'
    return f'<p>Команда ГТ: {plan["command"]["temperature"]:g} °C / {plan["command"]["pressure"]:g} МПа / {plan["command"]["feed"]:g} т/ч. Выпуск: <b>{plan["delivered_mass"]:g} т</b>; недопоставка исходного заказа: <b>{plan["unmet_original_mass"]:g} т</b>. Верхняя оценка затрат: <b>{plan["cost_upper"]:.6f}</b> относительных единиц.</p><div class="table"><table><thead><tr><th>Интервал, ч</th><th>Команда ГТ<br>°C / МПа / т/ч</th><th>Доли, %<br>чистый / тяжёлый / присадка</th><th>Сера max,<br>мг/кг</th><th>T95 max,<br>°C</th><th>Цетан min</th><th>Партия, т</th><th>Остатки, т<br>чистый / тяжёлый</th></tr></thead><tbody>{rows}</tbody></table></div><small>Указаны худшие модельные показатели. Дополнительно применены запасы по качеству: сера +0,5 мг/кг, T95 +1 °C, цетан −0,5. Свежий поток ГТ дополняет доли до 100%.</small>'


def render_decision(d,json_name='decision.json',title='Цикл решения на Python'):
    status=d['status'];bad=status.startswith(('INVALID','ABSTAIN'))
    body=f'<div class="eyebrow">Нефтекод / канонический Python-цикл / модельная демонстрация</div><h1>{esc(title)}</h1><div class="status {"refusal" if bad else ""}"><b>{esc(status)}</b><p>{esc(d.get("explanation") or "Расчёт рекомендации остановлен по входному контракту.")}</p>'
    if d.get('reasons'):body+='<ul>'+''.join('<li>'+esc(r)+'</li>' for r in d['reasons'])+'</ul>'
    body+='</div><div class="actions"><a href="'+esc(json_name)+'">Полный JSON решения</a><a href="#roles">Обмен ролей</a><a href="#inputs">Вход и доступность</a></div>'
    body+='<p>Это расчёт собственной модели. Команды оборудованию не выдаются. Полный набор задержек 0–3 часа — основное основание решения; быстрый отклик не установлен производственными данными.</p>'
    body+='<h2>Решение для исходного заказа</h2>'+plan_table(d.get('recommendation'))
    full=d.get('full')
    if full:
        baseline=full['current_plan'];best=full['primary']['dynamic'];static=full['primary']['stationary']
        body+='<div class="panel"><h3>С чем сравниваем</h3><p>Текущий модельный режим: 340 °C / 4 МПа / 100 т/ч; исходный рецепт — только свежий поток ГТ. Стоимость — сумма худших стоимостей партий, а не рублёвая экономия.</p><ul>'
        for label,p in [('Текущий полный рецепт',baseline),('Лучший динамический план',best),('Лучший постоянный рецепт',static)]:
            body+='<li>'+label+': '+(f'{p["cost_upper"]:.6f}' if p else 'недопустим для исходного заказа')+'</li>'
        body+='</ul><small>NO_CHANGE: текущий рецепт допустим, а относительный разрыв с оптимумом не превышает 1e−6. Это политика демонстрации.</small></div>'
    conditional=d.get('delay_le_1')
    if conditional:
        body+='<h2>Отдельный условный результат</h2><div class="conditional"><b>Только если задержка ≤1 часа</b><p>Это дополнительное допущение; оно не заменяет основное решение и не подтверждено EXP-0018.</p></div>'+plan_table(conditional['primary']['dynamic'])
        savings=conditional['primary']['saving_fraction']
        if savings is not None:body+=f'<p>Снижение верхней оценки затрат относительно лучшего постоянного рецепта: {100*savings:.4f}% при одинаковых заказе и ресурсах.</p>'
        deferred=next((f for f in conditional['frontier'] if f['start']>0 and f['dynamic']),None)
        if conditional['primary']['dynamic'] is None and deferred:
            body+='<details><summary>Диагностика переноса: исходный заказ не выполнен</summary>'+plan_table(deferred['dynamic'])+'</details>'
    body+='<h2 id="inputs">Вход и доступность</h2>'
    state_message=next((m['output'] for m in d.get('trace',[]) if m['role']=='state'),None)
    if state_message:
        body+=f'<p>Время решения: {esc(d["input"].get("origin"))}. Шкала — общее локальное время источников; UTC-смещение неизвестно.</p><p>Выбрано наблюдений: {len(state_message["selected"])}; исключено: {len(state_message["excluded"])}; расхождений между источниками: {len(state_message["conflicts"])}.</p>'
        body+='<details><summary>Происхождение, возраст, исключения и приоритет источников</summary><pre>'+esc(json.dumps(state_message,ensure_ascii=False,indent=2))+'</pre></details>'
    body+='<details><summary>Доступные входы модельного расчёта</summary><pre>'+esc(json.dumps(d.get('model_input'),ensure_ascii=False,indent=2))+'</pre></details>'
    body+='<h2 id="roles">Обмен ролей</h2><p>Каждое сообщение ссылается на одно состояние и сообщения, от которых оно зависит. Оптимизатор предлагает план; отдельная проверка пересчитывает его во всех 972 или 486 сочетаниях.</p>'
    names={'state':'Состояние и доступность','quality':'Качество и производственные ограничения','reliability':'Нагрузка оборудования','optimizer':'Выбор плана','gate':'Независимая проверка плана','decision':'Итоговое решение'}
    for m in d.get('trace',[]):
        body+='<details class="role"><summary>'+str(m['sequence']+1)+'. '+names[m['role']]+' '+esc(m['output'].get('scope',''))+'</summary><p class="muted">Сообщение '+esc(m['message_id'][:12])+' · состояние '+esc(m['state_id'][:12])+'</p><pre>'+esc(json.dumps(m,ensure_ascii=False,indent=2))+'</pre></details>'
    body+='<footer>Фиксированный модельный выход АВТ, одна команда ГТ, конечные запасы без пополнения, условная кинетика и прокси стоимости/нагрузки. Историческая точность прогнозов и эффект управления оцениваются раздельно. Карточка создана Python из того же результата, что сохранён в JSON.</footer>'
    return page(title,body)
