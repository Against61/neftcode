"""Independent scalar, full-world admission gate for the Python model cycle.

Recomputes physical proxies and ledgers from state and commands. It never trusts
optimizer flags, predicted quality, claimed costs, or inventory balances.
"""
from itertools import product
import math
from .cycle_state import finite

HARD_SULFUR_MAX = 10.0
FIELDS = ('temperature','pressure','feed','clean','heavy','additive')


def scalar(state, model, recipe, time, world):
    k,bias,gain,energy,clean_price,delay=world
    m=model['model']
    alpha=min(1,max(0,(time-delay)/m['response_ramp_hours']))
    T,P,Q=[model['current'][f]+alpha*(recipe[f]-model['current'][f]) for f in FIELDS[:3]]
    b,h,a=[recipe[f] for f in FIELDS[3:]]
    f=1-b-h-a
    sulfur=f*state['crude_sulfur']*10000*m['sulfur_partition']*math.exp(-k*(5.8+.06*(T-340)+.35*(P-4))*100/Q)+b*state['clean_s']+h*state['heavy_s']
    mix=(b+h)/(1-a)
    t95=(f*(state['feed_t95']-.1*(T-340)-.5*(P-4))+b*state['clean_t95']+h*state['heavy_t95'])/(1-a)+bias*4*mix*(1-mix)
    cn=(f*(state['feed_cn']+.08*(T-340)+.4*(P-4))+b*state['clean_cn']+h*state['heavy_cn'])/(1-a)+m['additive_cn_gain']*gain*a
    cost=f*(1+energy*(.0002*(T-330)**2+.04*(P-3)**2+.08*(100/Q-1)))+b*m['clean_cost']*clean_price+h*m['heavy_cost']+a*model['expert_basis']['additive_cost_ratio']
    severity=.6*(T-330)/40+.4*(P-3)/2
    reasons=[]
    if sulfur+m['sulfur_margin']>min(HARD_SULFUR_MAX,state['sulfur_limit'])+1e-9: reasons.append('QUALITY_s')
    if t95+m['t95_margin']>state['t95_limit']+1e-9: reasons.append('QUALITY_t95')
    if cn-m['cetane_margin']<state['cn_min']-1e-9: reasons.append('QUALITY_cn')
    if severity>m['severity_limit']+1e-9: reasons.append('SEVERITY')
    if Q>state['crude_flow']*m['yield']+1e-9: reasons.append('AVT_FEED_CAPACITY')
    if state['demand']*f>Q*m['ht_yield']+1e-9: reasons.append('HT_CAPACITY')
    if state['demand']*b>state['clean_capacity']+1e-9: reasons.append('CLEAN_CAPACITY')
    if state['demand']*h>state['heavy_capacity']+1e-9: reasons.append('HEAVY_CAPACITY')
    return {'sulfur':sulfur,'t95':t95,'cn':cn,'cost':cost,'severity':severity,
            'effective':dict(zip(FIELDS[:3],(T,P,Q))),'reasons':reasons}


def validate_state(state, model):
    issues=[]
    expected=set(model['defaults'])|{'clean_stock','heavy_stock'}
    if set(state)-expected: issues.append('UNKNOWN_STATE_FIELDS')
    for field,(lo,hi) in model['bounds'].items():
        if not finite(state.get(field)) or not lo<=state[field]<=hi:
            issues.append('INPUT_'+field)
    for field in ('clean_stock','heavy_stock'):
        if not finite(state.get(field)) or not 0<=state[field]<=200:
            issues.append('INPUT_'+field)
    if not finite(state.get('sulfur_limit')) or state['sulfur_limit']>HARD_SULFUR_MAX:
        issues.append('SULFUR_HARD_LIMIT_10')
    if state.get('data_ok') is not True: issues.append('DATA_UNAVAILABLE')
    h,step=state.get('horizon'),state.get('step')
    if not finite(h) or not finite(step) or step not in (15,30,60) or not 0<h<=3 or abs(h*60/step-round(h*60/step))>1e-9:
        issues.append('TIME_GRID')
    return issues


