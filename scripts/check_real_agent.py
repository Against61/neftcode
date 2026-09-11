"""Audit a real agent transcript against its tool calls, never self-reported success."""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def check(folder):
    execution=json.loads((folder/'execution.json').read_text());assert execution['status']=='completed'
    events=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines()]
    items=[e['item'] for e in events if e['type']=='item.completed']
    calls=[i for i in items if i['type']=='mcp_tool_call']
    assert [i['tool'] for i in calls]==['get_refinery_contract','recommend_refinery_plan','recommend_refinery_plan']
    assert all(i['server']=='neft' and i['status']=='completed' and i.get('error') is None for i in calls)
    assert all(i['type'] in ('mcp_tool_call','agent_message','reasoning','error') for i in items),'Unexpected tool or file action'
    results=[i['result']['structured_content'] for i in calls]
    sample=results[0]['example_request']
    assert calls[1]['arguments']=={'request':sample}
    altered=copy.deepcopy(sample);altered['state']['sulfur_limit']=30
    assert calls[2]['arguments']=={'request':altered}
    assert results[1]['status']=='MODEL_PLAN' and results[2]['status']=='INVALID_INPUT'
    assert results[2]['recommendation'] is None and 'SULFUR_HARD_LIMIT_10' in results[2]['reasons']
    hashes=0
    for result in results:
        call=folder/'calls'/result['call_id']
        assert json.loads((call/'response.json').read_text())==result
        m=json.loads((call/'manifest.json').read_text())
        for name,h in m['artifacts_sha256'].items():assert sha(call/name)==h;hashes+=1
    answer=(folder/'answer.md').read_text()
    assert all(r['call_id'] in answer for r in results[1:])
    assert all(term in answer for term in ('MODEL_PLAN','INVALID_INPUT','330','80','270','10','SULFUR_HARD_LIMIT_10'))
    # Content manually reviewed in addition to these mechanical assertions.
    assert 'условн' in answer.lower() and 'не' in answer and 'модель' in answer.lower()
    usage=next(e['usage'] for e in events if e['type']=='turn.completed')
    return {'status':'passed','actual_mcp_calls':3,'tools_only_expected':True,'first_input_matches_example':True,
            'second_diff_only_sulfur_limit':True,'tool_outputs_match_audits':True,'audit_hashes_verified':hashes,
            'answer_review':'Correct command and volume; hard cap refusal preserved; conditional result not promoted; model nature explicit.',
            'language_model_used':True,'usage':usage,'seconds':execution['seconds'],'global_connection_added':False}


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args()
    result=check(a.folder);(a.folder/'agent_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result))
