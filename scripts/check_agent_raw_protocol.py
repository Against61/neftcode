"""Verify raw malformed JSON never reaches an MCP calculation tool."""
import argparse
import json
from pathlib import Path
import select
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    with (out/'server.stderr.txt').open('w') as err:
        child=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/serve_agent_tool.py'),'--audit-root',str(out/'calls')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err)
        def send(obj):child.stdin.write((obj+'\n').encode() if isinstance(obj,str) else (json.dumps(obj)+'\n').encode());child.stdin.flush()
        def receive():
            assert select.select([child.stdout],[],[],10)[0],'Server response timeout'
            return json.loads(child.stdout.readline())
        records=[]
        try:
            send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'raw-contract-check','version':'1'}}})
            assert receive()['id']==1
            send({'jsonrpc':'2.0','method':'notifications/initialized'})
            bads=[('duplicate','{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"recommend_refinery_plan","arguments":{"request":{},"request":{}}}}'),
                  ('nonfinite','{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"recommend_refinery_plan","arguments":{"request":{"state":{"sulfur_limit":NaN}}}}}'),
                  ('oversize',json.dumps({'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'recommend_refinery_plan','arguments':{'request':{'x':'x'*280000}}}}))]
            for label,message in bads:
                send(message);reply=receive();assert reply.get('method')=='notifications/message' and reply['params']['level']=='error',(label,reply)
                records.append({'case':label,'response':reply})
            assert not list((out/'calls').glob('*/manifest.json')),'Rejected transport input reached tool'
            send({'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'get_refinery_contract','arguments':{}}})
            reply=receive();assert reply['id']==5 and not reply['result']['isError']
            errors=[json.loads(x) for x in (out/'calls/transport-errors.jsonl').read_text().splitlines()]
            assert [x['reason'] for x in errors]==['DUPLICATE_JSON_KEY','NONFINITE_JSON','PROTOCOL_REQUEST_TOO_LARGE']
            result={'status':'passed','rejections':records,'transport_errors':errors,'no_rejected_request_reached_tool':True,'server_recovers':True}
            (out/'raw_protocol_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result))
        finally:
            child.stdin.close()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)


if __name__=='__main__':main()
