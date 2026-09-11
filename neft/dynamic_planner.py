"""Python finite-resource optimizer for the frozen illustrative process model.

It consumes quality/capacity and reliability assessments. Final admission of a
plan is a separate scalar full-world gate, not an optimizer success flag.
"""
from dataclasses import dataclass
from itertools import product
import math
import numpy as np
from .scenario_sensitivity import candidates, matrix, FIELDS, REASONS


@dataclass(frozen=True)
class QualityAssessment:
    costs: np.ndarray
    failures: np.ndarray
    sulfur: np.ndarray
    t95: np.ndarray
    cn: np.ndarray
    scope: str
    delays: tuple


@dataclass(frozen=True)
class ReliabilityAssessment:
    failures: np.ndarray
    maximum: np.ndarray


def assess(state, model, delays, scope):
    commands = candidates(model)[:-1]
    corners = np.asarray(list(product([.9], [5], [.75], [.75,1.25], [1.2], delays)))
    n = round(state['horizon']*60/state['step'])
    mass = state['demand']*state['step']/60
    rows = []
    for t in range(n):
        endpoints = [matrix({**state, 'horizon': k*state['step']/60}, model, commands, corners)
                     for k in (t,t+1)]
        bits = np.bitwise_or(*(np.bitwise_or.reduce(e['failures'], axis=0) for e in endpoints))
        rows.append((np.maximum(*(e['cost'].max(axis=0) for e in endpoints))*mass,
                     bits, np.maximum(*(e['sulfur'].max(axis=0) for e in endpoints)),
                     np.maximum(*(e['t95'].max(axis=0) for e in endpoints)),
                     np.minimum(*(e['cn'].min(axis=0) for e in endpoints)),
                     np.maximum(*(e['severity'].max(axis=0) for e in endpoints))))
    arrays = [np.asarray([r[k] for r in rows]) for k in range(6)]
    return (QualityAssessment(arrays[0], arrays[1].astype(np.uint16)&127,
                              arrays[2], arrays[3], arrays[4], scope, tuple(delays)),
            ReliabilityAssessment(arrays[1].astype(np.uint16)&128, arrays[5]))


def schedule(options, clean_units, heavy_units):
    labels = {(0,0): (0.0, ())}
    for recipes in options:
        # Equal resources: a strictly more expensive recipe is dominated for all future periods.
        best = {}
        for r in recipes:
            key = (r['b'],r['h'])
            if key not in best or r['cost'] < best[key]['cost']-1e-10:
                best[key] = r
        recipes = sorted(best.values(), key=lambda r:r['id'])
        following = {}
        for (b,h),(cost,path) in sorted(labels.items()):
            for r in recipes:
                dest = (b+r['b'],h+r['h'])
                value = cost+r['cost']
                if dest[0]<=clean_units and dest[1]<=heavy_units and (
                    dest not in following or value<following[dest][0]-1e-10):
                    following[dest] = (value,path+(r['id'],))
        labels = following
        if not labels:
            return None
    dest = min(sorted(labels), key=lambda x:labels[x][0])
    cost,path = labels[dest]
    return {'cost':cost,'clean_units':dest[0],'heavy_units':dest[1],'recipes':list(path)}


