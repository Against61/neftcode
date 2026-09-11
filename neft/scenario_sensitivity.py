"""Finite assumption-grid audit of the frozen synthetic process scenario."""
from itertools import product
import numpy as np

FIELDS=('temperature','pressure','feed','clean','heavy','additive')
REASONS=('AVT_FEED_CAPACITY','HT_CAPACITY','CLEAN_CAPACITY','HEAVY_CAPACITY','QUALITY_s','QUALITY_t95','QUALITY_cn','SEVERITY')

def candidates(config):
    grid=[v for v in product(*(config['grid'][k] for k in FIELDS)) if v[3]+v[4]<=.4+1e-9]
    grid.append(tuple(config['current'][k] for k in FIELDS))
    return np.asarray(grid,dtype=float)

def worlds(protocol):return np.asarray(list(product(*protocol['axes'].values())),dtype=float)

def matrix(state,config,commands,parameters):
    n=len(parameters);T0,P0,Q0=(config['current'][k] for k in ('temperature','pressure','feed'))
    Tcmd,Pcmd,Qcmd,b,h,a=commands.T
    alpha=np.clip((state['horizon']-parameters[:,5,None])/config['model']['response_ramp_hours'],0,1)
    T=T0+alpha*(Tcmd-T0);P=P0+alpha*(Pcmd-P0);Q=Q0+alpha*(Qcmd-Q0)
    f=1-b-h-a;w=(b+h)/(1-a)
    ht_s=state['crude_sulfur']*10000*config['model']['sulfur_partition']*np.exp(-parameters[:,0,None]*(5.8+.06*(T-340)+.35*(P-4))*100/Q)
    ht_t=state['feed_t95']-.1*(T-340)-.5*(P-4)
    ht_cn=state['feed_cn']+.08*(T-340)+.4*(P-4)
    sulfur=f*ht_s+b*state['clean_s']+h*state['heavy_s']
    t95=(f*ht_t+b*state['clean_t95']+h*state['heavy_t95'])/(1-a)+parameters[:,1,None]*4*w*(1-w)
    cn=(f*ht_cn+b*state['clean_cn']+h*state['heavy_cn'])/(1-a)+config['model']['additive_cn_gain']*parameters[:,2,None]*a
    premium=.0002*(T-330)**2+.04*(P-3)**2+.08*(100/Q-1)
    cost=f*(1+parameters[:,3,None]*premium)+b*config['model']['clean_cost']*parameters[:,4,None]+h*config['model']['heavy_cost']+a*config['expert_basis']['additive_cost_ratio']
    severity=.6*(T-330)/40+.4*(P-3)/2
    tests=[Q>state['crude_flow']*config['model']['yield']+1e-9,
           np.broadcast_to(state['demand']*f,(n,len(commands)))>Q*config['model']['ht_yield']+1e-9,
           np.broadcast_to(state['demand']*b>state['clean_capacity']+1e-9,(n,len(commands))),
           np.broadcast_to(state['demand']*h>state['heavy_capacity']+1e-9,(n,len(commands))),
           sulfur+config['model']['sulfur_margin']>state['sulfur_limit']+1e-9,
           t95+config['model']['t95_margin']>state['t95_limit']+1e-9,
           cn-config['model']['cetane_margin']<state['cn_min']-1e-9,
           severity>config['model']['severity_limit']+1e-9]
    failures=np.zeros(cost.shape,dtype=np.uint16)
    for i,bad in enumerate(tests):failures|=bad.astype(np.uint16)<<i
    return {'cost':cost,'failures':failures,'sulfur':sulfur,'t95':t95,'cn':cn,'severity':severity}

def distance(commands,config):
    current=np.array([config['current'][k] for k in FIELDS]);scales=np.array([.1,1,.05,10,10,100])
    return (np.abs(commands-current)*scales).sum(axis=1)

def choose_nominal(cost,valid,dist):
    available=np.flatnonzero(valid)
    if not len(available):return -1,None
    minimum=float(cost[available].min());ties=available[cost[available]<=minimum+1e-10]
    return int(ties[np.argmin(dist[ties])]),minimum

