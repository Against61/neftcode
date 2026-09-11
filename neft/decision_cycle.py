"""Canonical Python API. No Node, browser, production import or plant write."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from .cycle_state import digest, select_state
from .cycle_gate import validate_state, validate_plan
from .dynamic_planner import assess, optimize

ROOT=Path(__file__).resolve().parents[1]


def contracts():
    names=('process_scenario_v1','scenario_sensitivity_v1','dynamic_blending_v1','python_cycle_v1')
    return tuple(json.loads((ROOT/'configs'/f'{name}.json').read_text()) for name in names)


def example_request(preset='bridge'):
    c,_,k,p=contracts()
    found=next(x for x in k['presets'] if x['id']==preset)
    return {'schema':p['id'],'scope':'synthetic_model','origin':'2024-06-01T12:00:00',
            'state':{**c['defaults'],**k['defaults'],**found['overrides']},
            'observations':[],'bindings':{},'use_lims_upper_bound':False}


@dataclass(frozen=True)
class RoleMessage:
    sequence: int
    role: str
    consumes: tuple
    state_id: str
    message_id: str
    output: dict


class Journal:
    def __init__(self,state_id):
        self.state_id=state_id
        self.messages=[]

    def emit(self,role,output,consumes=()):
        content={'role':role,'state_id':self.state_id,'output':output,'consumes':consumes}
        message=RoleMessage(len(self.messages),role,tuple(consumes),self.state_id,digest(content),output)
        self.messages.append(asdict(message))
        return message.message_id


def run_cycle(request):
    c,p,k,policy=contracts()
    try:
        state_id=digest(request)
    except (TypeError,ValueError):
        return {'version':policy['id'],'status':'INVALID_INPUT','industrial_command':False,
                'reasons':['NON_JSON_OR_NONFINITE_REQUEST'],'recommendation':None,'trace':[]}
    journal=Journal(state_id)
    out={'version':policy['id'],'scope':request.get('scope') if isinstance(request,dict) else None,
         'state_id':state_id,'industrial_command':False,'input':request,'status':'INVALID_INPUT',
         'recommendation':None,'reasons':[],'trace':journal.messages,
         'contract_sha256':digest({'model':c,'uncertainty':p,'dynamic':k,'policy':policy}),
         'policy':policy,'full':None,'delay_le_1':None}
    def refuse(status,reasons,dependencies=()):
        out.update(status=status,reasons=reasons,recommendation=None)
        journal.emit('decision',{'status':status,'reasons':reasons,'recommendation':None},dependencies)
        return out
    allowed={'schema','scope','origin','state','observations','bindings','use_lims_upper_bound'}
    if not isinstance(request,dict) or set(request)-allowed or request.get('schema')!=policy['id'] or request.get('scope') not in ('synthetic_model','historical_replay'):
        return refuse('INVALID_INPUT',['REQUEST_SCHEMA'])
    if 'use_lims_upper_bound' in request and type(request['use_lims_upper_bound']) is not bool:
        return refuse('INVALID_INPUT',['UPPER_BOUND_FLAG_MUST_BE_BOOLEAN'])
    try:
        state=select_state(request,policy)
    except (TypeError,ValueError,KeyError) as exc:
        return refuse('INVALID_INPUT',['STATE_CONTRACT: '+str(exc)])
    sid=journal.emit('state',state.summary())
    out['model_input']=state.state
    if state.issues:
        return refuse('ABSTAIN_DATA',state.issues,(sid,))
    if request['scope']=='historical_replay':
        return refuse('ABSTAIN_HISTORICAL_SCOPE',['SYNTHETIC_ACTION_MAPPING_NOT_ESTABLISHED'],(sid,))
    issues=validate_state(state.state,c)
    if issues:
        return refuse('INVALID_INPUT',issues,(sid,))
    out['periods']=round(state.state['horizon']*60/state.state['step'])
    gate_checks=[]
    final_dependencies=[]
    for scope,delays in [('full',(0,1,2,3)),('delay_le_1',(0,1))]:
        q,r=assess(state.state,c,delays,scope)
        qid=journal.emit('quality',{'scope':scope,'candidate_count':q.costs.shape[1],
            'periods':len(q.costs),'source_message':sid,
            'quality_capacity_feasible_cells':int((q.failures==0).sum()),
            'sulfur_hard_cap_mg_kg':10,'uncertainty':'finite_synthetic_grid'},(sid,))
        rid=journal.emit('reliability',{'scope':scope,'maximum_burden':float(r.maximum.max()),
            'burden_feasible_cells':int((r.failures==0).sum()),
            'interpretation':'model_burden_index_not_failure_probability'},(sid,))
        result=optimize(state.state,c,q,r)
        ids=[]
        # Same plan can occur as primary/frontier or dynamic/stationary: verify it once per scope.
        checked={}
        for entry in result['frontier']:
            for key in ('dynamic','stationary'):
                plan=entry[key]
                if plan is not None:
                    pid=digest(plan)
                    plan['candidate_id']=pid
                    ids.append(pid)
                    checked.setdefault(pid,plan)
        if result['current_plan'] is not None:
            plan=result['current_plan'];pid=digest(plan);plan['candidate_id']=pid
            checked.setdefault(pid,plan);ids.append(pid)
        oid=journal.emit('optimizer',{'scope':scope,'primary_status':result['primary']['status'],
            'candidate_ids':sorted(set(ids)),'rejections':result['rejections'],
            'objective':result['objective'],'earliest_feasible_start':result['earliest_feasible_start']},(qid,rid))
        for pid,plan in checked.items():
            check=validate_plan(state.state,plan,scope,c,p)
            gate_checks.append({'scope':scope,'candidate_id':pid,**check})
        failed=[x for x in gate_checks if x['scope']==scope and not x['passed']]
        gid=journal.emit('gate',{'scope':scope,'checks':[x for x in gate_checks if x['scope']==scope],
            'passed':not failed,'sulfur_hard_cap_mg_kg':10},(oid,sid))
        final_dependencies.append(gid)
        if failed:
            return refuse('ABSTAIN_GATE',['INDEPENDENT_PLAN_GATE_FAILED'],(gid,))
        out[scope]=result
    full=out['full'];optimal=full['primary']['dynamic'];baseline=full['current_plan']
    if optimal is None:
        status='ABSTAIN_NO_FULL_PLAN'
        out['reasons']=['NO_ROBUST_PLAN_FOR_ORIGINAL_ORDER']
        explanation='Нет допустимого плана исходного заказа во всём наборе допущений. Условный план и перенос остаются отдельными диагностическими результатами.'
    else:
        keep=baseline is not None and baseline['cost_upper']<=optimal['cost_upper']*(1+policy['no_change_relative_cost_tolerance'])+1e-10
        status='NO_CHANGE' if keep else 'MODEL_PLAN'
        out['recommendation']=baseline if keep else optimal
        explanation=('Текущий полный рецепт допустим; выигрыш оптимизации не превышает заданный порог. Сохранить режим.' if keep else 'Предложен модельный план исходного заказа, прошедший независимую проверку во всех 972 сочетаниях. Физический эффект не подтверждён.')
    out.update(status=status,explanation=explanation)
    journal.emit('decision',{'status':status,'candidate_id':out['recommendation']['candidate_id'] if out['recommendation'] else None,
        'reasons':out['reasons'],'explanation':explanation,'conditional_promoted':False},final_dependencies)
    return out
