"""Local evidence card. Unavailable laboratory values are never rendered."""
from html import escape
from .cycle_report import page


def render_history(s):
    e = lambda v: escape('—' if v is None else str(v))
    status_rows = ''.join('<tr>' + ''.join('<td>' + e(v) + '</td>' for v in
        [r['field'], r['value'], r['unit'], r['event_time'], r['status'], ', '.join(r['reasons'])]) + '</tr>'
        for r in s['lims_series_status'])
    labs = ''.join('<tr>' + ''.join('<td>' + e(x) + '</td>' for x in
                 [r['field'], r['event_time'], r['value'], r['unit'],
                  r['availability']['eligibility_time'], r['value_cell'],
                  'Выбрано' if r['selected'] else ', '.join(r['reasons']) or 'Более ранняя проба']) + '</tr>'
                 for r in s['lims_selection_log'])
    telemetry = ''.join('<tr>' + ''.join('<td>' + e(x) + '</td>' for x in
                       [r['tag'], r['context']['description'] if r['context'] else '', r['archive_value'],
                        r['event_time'], r['source_row'], str(r['finite_grid_points']) + '/' + str(r['expected_grid_points']),
                        ', '.join(r['flags']) or 'Численные проверки пройдены']) + '</tr>'
                       for r in s['telemetry']['channels'])
    selected = len(s['selected_lims'])
    body = f'''<style>.table table{{min-width:850px}}.table td,.table th{{overflow-wrap:normal;word-break:normal}}</style>
<div class="eyebrow">Нефтекод / адаптер архива</div><h1>Состояние на {e(s['as_of'])}</h1>
<div class="pills"><span>22 канала HT</span><span>Допущено серий ЛИМС: {selected}/5</span><span>ПАК: доступность неизвестна</span></div>
<div class="status refusal"><strong>{e(s['decision']['status'])}</strong><p>Архив прочитан. Управляющая рекомендация отсутствует:
для полной модели ещё не определены все входы и соответствие исторического режима модели действий.</p></div>
<nav class="actions"><a href="history.json">Полное состояние JSON</a><a href="request.json">Запрос Python/MCP</a>
<a href="decision.json">Ответ цикла</a><a href="#laboratory">ЛИМС</a><a href="#telemetry">Телеметрия</a></nav>
<h2>Что нужно для модельного расчёта</h2><p>Недостающие поля: {e(', '.join(s['missing_model_fields']))}.</p>
<p>Параметры задания, введённые пользователем: {e(s['scenario_parameters'])}.</p>
<p>Сера входа ГТ не заменяет серу исходной нефти. P8/T11/F19 описывают режим ГТ;
единицы и доступность этих архивных отсчётов ещё не подтверждены. Выходные T95 и цетан не подставляются во входное сырьё.</p>
<h2 id="laboratory">Лабораторные пробы и доступность</h2>
<p>Правило sample + 4 ч: <strong>{'включено явно' if s['use_lims_upper_bound'] else 'выключено'}</strong>.
Это консервативная граница по сообщению эксперта; фактическое время публикации неизвестно.
Значения недоступных проб скрыты и не входят в запрос расчётного цикла.</p>
<div class="table"><table><thead><tr><th>Свойство</th><th>Выбранное значение</th><th>Единицы</th><th>Проба</th><th>Доступность</th><th>Причины</th></tr></thead><tbody>{status_rows}</tbody></table></div>
<details><summary>Журнал отбора проб: даты, ячейки и причины исключения</summary>
<div class="table"><table><thead><tr><th>Свойство</th><th>Проба</th><th>Значение</th><th>Единицы</th><th>Граница допуска</th><th>Ячейка</th><th>Результат</th></tr></thead><tbody>{labs}</tbody></table></div>
</details>
<h2 id="telemetry">Историческая диагностика HT</h2><p>Срез до {e(s['telemetry']['cutoff'])}; отступ 10 мин —
условие просмотра истории. Эти значения не объявлены доступными онлайн и не входят в признаки модели.</p>
<div class="table"><table><thead><tr><th>Тег</th><th>Подтверждённая роль</th><th>Архивное значение</th><th>Время</th><th>Строка CSV</th><th>Полнота</th><th>Замечания</th></tr></thead><tbody>{telemetry}</tbody></table></div>
<footer>ПАК исключён без чтения значений. Нового обучения и оценки прогнозов нет. Исходники доступны только на чтение.</footer>'''
    return page('Историческое состояние · ' + s['as_of'], body)