def audit_case(state,config,commands,parameters,protocol,old):
    arrays=matrix(state,config,commands,parameters);cost=arrays['cost'];valid=arrays['failures']==0
    dist=distance(commands,config);nominal=np.asarray(protocol['nominal']);ni=int(np.flatnonzero(np.all(parameters==nominal,axis=1))[0])
    world_best=[];optimum=[]
    for row in range(len(parameters)):
        chosen,minimum=choose_nominal(cost[row],valid[row],dist);world_best.append(chosen);optimum.append(minimum if minimum is not None else np.nan)
    optimum=np.asarray(optimum);world_best=np.asarray(world_best,dtype=np.int32)
    assert int(valid[ni].sum())==old['counts']['feasible']
    selected=old['selected_candidate'];nominal_id=None
    if selected is not None:
        command=np.array([selected[k] for k in FIELDS]);nominal_id=int(np.flatnonzero(np.all(commands==command,axis=1))[0])
        assert np.all(commands[world_best[ni]]==command)
        assert np.isclose(cost[ni,nominal_id],selected['predictions']['cost'],rtol=1e-12)
        for name,key in [('sulfur','s'),('t95','t95'),('cn','cn')]:assert np.isclose(arrays[name][ni,nominal_id],selected['predictions']['quality'][key],rtol=1e-12)
    else:assert world_best[ni]==-1
    def summarize(mask):
        ids=np.flatnonzero(mask);feas=valid[mask];vals=cost[mask];mins=optimum[mask]
        common=np.flatnonzero(feas.all(axis=0));robust=None
        if len(common):
            regrets=np.maximum(0,vals[:,common]/mins[:,None]-1)
            worst=regrets.max(axis=0);best=float(worst.min());ties=common[worst<=best+1e-10]
            nc=cost[ni,ties];ties=ties[nc<=nc.min()+1e-10];ci=int(ties[np.argmin(dist[ties])]);r=np.maximum(0,cost[ids,ci]/optimum[ids]-1);wi=int(ids[np.argmax(r)])
            robust={'candidate_id':ci,'command':dict(zip(FIELDS,commands[ci].tolist())),'max_regret':float(r.max()),'worst_world':wi,'nominal_cost':float(cost[ni,ci]),'within_5_percent':bool(r.max()<=.05+1e-12)}
        nominal_summary=None
        if nominal_id is not None:
            safe=feas[:,nominal_id];safeids=ids[safe];regret=np.maximum(0,cost[safeids,nominal_id]/optimum[safeids]-1)
            fails=ids[~safe];nominal_summary={'feasible_worlds':int(safe.sum()),'total_worlds':len(ids),'feasible_fraction':float(safe.mean()),'max_regret_when_feasible':float(regret.max()) if len(regret) else None,'accepted':bool(safe.all() and len(regret) and regret.max()<=.05+1e-12),'first_failure_world':int(fails[0]) if len(fails) else None,'worst_regret_world':int(safeids[np.argmax(regret)]) if len(regret) else None,'same_optimal_command_worlds':sum(int(i)>=0 and np.array_equal(commands[i],commands[nominal_id]) for i in world_best[mask])}
        return {'worlds':len(ids),'worlds_with_any_feasible':int(np.isfinite(mins).sum()),'common_candidates':len(common),'robust':robust,'nominal':nominal_summary}
    oat=[]
    for i,p in enumerate(parameters):
        diff=np.flatnonzero(p!=nominal)
        if len(diff)>1:continue
        factor=list(protocol['axes'])[diff[0]] if len(diff) else 'nominal'
        bits=int(arrays['failures'][i,nominal_id]) if nominal_id is not None else None
        oat.append({'world_id':i,'factor':factor,'parameters':p.tolist(),'feasible_candidates':int(valid[i].sum()),'optimum_cost':float(optimum[i]) if np.isfinite(optimum[i]) else None,'best_candidate_id':int(world_best[i]),'nominal_feasible':bits==0 if bits is not None else None,'nominal_failures':[name for j,name in enumerate(REASONS) if bits is not None and bits&(1<<j)],'same_command':bool(nominal_id is not None and world_best[i]>=0 and np.array_equal(commands[world_best[i]],commands[nominal_id]))})
    summary={'nominal_candidate_id':nominal_id,'nominal_world_id':ni,'full':summarize(np.ones(len(parameters),dtype=bool)),'delay_le_1':summarize(parameters[:,5]<=1),'oat':oat}
    arrays.update(optimum=optimum,best=world_best)
    return summary,arrays