def validate_plan(state, plan, scope, model, uncertainty):
    issues=validate_state(state,model)
    if scope not in ('full','delay_le_1'): issues.append('UNKNOWN_UNCERTAINTY_SCOPE')
    if issues: return {'passed':False,'reasons':issues,'evaluations':0}
    calls=0
    def require(ok,reason):
        if not ok: raise ValueError(reason)
    def close(a,b,reason):
        require(finite(a) and math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-8),reason)
    try:
        require(isinstance(plan,dict),'PLAN_REQUIRED')
        require(set(plan['command'])==set(FIELDS[:3]),'COMMAND_FIELDS')
        timeline=plan['timeline']
        n=round(state['horizon']*60/state['step']);dt=state['step']/60;mass=state['demand']*dt
        require(isinstance(timeline,list) and 0<len(timeline)<=n,'TIMELINE_LENGTH')
        start=n-len(timeline)
        clean,heavy=state['clean_stock'],state['heavy_stock']
        initial_clean,initial_heavy=clean,heavy
        total=0
        ws=list(product(*uncertainty['axes'].values()))
        if scope=='delay_le_1': ws=[w for w in ws if w[5]<=1]
        for i,row in enumerate(timeline,start):
            require(row['period']==i,'PERIOD_ORDER')
            close(row['start'],i*dt,'PERIOD_START');close(row['end'],(i+1)*dt,'PERIOD_END')
            close(row['mass'],mass,'PERIOD_MASS')
            recipe=row['recipe']
            require(set(recipe)==set(FIELDS),'RECIPE_FIELDS')
            for field in FIELDS:
                require(finite(recipe[field]) and recipe[field] in model['grid'][field],'COMMAND_GRID_'+field)
            require(all(recipe[f]==plan['command'][f] for f in FIELDS[:3]),'HT_COMMAND_CHANGED')
            b,h,a=[recipe[f] for f in FIELDS[3:]];f=1-b-h-a
            require(b+h<=.4+1e-9 and a<=.03 and min(b,h,a,f)>=0,'BLEND_BOUNDS')
            close(f+b+h+a,1,'BLEND_SUM')
            close(row['stocks_before']['clean'],clean,'CLEAN_BEFORE')
            close(row['stocks_before']['heavy'],heavy,'HEAVY_BEFORE')
            expected={'clean':mass*b,'heavy':mass*h,'additive':mass*a,'fresh':mass*f}
            require(set(row['consumption'])==set(expected),'CONSUMPTION_FIELDS')
            for field,value in expected.items():close(row['consumption'][field],value,'MASS_BALANCE_'+field)
            clean-=expected['clean'];heavy-=expected['heavy']
            require(min(clean,heavy)>=-1e-8,'NEGATIVE_INVENTORY')
            close(row['stocks_after']['clean'],max(0,clean),'CLEAN_AFTER')
            close(row['stocks_after']['heavy'],max(0,heavy),'HEAVY_AFTER')
            maxima={'sulfur':-math.inf,'t95':-math.inf,'cn':math.inf,'cost':-math.inf,'severity':-math.inf}
            effective={field:[math.inf,-math.inf] for field in FIELDS[:3]}
            for world in ws:
                for at in (i*dt,(i+1)*dt):
                    value=scalar(state,model,recipe,at,world);calls+=1
                    require(not value['reasons'],'CONSTRAINT_'+','.join(value['reasons']))
                    for field in maxima:
                        maxima[field]=(min if field=='cn' else max)(maxima[field],value[field])
                    for field in effective:
                        effective[field][0]=min(effective[field][0],value['effective'][field])
                        effective[field][1]=max(effective[field][1],value['effective'][field])
            for source,target in [('sulfur','sulfur_max'),('t95','t95_max'),('cn','cn_min')]:
                close(row['quality'][target],maxima[source],'QUALITY_SUMMARY_'+source)
            close(row['severity_max'],maxima['severity'],'SEVERITY_SUMMARY')
            for field,values in effective.items():
                require(len(row['effective'][field])==2,'EFFECTIVE_LENGTH')
                for k in (0,1):close(row['effective'][field][k],values[k],'EFFECTIVE_'+field)
            cost=mass*maxima['cost'];close(row['cost_upper'],cost,'PERIOD_COST');total+=cost
        close(plan['cost_upper'],total,'TOTAL_COST')
        close(plan['unit_cost_upper'],total/(mass*len(timeline)),'UNIT_COST')
        close(plan['delivered_mass'],mass*len(timeline),'DELIVERED_MASS')
        close(plan['unmet_original_mass'],mass*start,'UNMET_ORIGINAL_MASS')
        close(plan['clean_used'],initial_clean-clean,'CLEAN_USED')
        close(plan['heavy_used'],initial_heavy-heavy,'HEAVY_USED')
        return {'passed':True,'reasons':[],'evaluations':calls,'worlds':len(ws),'periods':len(timeline),
                'original_order_complete':start==0}
    except (ValueError,KeyError,TypeError,OverflowError,ZeroDivisionError) as exc:
        return {'passed':False,'reasons':[str(exc)],'evaluations':calls}
