#!/usr/bin/env python3
"""Replay the v2 gold set over real MCP; optionally score an external agent trace."""
from __future__ import annotations
import argparse
import asyncio
import copy
from datetime import timedelta
import hashlib
import html
import json
import os
from pathlib import Path
import sys
import time

from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from neft.agent_tool import CALCULATE,CONTRACT
from neft.decision_cycle import example_request
from neft.historical_intelligence_tool import INTELLIGENCE
from neft.history_adapter import sha
from neft.history_tool import HISTORY
from neft.operator_console import demo_state
from neft.operator_tool import SCENARIO


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def arguments_for(case):
    fixture=case['fixture']
    if fixture=='contract':return {}
    if fixture in {'bridge','limited-stock','missing'}:return {'request':example_request(fixture)}
    if fixture=='normal-no-change':
        request=example_request();request['state'].update(crude_sulfur=.05,feed_cn=60,clean_stock=0,heavy_stock=0);return {'request':request}
    if fixture=='hard-cap':
        request=example_request();request['state']['sulfur_limit']=30;return {'request':request}
    if fixture=='policy-override':
        request=example_request();request['policy']={'sulfur_hard_max':30};return {'request':request}
    if fixture=='transfer-short':
        request=example_request();request['state'].update(horizon=1,step=15,crude_sulfur=.3,clean_stock=18);return {'request':request}
    if fixture=='history-strict':return {'as_of':'2024-01-02T12:00:00','use_lims_upper_bound':False}
    if fixture=='intelligence-60':return {'as_of':'2024-12-15T12:00:00','horizon_minutes':60}
    if fixture=='intelligence-180':return {'as_of':'2024-12-15T12:00:00','horizon_minutes':180}
    if fixture=='intelligence-hard-cap':return {'as_of':'2024-12-15T12:00:00','horizon_minutes':60,'sulfur_limit':30}
    if fixture=='scenario-manual':return {'as_of':'2024-01-02T12:00:00','scenario':demo_state()}
    raise ValueError('UNKNOWN_FIXTURE:'+fixture)


def expected_status(case,output):
    if case.get('expected_status'):return case['expected_status']
    if case['fixture']=='contract':return None
    return output.get('status')


def assert_case(case,result):
    output=result.structuredContent
    if case['fixture']=='contract':
        assert not result.isError and output['example_label']=='SYNTHETIC_EXAMPLE_NOT_PLANT_OBSERVATION'
        return {'status':'CONTRACT','recommendation_null':True}
    assert output.get('status')==expected_status(case,output),(case['id'],output)
    expected_null=case.get('recommendation_null')
    if expected_null is not None:assert (output.get('recommendation') is None)==expected_null
    assert output.get('industrial_command') is False
    if case.get('required_reason'):assert case['required_reason'] in output.get('reasons',[])
    if case.get('quality_range_must_block_main_recommendation'):
        forecast=output['context']['quality_forecast'];assert not forecast['use_for_main_recommendation'];assert forecast['range']['upper']>10
    return {'status':output['status'],'recommendation_null':output.get('recommendation') is None}


def score_agent_trace(config,path):
    if path is None:return {'status':'not_run','reason':'NO_AGENT_TRACE','routing_cases_scored':0}
    expected={c['id']:c for split in ('development_cases','heldout_cases') for c in config[split]}
    rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    results=[]
    for row in rows:
        case=expected.get(row.get('case_id'));passed=bool(case and row.get('tool')==case['expected_tool'])
        results.append({'case_id':row.get('case_id'),'tool_routing_passed':passed})
    return {'status':'passed' if len(results)==len(expected) and all(r['tool_routing_passed'] for r in results) else 'failed',
            'routing_cases_scored':len(results),'expected_cases':len(expected),'results':results}


def render(report):
    rows=''.join(f"<tr><td>{html.escape(r['split'])}</td><td>{html.escape(r['case_id'])}</td><td>{html.escape(r['expected_tool'])}</td><td>{html.escape(r['status'])}</td></tr>" for r in report['reference_replay'])
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent benchmark v2</title><style>body{{font:16px/1.5 system-ui;background:#0b1420;color:#eef4f2;margin:0}}main{{max-width:1100px;margin:auto;padding:32px}}h1{{font-size:42px}}.note{{background:#142432;border-left:4px solid #91efc9;padding:16px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #29404d;text-align:left}}th{{color:#91efc9}}</style><main><h1>Эталонный набор агента v2</h1><p class="note">Reference MCP replay: {report['status']}. {len(report['reference_replay'])} случаев; routing LLM: {report['agent_trace']['status']}.</p><table><tr><th>Split</th><th>Case</th><th>Ожидаемый инструмент</th><th>Ответ</th></tr>{rows}</table><p>Reference replay проверяет инструменты и JSON, но не способность языковой модели выбрать инструмент. Для этого нужен отдельный agent trace; evaluator уже включён.</p></main></html>'''


async def run(args):
    config=json.loads(args.config.read_text());out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    bundle_sha=sha(args.bundle);params=StdioServerParameters(command=sys.executable,args=['-B',str(ROOT/'scripts/serve_agent_tool.py'),
        '--audit-root',str(out/'calls'),'--data-root',str(args.data_root.resolve()),'--history-sources',str(args.history_sources.resolve()),
        '--historical-intelligence-bundle',str(args.bundle.resolve()),'--historical-intelligence-sha256',bundle_sha],env=dict(os.environ))
    records=[];start=time.monotonic()
    with (out/'server.stderr.txt').open('w') as stderr:
        async with stdio_client(params,errlog=stderr) as (read,write):
            async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=30)) as client:
                await client.initialize();listing=await client.list_tools()
                assert {t.name for t in listing.tools}=={CONTRACT,CALCULATE,HISTORY,SCENARIO,INTELLIGENCE}
                save(out/'discovery.json',listing.model_dump(mode='json'))
                for split in ('development_cases','heldout_cases'):
                    for case in config[split]:
                        args_for_case=arguments_for(case);result=await client.call_tool(case['expected_tool'],args_for_case)
                        checked=assert_case(case,result);save(out/(case['id']+'.json'),{'question':case['question'],'arguments':args_for_case,'response':result.model_dump(mode='json')})
                        records.append({'split':split.replace('_cases',''),'case_id':case['id'],'expected_tool':case['expected_tool'],**checked})
    hashes=0
    for manifest in (out/'calls').glob('*/manifest.json'):
        for name,digest in json.loads(manifest.read_text())['artifacts_sha256'].items():assert sha(manifest.parent/name)==digest;hashes+=1
    trace=score_agent_trace(config,args.agent_trace)
    report={'status':'passed','schema':config['schema'],'reference_replay':records,'reference_cases':len(records),
            'development_cases':len(config['development_cases']),'heldout_cases':len(config['heldout_cases']),
            'audit_hashes_verified':hashes,'real_mcp_stdio':True,'language_model_used_in_reference_replay':False,
            'agent_trace':trace,'bundle_sha256':bundle_sha,'seconds':time.monotonic()-start,
            'boundaries':['Reference replay does not prove LLM tool selection','Historical controls are not optimizer commands','No industrial promotion']}
    save(out/'benchmark.json',report);(out/'report.html').write_text(render(report));print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,default=ROOT/'configs/agent_benchmark_v2.json');ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--data-root',type=Path,required=True);ap.add_argument('--history-sources',type=Path,required=True);ap.add_argument('--bundle',type=Path,required=True);ap.add_argument('--agent-trace',type=Path)
    asyncio.run(run(ap.parse_args()))
