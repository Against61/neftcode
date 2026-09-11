"""Real MCP client/server transcript. No LLM or production data involved."""
import argparse
import asyncio
import copy
from datetime import timedelta
import hashlib
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
                    records.append({'case':name,'group':group,'status':answer['status'],'call_id':answer['call_id'],'seconds':elapsed,'matches_direct_api':True})
                for name,arg in [('extra-argument',{'request':example_request(),'timeout':999}),('missing-request',{})]:
                    result=await client.call_tool(CALCULATE,arg);assert result.isError and result.structuredContent['recommendation'] is None
                    save(out/(name+'.json'),result.model_dump(mode='json'))
                    records.append({'case':name,'group':'protocol_rejection','status':'TOOL_ERROR'})
                result=await client.call_tool('write_file',{'path':'not-permitted'});assert result.isError
                save(out/'unknown-tool.json',result.model_dump(mode='json'))
    count=0
    for manifest in (out/'calls').glob('*/manifest.json'):
        m=json.loads(manifest.read_text())
        for name,h in m['artifacts_sha256'].items():assert sha(manifest.parent/name)==h;count+=1
    result={'status':'passed','cases':records,'audit_artifact_hashes_verified':count,'tool_discovery':True,
            'stdio_client_server':True,'language_model_used':False,'seconds':time.monotonic()-start,
            'model_fits':0,'production_rows_read':0}
    save(out/'protocol_qa.json',result);print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);asyncio.run(main(ap.parse_args()))
