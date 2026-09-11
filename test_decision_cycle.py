"""Behavioral contracts: temporal leakage, hard limits, full-world gate and parity."""
import copy
import json
import math
from pathlib import Path
import unittest
from neft.decision_cycle import run_cycle,example_request,contracts
from neft.cycle_gate import validate_plan,validate_state
from neft.cycle_state import select_state
from neft.dynamic_planner import schedule

ROOT=Path(__file__).resolve().parent
C,P,K,POLICY=contracts()


def observation(**kw):
    return {'id':'lab1','source':'lims','field':'crude_sulfur','value':.4,
            'unit':'mass_percent','event_time':'2024-06-01T08:00:00',
            'provenance':'synthetic laboratory fixture',**kw}


class DecisionContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request=example_request('bridge')
        cls.decision=run_cycle(cls.request)
        cls.plan=cls.decision['full']['primary']['dynamic']

    def test_hard_limit_counterexample(self):
        r=example_request();r['state'].update(sulfur_limit=30,t95_limit=400,cn_min=30,demand=10,clean_stock=0,heavy_stock=0)
        d=run_cycle(r)
        self.assertEqual(d['status'],'INVALID_INPUT')
        self.assertIn('SULFUR_HARD_LIMIT_10',d['reasons'])
        self.assertIsNone(d['recommendation'])

    def test_json_cannot_override_policy(self):
        for key in ('policy','sulfur_hard_max_mg_kg','model','uncertainty'):
            with self.subTest(key=key):
                r=example_request();r[key]={'sulfur_hard_max_mg_kg':30}
                self.assertEqual(run_cycle(r)['status'],'INVALID_INPUT')

    def test_nonfinite_request(self):
        for value in (math.inf,math.nan,-math.inf):
            r=example_request();r['state']['sulfur_limit']=value
            self.assertEqual(run_cycle(r)['reasons'],['NON_JSON_OR_NONFINITE_REQUEST'])

    def test_booleans_are_not_numbers(self):
        r=example_request();r['state']['sulfur_limit']=True
        self.assertEqual(run_cycle(r)['status'],'INVALID_INPUT')

    def test_upper_bound_flag_strict(self):
        r=example_request();r['use_lims_upper_bound']='false'
        self.assertEqual(run_cycle(r)['status'],'INVALID_INPUT')

    def test_sample_time_is_not_publication(self):
        r=example_request();r.update(origin='2024-06-01T08:00:00',observations=[observation()],bindings={'crude_sulfur':'crude_sulfur'})
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_DATA')
        self.assertNotIn('crude_sulfur',run_cycle(r)['model_input'])

    def test_four_hour_boundary(self):
        r=example_request();r.update(use_lims_upper_bound=True,observations=[observation()],bindings={'crude_sulfur':'crude_sulfur'})
        r['origin']='2024-06-01T11:59:59'
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_DATA')
        r['origin']='2024-06-01T12:00:00'
        a=select_state(r,POLICY)
        self.assertEqual(a.state['crude_sulfur'],.4)
        self.assertIsNone(a.selected['crude_sulfur']['availability']['actual_publication'])
        self.assertEqual(a.selected['crude_sulfur']['availability']['basis'],'reported_upper_bound_not_observed_publication')

    def test_late_receipt(self):
        r=example_request();r.update(use_lims_upper_bound=True,observations=[observation(received_time='2024-06-01T13:00:00')],bindings={'crude_sulfur':'crude_sulfur'})
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_DATA')

    def test_explicit_publication_boundary(self):
        r=example_request();r['observations']=[observation(available_time='2024-06-01T09:00:00')]
        r['origin']='2024-06-01T08:59:59';self.assertFalse(select_state(r,POLICY).selected)
        r['origin']='2024-06-01T09:00:00';self.assertTrue(select_state(r,POLICY).selected)

    def test_future_value_never_enters_state_or_trace(self):
        r=example_request();r['observations']=[observation(event_time='2024-06-01T13:00:00',value=9999)]
        a=select_state(r,POLICY)
        self.assertFalse(a.selected);self.assertEqual(a.state,r['state'])
        self.assertNotIn('9999',json.dumps(a.summary()))
        r['observations'][0]['value']=-9999
        self.assertEqual(select_state(r,POLICY),a)

    def test_pac_unknown_or_unhealthy(self):
        r=example_request();r['observations']=[observation(source='pac',event_time='2024-06-01T11:30:00',available_time='2024-06-01T11:40:00')]
        self.assertFalse(select_state(r,POLICY).selected)
        r['observations'][0].update(event_time_basis='confirmed_measurement',health='failed')
        self.assertFalse(select_state(r,POLICY).selected)
        r['observations'][0]['health']='healthy'
        self.assertTrue(select_state(r,POLICY).selected)
        del r['observations'][0]['available_time']
        self.assertFalse(select_state(r,POLICY).selected)

    def test_source_priority_and_conflict(self):
        r=example_request();r['observations']=[observation(available_time='2024-06-01T09:00:00'),observation(id='pac1',source='pac',event_time='2024-06-01T11:30:00',available_time='2024-06-01T11:40:00',event_time_basis='confirmed_measurement',health='healthy',value=.8)]
        a=select_state(r,POLICY)
        self.assertEqual(a.selected['crude_sulfur']['id'],'lab1')
        self.assertEqual(len(a.conflicts),1)

    def test_stale_lab_allows_eligible_lower_priority(self):
        r=example_request();r['observations']=[observation(event_time='2024-05-30T08:00:00',available_time='2024-05-30T12:00:00'),observation(id='v1',source='virtual',event_time='2024-06-01T11:30:00',available_time='2024-06-01T11:40:00',dependencies_available=True)]
        a=select_state(r,POLICY);self.assertEqual(a.selected['crude_sulfur']['id'],'v1')
        self.assertEqual(a.excluded[0]['reason'],'STALE')

    def test_unit_mismatch_and_no_cross_field_binding(self):
        r=example_request();r.update(observations=[observation(unit='mg/kg',available_time='2024-06-01T09:00:00')],bindings={'crude_sulfur':'crude_sulfur'})
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_DATA')
        r['bindings']={'crude_sulfur':'product_sulfur'}
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_DATA')

    def test_ambiguous_ids_fail_closed(self):
        r=example_request();r['observations']=[observation(),observation()]
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_DATA')

    def test_replay_never_becomes_action(self):
        r=example_request();r['scope']='historical_replay'
        self.assertEqual(run_cycle(r)['status'],'ABSTAIN_HISTORICAL_SCOPE')

    def test_normal_no_change(self):
        r=example_request();r['state'].update(crude_sulfur=.05,feed_cn=60,clean_stock=0,heavy_stock=0)
        d=run_cycle(r);self.assertEqual(d['status'],'NO_CHANGE')
        self.assertEqual(d['recommendation']['command'],{k:C['current'][k] for k in ('temperature','pressure','feed')})
        self.assertTrue(all(t['recipe']==C['current'] for t in d['recommendation']['timeline']))

    def test_conditional_not_promoted(self):
        d=run_cycle(example_request('limited-stock'))
        self.assertEqual(d['status'],'ABSTAIN_NO_FULL_PLAN')
        self.assertIsNone(d['recommendation'])
        self.assertIsNotNone(d['delay_le_1']['primary']['dynamic'])

    def test_deferred_volume_not_original_success(self):
        d=run_cycle(example_request('base'))
        plan=next(f['dynamic'] for f in d['delay_le_1']['frontier'] if f['dynamic'])
        self.assertEqual(plan['unmet_original_mass'],135)
        self.assertEqual(d['status'],'ABSTAIN_NO_FULL_PLAN')

    def test_gate_rechecks_every_claim(self):
        mutations=[('stocks',lambda p:p['timeline'][0]['stocks_after'].update(clean=999)),
                   ('sulfur',lambda p:p['timeline'][0]['quality'].update(sulfur_max=0)),
                   ('cost',lambda p:p.update(cost_upper=0)),
                   ('mass',lambda p:p['timeline'][0].update(mass=0)),
                   ('dose',lambda p:p['timeline'][0]['recipe'].update(additive=.04)),
                   ('time',lambda p:p['timeline'][0].update(end=2)),
                   ('command',lambda p:p['timeline'][0]['recipe'].update(temperature=370)),
                   ('delivery',lambda p:p.update(unmet_original_mass=1)),
                   ('nonfinite',lambda p:p['timeline'][0]['recipe'].update(pressure=math.nan))]
        self.assertTrue(validate_plan(self.request['state'],self.plan,'full',C,P)['passed'])
        for name,mutate in mutations:
            with self.subTest(name=name):
                plan=copy.deepcopy(self.plan);mutate(plan)
                self.assertFalse(validate_plan(self.request['state'],plan,'full',C,P)['passed'])

    def test_gate_refuses_cap_override_even_for_valid_plan(self):
        state={**self.request['state'],'sulfur_limit':30}
        check=validate_plan(state,self.plan,'full',C,P)
        self.assertFalse(check['passed'])
        self.assertIn('SULFUR_HARD_LIMIT_10',check['reasons'])

    def test_role_exchange_is_linked_and_finite(self):
        d=self.decision;seen=set()
        self.assertEqual([m['role'] for m in d['trace']],['state','quality','reliability','optimizer','gate','quality','reliability','optimizer','gate','decision'])
        for msg in d['trace']:
            self.assertTrue(set(msg['consumes'])<=seen)
            self.assertEqual(msg['state_id'],d['state_id']);seen.add(msg['message_id'])
        json.dumps(d,allow_nan=False)
        self.assertFalse(d['industrial_command'])

    def test_dp_avoids_greedy_inventory_failure(self):
        opts=[[{'id':0,'b':1,'h':0,'cost':1},{'id':1,'b':0,'h':1,'cost':2}],
              [{'id':2,'b':1,'h':0,'cost':1}]]
        self.assertEqual(schedule(opts,1,1)['recipes'],[1,2])
        self.assertIsNone(schedule(opts,0,0))

    def test_supported_time_grid(self):
        for change in ({'horizon':0},{'step':20},{'horizon':2.2},{'step':False}):
            r=example_request();r['state'].update(change)
            self.assertEqual(run_cycle(r)['status'],'INVALID_INPUT')
        r=example_request();r['state'].update(step=15,horizon=.5)
        self.assertEqual(run_cycle(r)['periods'],2)


if __name__=='__main__': unittest.main()
