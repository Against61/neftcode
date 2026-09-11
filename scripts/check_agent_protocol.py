"""Real MCP client/server transcript. No LLM or production data involved."""
import argparse
import asyncio
import copy
from datetime import timedelta
import hashlib
import html
import json
from pathlib import Path
import sys
import time
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from neft.decision_cycle import run_cycle,example_request
from neft.agent_tool import CONTRACT,CALCULATE,sha


def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def render_report(result):
    rows=[]
    for item in result['cases']:
        status=html.escape(item['status'])
        details=html.escape(item.get('details') or '—')
        call=item.get('call_id')
        evidence=(f'<a href="calls/{html.escape(call)}/response.json">response</a> · '
                  f'<a href="calls/{html.escape(call)}/manifest.json">audit</a>') if call else 'protocol rejection'
        rows.append(f'<tr><td>{html.escape(item["case"])}</td><td>{html.escape(item["group"])}</td>'
                    f'<td><code>{status}</code></td><td>{details}</td><td>{evidence}</td></tr>')
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Neftecode — конкурсный прогон</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;color:#173038;background:#f4f8f7;margin:0}}main{{max-width:1120px;margin:auto;padding:32px}}
h1,h2{{line-height:1.15}}.hero,.card{{background:white;border:1px solid #cbdcda;border-radius:14px;padding:22px;margin:16px 0}}
.hero{{background:#123f47;color:white}}.ok{{color:#87efc0;font-weight:700}}code{{background:#e8f1ef;padding:2px 5px;border-radius:4px}}table{{width:100%;border-collapse:collapse;background:white}}
th,td{{padding:10px;border-bottom:1px solid #dce8e6;text-align:left;vertical-align:top}}th{{background:#dfecea}}a{{color:#087584}}ol{{padding-left:22px}}small{{color:#4d666b}}</style></head><body><main>
<section class="hero"><h1>Neftecode: проверяемый агент-рекомендатель</h1><p class="ok">MCP protocol: {html.escape(result['status'].upper())}</p>
<p>Детерминированный прогон без производственных данных и без LLM проверяет тот же JSON/MCP-контракт, который использует агент.</p></section>
<section class="card"><h2>Контур решения</h2><ol><li>Агент получает контракт и формирует сценарий.</li><li>Python рассчитывает варианты АВТ → ГТ → смешение.</li>
<li>Независимый gate проверяет исходный заказ, серу ≤10 мг/кг и полную сетку неопределённости.</li><li>Инструмент возвращает рекомендацию, NO_CHANGE или объяснимый отказ и сохраняет аудит.</li></ol>
<p><strong>Полномочия:</strong> только модельная рекомендация; запись уставок в оборудование отсутствует.</p></section>
<section><h2>Сценарии</h2><table><thead><tr><th>Сценарий</th><th>Группа</th><th>Результат</th><th>Смысл</th><th>Доказательства</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section>
<section class="card"><h2>Проверено</h2><p>{len(result['cases'])} MCP-вызовов/отказов; {result['audit_artifact_hashes_verified']} SHA-256 артефактов; ответы совпали с прямым Python API.</p>
<p>Реальные времена команд и причинный промышленный эффект не требуются для этого конкурсного прогона. Их сбор реализован как отдельное будущее расширение.</p>
<small>LLM в этом прогоне не вызывается: так проверка воспроизводима офлайн. Опциональный реальный агент запускается через scripts/run_agent_demo.py.</small></section>
</main></body></html>'''


async def main(args):
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    start=time.monotonic();records=[]
    parameters=StdioServerParameters(command=sys.executable,args=['-B',str(ROOT/'scripts/serve_agent_tool.py'),'--audit-root',str(out/'calls')])
    with (out/'server.stderr.txt').open('w') as err:
        async with stdio_client(parameters,errlog=err) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=30)) as client:
                init=await client.initialize();listing=await client.list_tools()
                assert {t.name for t in listing.tools}=={CONTRACT,CALCULATE}
                save(out/'discovery.json',{'initialize':init.model_dump(mode='json'),'tools':listing.model_dump(mode='json')})
                contract=await client.call_tool(CONTRACT,{})
                assert not contract.isError
                assert contract.structuredContent['example_label']=='SYNTHETIC_EXAMPLE_NOT_PLANT_OBSERVATION'
                save(out/'contract_response.json',contract.model_dump(mode='json'))
                cases=[(name,example_request(name),'baseline') for name in ('base','bridge','limited-stock','no-clean','sulfur-rise','cetane','missing')]
                r=example_request();r['state'].update(crude_sulfur=.05,feed_cn=60,clean_stock=0,heavy_stock=0)
                cases.append(('normal-no-change',r,'decision'))
                # Registered transfer cases: not used to implement the adapter or alter the evaluator.
                for name,changes in [('transfer-short',{'horizon':1,'step':15,'crude_sulfur':.3,'clean_stock':18}),
                                     ('transfer-hourly',{'horizon':2,'step':60,'crude_sulfur':.55,'clean_stock':33,'demand':70}),
                                     ('transfer-low-s',{'horizon':1.5,'step':30,'crude_sulfur':.2,'clean_stock':9,'feed_cn':55})]:
                    r=example_request();r['state'].update(changes);cases.append((name,r,'transfer'))
                r=example_request();r['state']['sulfur_limit']=30;cases.append(('hard-cap',r,'adversarial'))
                r=example_request();r['policy']={'sulfur_hard_max':30};cases.append(('policy-override',r,'adversarial'))
                r=example_request();r.update(bindings={'crude_sulfur':'crude_sulfur'},use_lims_upper_bound=True,observations=[{'id':'lab','source':'lims','field':'crude_sulfur','unit':'mass_percent','value':.4,'event_time':r['origin'],'provenance':'synthetic boundary test'}]);cases.append(('unavailable-lims',r,'temporal'))
                for name,request,group in cases:
                    before=time.monotonic();result=await client.call_tool(CALCULATE,{'request':request});elapsed=time.monotonic()-before
                    assert not result.isError,(name,result)
                    answer=result.structuredContent;direct=json.loads(json.dumps(run_cycle(request),allow_nan=False))
                    actual=json.loads(Path(answer['artifacts']['decision_json']).read_text())
                    assert actual==direct,name
                    assert answer['status']==direct['status'] and answer['recommendation']==direct['recommendation'],name
                    assert answer['industrial_command'] is False
                    save(out/(name+'.json'),{'arguments':{'request':request},'response':result.model_dump(mode='json')})
                    plan=answer.get('recommendation') or {}
                    if answer['status'] in ('MODEL_PLAN','NO_CHANGE'):
                        command=plan['command']
                        details=(f"P8={command['temperature']:g}, F19={command['pressure']:g}, "
                                 f"T11={command['feed']:g}; выпуск={plan['delivered_mass']:g}")
                    else:
                        details=', '.join(answer.get('reasons') or [])
                    records.append({'case':name,'group':group,'status':answer['status'],'call_id':answer['call_id'],
                                    'details':details,'seconds':elapsed,'matches_direct_api':True})
                for name,arg in [('extra-argument',{'request':example_request(),'timeout':999}),('missing-request',{})]:
                    result=await client.call_tool(CALCULATE,arg);assert result.isError and result.structuredContent['recommendation'] is None
                    save(out/(name+'.json'),result.model_dump(mode='json'))
                    records.append({'case':name,'group':'protocol_rejection','status':'TOOL_ERROR','details':'invalid MCP arguments rejected'})
                result=await client.call_tool('write_file',{'path':'not-permitted'});assert result.isError
                save(out/'unknown-tool.json',result.model_dump(mode='json'))
    count=0
    for manifest in (out/'calls').glob('*/manifest.json'):
        m=json.loads(manifest.read_text())
        for name,h in m['artifacts_sha256'].items():assert sha(manifest.parent/name)==h;count+=1
    result={'status':'passed','cases':records,'audit_artifact_hashes_verified':count,'tool_discovery':True,
            'stdio_client_server':True,'language_model_used':False,'seconds':time.monotonic()-start,
            'model_fits':0,'production_rows_read':0}
    save(out/'protocol_qa.json',result)
    (out/'report.html').write_text(render_report(result))
    print(json.dumps({**result,'report':str((out/'report.html').resolve())},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);asyncio.run(main(ap.parse_args()))
