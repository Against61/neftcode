"""Narrow, audited JSON tools around the unchanged Python decision cycle.

Agent arguments never select executable paths, model policy, timeout or audit
root. A child process bounds each calculation and every result gets its own ID.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/'configs/agent_tool_v1.json').read_text())
CALCULATE='recommend_refinery_plan'
CONTRACT='get_refinery_contract'


def strict_loads(text):
    def unique(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:raise ValueError('DUPLICATE_JSON_KEY')
            obj[key]=value
        return obj
    def number(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('NONFINITE_JSON')
        return result
    return json.loads(text,object_pairs_hook=unique,parse_float=number,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_bytes(encoded(value)+b'\n')


def descriptions():
    return [
        {'name':CONTRACT,'description':'Get the exact input contract and a clearly synthetic example. Do not invent missing plant observations, sample publication times, analyzer health or source evidence.',
         'inputSchema':{'type':'object','properties':{},'additionalProperties':False}},
        {'name':CALCULATE,'description':'Calculate a model-only refinery plan from the full Python-cycle request JSON. Returns a primary recommendation only if the original order passes the full uncertainty set and independent gate. NO_CHANGE preserves current operation. Business refusals are normal results. Conditional/deferred plans must never be promoted; no plant actuation. Logs each call to a server-owned directory. Get the contract first; never fabricate observations or override mandatory limits.',
         'inputSchema':{'type':'object','properties':{'request':{'type':'object','description':'Full python-cycle-v1 request from get_refinery_contract. Explicit synthetic inputs or read-only historical replay; unknown data must not be invented.'}},'required':['request'],'additionalProperties':False}}
    ]


def input_contract():
    # Contract discovery does not run the optimizer.
    from .decision_cycle import contracts,example_request
    c,_,k,p=contracts()
    fields={name:{'type':'number','minimum':lo,'maximum':min(hi,10) if name=='sulfur_limit' else hi}
            for name,(lo,hi) in c['bounds'].items()}
    fields.update(clean_stock={'type':'number','minimum':0,'maximum':200},
                  heavy_stock={'type':'number','minimum':0,'maximum':200},data_ok={'type':'boolean'})
    fields['step']['enum']=[15,30,60]
    fields['horizon'].update(exclusiveMinimum=0)
    schema={'type':'object','properties':{
        'schema':{'const':'python-cycle-v1'},'scope':{'enum':['synthetic_model','historical_replay']},
        'origin':{'type':'string','description':'ISO local timestamp without UTC offset'},
        'state':{'type':'object','properties':fields,'required':list(fields),'additionalProperties':False},
        'observations':{'type':'array','maxItems':1000,'items':{'type':'object'},'description':'See observation_contract; future/unavailable values are excluded.'},
        'bindings':{'type':'object','description':'Each bound field must match the observation field and unit; no fallback if unavailable.'},
        'use_lims_upper_bound':{'type':'boolean'}},
        'required':['schema','scope','origin','state'],'additionalProperties':False}
    return {'version':POLICY['id'],'input_schema':schema,'tools':descriptions(),
        'example_label':'SYNTHETIC_EXAMPLE_NOT_PLANT_OBSERVATION','example_request':example_request('bridge'),
        'observation_contract':{'required':['id','source','field','value','unit','event_time','provenance'],
            'optional':['available_time','received_time','event_time_basis','health','dependencies_available'],
            'sources':['lims','pac','virtual'],'lims':'event_time is sample time. Explicit publication or explicitly enabled conservative sample+4h rule; never assume immediate availability.',
            'pac':'Requires confirmed_measurement time basis, healthy status, explicit available_time. These are unknown for the existing archive.',
            'virtual':'Explicit available_time and dependencies_available=true are required; this flag is supplied evidence, not independent verification.',
            'priority':'Eligible LIMS > eligible PAC > eligible virtual, within same field and unit.'},
        'limits':p,'model_assumptions':c['assumptions'],'stop_rules':[
            'Explain NO_CHANGE; do not invent another action.',
            'Explain refusals. Ask for missing facts or explicitly changed scenarios; do not weaken mandatory limits.',
            'Conditional-only and deferred orders are diagnostics, not the original recommendation.',
            'If the tool fails, report the failure; do not reuse a previous plan for a changed state.'],
        'authority':'Own illustrative model only; no industrial command or causal validation.'}


def summarize(decision,call_id,call_dir):
    conditional=decision.get('delay_le_1')
    diagnostic=None
    if conditional:
        primary=conditional['primary']
        diagnostic={'condition':'ASSUMED_DELAY_LE_1_HOUR_NOT_ESTABLISHED',
                    'primary_plan_available':primary['dynamic'] is not None,
                    'saving_fraction_vs_stationary':primary['saving_fraction'],
                    'earliest_feasible_start':conditional['earliest_feasible_start'],
                    'not_primary_recommendation':True}
    trace=[]
    for m in decision.get('trace',[]):
        entry={k:m[k] for k in ('sequence','role','message_id','state_id','consumes')}
        entry['scope']=m['output'].get('scope')
        if m['role']=='gate':
            entry['passed']=m['output']['passed']
            entry['plans_checked']=len(m['output']['checks'])
        trace.append(entry)
    return {'tool_version':POLICY['id'],'call_id':call_id,'status':decision['status'],
            'industrial_command':False,'model_only':True,'state_id':decision.get('state_id'),
            'contract_sha256':decision.get('contract_sha256'),'reasons':decision.get('reasons',[]),
            'explanation':decision.get('explanation'),
            'recommendation':decision.get('recommendation'),'conditional_diagnostic':diagnostic,
            'trace':trace,'artifacts':{'decision_json':str(call_dir/'result/decision.json'),
                                     'operator_card':str(call_dir/'result/report.html'),
                                     'audit_manifest':str(call_dir/'manifest.json')},
            'limitations':['MODEL_EFFECT_NOT_INDUSTRIALLY_VALIDATED','PRACTICAL_FORECAST_QUALITY_NOT_ESTABLISHED',
                           'DO_NOT_INVENT_AVAILABILITY_OR_PROMOTE_CONDITIONAL_PLAN']}


def output_admissible(decision):
    if decision.get('industrial_command') is not False:
        return False
    status=decision.get('status')
    if status not in {'MODEL_PLAN','NO_CHANGE','INVALID_INPUT','ABSTAIN_DATA',
                      'ABSTAIN_HISTORICAL_SCOPE','ABSTAIN_GATE','ABSTAIN_NO_FULL_PLAN'}:
        return False
    plan=decision.get('recommendation')
    if status not in ('MODEL_PLAN','NO_CHANGE'):
        return plan is None
    if not isinstance(plan,dict) or plan.get('unmet_original_mass')!=0:
        return False
    full=decision.get('full') or {}
    expected=full.get('current_plan') if status=='NO_CHANGE' else (full.get('primary') or {}).get('dynamic')
    if plan!=expected:
        return False
    return any(message.get('role')=='gate' and message['output'].get('scope')=='full'
               and message['output'].get('passed') is True
               and any(check.get('candidate_id')==plan.get('candidate_id')
                       and check.get('passed') is True
                       and check.get('original_order_complete') is True
                       for check in message['output'].get('checks',[]))
               for message in decision.get('trace',[]))


class ToolService:
    def __init__(self,audit_root):
        self.audit_root=Path(audit_root).resolve()
        self.audit_root.mkdir(parents=True,exist_ok=True)

    def call(self,name,arguments):
        call_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:12]
        folder=self.audit_root/call_id;folder.mkdir()
        started=time.monotonic()
        manifest={'tool_version':POLICY['id'],'call_id':call_id,'tool':name,'started_at':datetime.now(timezone.utc).isoformat(),
                  'python':sys.version,'timeout_seconds':POLICY['timeout_seconds'],'status':'running',
                  'model_fits':0,'production_rows_read':0}
        save(folder/'manifest.json',manifest)
        def finish(output,is_error=False):
            save(folder/'response.json',output)
            manifest.update(status='tool_error' if is_error else 'completed',finished_at=datetime.now(timezone.utc).isoformat(),
                            seconds=time.monotonic()-started,output_status=output.get('status'),is_error=is_error)
            manifest['artifacts_sha256']={str(p.relative_to(folder)):sha(p) for p in sorted(folder.rglob('*')) if p.is_file() and p.name!='manifest.json'}
            save(folder/'manifest.json',manifest)
            return {'is_error':is_error,'output':output}
        def error(reason):
            return finish({'tool_version':POLICY['id'],'call_id':call_id,'status':'TOOL_ERROR','reasons':[reason],
                'recommendation':None,'industrial_command':False,'audit_manifest':str(folder/'manifest.json')},True)
        try:
            body=encoded(arguments)
        except (TypeError,ValueError,RecursionError):return error('NONFINITE_OR_NON_JSON_ARGUMENTS')
        if len(body)>POLICY['max_request_bytes']:
            manifest['rejected_request_bytes']=len(body)
            manifest['rejected_request_sha256']=hashlib.sha256(body).hexdigest()
            return error('REQUEST_TOO_LARGE')
        save(folder/'arguments.json',arguments)
        if name not in (CONTRACT,CALCULATE):return error('UNKNOWN_TOOL')
        if not isinstance(arguments,dict):return error('ARGUMENTS_MUST_BE_OBJECT')
        if name==CONTRACT:
            if arguments:return error('UNEXPECTED_ARGUMENTS')
            return finish({**input_contract(),'call_id':call_id,'audit_manifest':str(folder/'manifest.json')})
        if set(arguments)!={'request'} or not isinstance(arguments['request'],dict):
            return error('EXPECTED_ONLY_REQUEST_OBJECT')
        save(folder/'request.json',arguments['request'])
        # Fingerprint the actual code/config used, before and after the child run.
        code=[ROOT/name for name in (
            'neft/__init__.py','neft/agent_tool.py','neft/decision_cycle.py','neft/cycle_state.py',
            'neft/cycle_gate.py','neft/dynamic_planner.py','neft/cycle_report.py',
            'neft/expert_contracts.py','neft/scenario_sensitivity.py',
            'configs/agent_tool_v1.json','configs/python_cycle_v1.json',
            'configs/process_scenario_v1.json','configs/scenario_sensitivity_v1.json',
            'configs/dynamic_blending_v1.json','scripts/run_decision_cycle.py')]
        before={str(p.relative_to(ROOT)):sha(p) for p in code}
        manifest['runtime_sha256']=before
        command=[sys.executable,'-I','-B',str(ROOT/'scripts/run_decision_cycle.py'),
                 '--input',str(folder/'request.json'),'--output',str(folder/'result')]
        manifest['command']=command
        try:
            with (folder/'worker.stdout.txt').open('w') as stdout,(folder/'worker.stderr.txt').open('w') as stderr:
                worker=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=POLICY['timeout_seconds'])
            manifest['returncode']=worker.returncode
            if worker.returncode:return error('WORKER_FAILED')
            after={str(p.relative_to(ROOT)):sha(p) for p in code}
            if before!=after:return error('RUNTIME_CHANGED_DURING_CALL')
            decision=strict_loads((folder/'result/decision.json').read_text())
            if not output_admissible(decision):
                return error('OUTPUT_CONTRACT_VIOLATION')
            return finish(summarize(decision,call_id,folder))
        except subprocess.TimeoutExpired:return error('WORKER_TIMEOUT')
        except (OSError,ValueError,KeyError,TypeError):return error('WORKER_OR_OUTPUT_ERROR')