def optimize(state, model, q, reliability):
    commands = candidates(model)[:-1]
    n = len(q.costs)
    mass = state['demand']*state['step']/60
    unit = mass*.1
    valid = (q.failures | reliability.failures)==0
    def detail(plan, start):
        if plan is None:
            return None
        clean,heavy = state['clean_stock'],state['heavy_stock']
        timeline=[]
        for t,mix in enumerate(plan['recipes'], start):
            j=plan['ht']*77+mix
            v=commands[j]
            before={'clean':clean,'heavy':heavy}
            clean-=mass*v[3];heavy-=mass*v[4]
            effective={}
            for k,field in enumerate(FIELDS[:3]):
                values=[]
                for delay in q.delays:
                    for time in (t*state['step']/60,(t+1)*state['step']/60):
                        alpha=min(1,max(0,(time-delay)/model['model']['response_ramp_hours']))
                        values.append(model['current'][field]+alpha*(v[k]-model['current'][field]))
                effective[field]=[float(min(values)),float(max(values))]
            timeline.append({'period':t,'start':t*state['step']/60,'end':(t+1)*state['step']/60,
                'recipe':dict(zip(FIELDS,map(float,v))), 'mass':mass,'stocks_before':before,
                'stocks_after':{'clean':max(0,float(clean)),'heavy':max(0,float(heavy))},
                'consumption':{'clean':float(mass*v[3]),'heavy':float(mass*v[4]),
                               'additive':float(mass*v[5]),'fresh':float(mass*(1-v[3]-v[4]-v[5]))},
                'quality':{'sulfur_max':float(q.sulfur[t,j]),'t95_max':float(q.t95[t,j]),
                           'cn_min':float(q.cn[t,j])},
                'severity_max':float(reliability.maximum[t,j]),'effective':effective,
                'cost_upper':float(q.costs[t,j])})
        periods=n-start
        return {'cost_upper':plan['cost'],'unit_cost_upper':plan['cost']/(mass*periods),
                'command':{k:timeline[0]['recipe'][k] for k in FIELDS[:3]},
                'delivered_mass':periods*mass,'unmet_original_mass':start*mass,
                'clean_used':plan['clean_units']*unit,'heavy_used':plan['heavy_units']*unit,
                'timeline':timeline}
    frontier=[]
    for start in range(n):
        remaining=n-start
        cb=min(remaining*3,math.floor(state['clean_stock']/unit+1e-9))
        hb=min(remaining*2,math.floor(state['heavy_stock']/unit+1e-9))
        dynamic=stationary=None
        for ht in range(45):
            options=[]
            for t in range(start,n):
                row=[]
                for j in np.flatnonzero(valid[t,ht*77:(ht+1)*77])+ht*77:
                    row.append({'id':int(j%77),'b':round(commands[j,3]*10),
                                'h':round(commands[j,4]*10),'cost':float(q.costs[t,j])})
                options.append(row)
            if any(not row for row in options):
                continue
            d=schedule(options,cb,hb)
            if d and (dynamic is None or d['cost']<dynamic['cost']-1e-10):
                dynamic={**d,'ht':ht}
            for mix in range(77):
                j=ht*77+mix
                b,h=(round(commands[j,k]*10)*remaining for k in (3,4))
                if b<=cb and h<=hb and valid[start:,j].all():
                    price=float(q.costs[start:,j].sum())
                    if stationary is None or price<stationary['cost']-1e-10:
                        stationary={'cost':price,'clean_units':b,'heavy_units':h,
                                    'ht':ht,'recipes':[mix]*remaining}
        frontier.append({'start':start*state['step']/60,'delivered_mass':mass*remaining,
                         'unmet_original_mass':mass*start,'dynamic':detail(dynamic,start),
                         'stationary':detail(stationary,start),
                         'saving_fraction':max(0,1-dynamic['cost']/stationary['cost']) if stationary else None,
                         'status':'FEASIBLE_MODEL_PLAN' if dynamic else 'ABSTAIN_NO_FULL_PLAN'})
    primary=frontier[0]
    # Current complete recipe is a baseline for the original order, never a deferred order.
    current=np.array([model['current'][f] for f in FIELDS])
    j=int(np.flatnonzero(np.all(commands==current,axis=1))[0])
    b,h=(round(commands[j,k]*10)*n for k in (3,4))
    current_plan=None
    if valid[:,j].all() and b*unit<=state['clean_stock']+1e-9 and h*unit<=state['heavy_stock']+1e-9:
        current_plan=detail({'ht':j//77,'recipes':[j%77]*n,'clean_units':b,'heavy_units':h,
                             'cost':float(q.costs[:,j].sum())},0)
    return {'name':q.scope,'conditional':q.scope!='full','worlds':len(q.delays)*243,
            'delays':list(q.delays),'objective':'sum_of_period_worst_endpoint_costs',
            'primary':primary,'frontier':frontier,'current_plan':current_plan,
            'earliest_feasible_start':next((f['start'] for f in frontier if f['dynamic']),None),
            'meets_gain_hypothesis':bool(primary['dynamic'] and (not primary['stationary'] or primary['saving_fraction']>=.01-1e-12)),
            'rejections':{name:int(np.count_nonzero((q.failures|reliability.failures)&(1<<k))) for k,name in enumerate(REASONS)}}
