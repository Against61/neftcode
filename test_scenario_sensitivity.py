import unittest
import numpy as np
from neft.scenario_sensitivity import matrix,choose_nominal

class SensitivityTests(unittest.TestCase):
    def fixture(self):
        c={'current':{'temperature':340,'pressure':4,'feed':100},'model':{'response_ramp_hours':.5,'sulfur_partition':.5,'additive_cn_gain':400,'clean_cost':1.6,'heavy_cost':.85,'yield':.2,'ht_yield':.98,'sulfur_margin':.5,'t95_margin':1,'cetane_margin':.5,'severity_limit':.85},'expert_basis':{'additive_cost_ratio':100}}
        s=dict(crude_sulfur=.8,horizon=2,feed_t95=350,feed_cn=47,clean_s=2,heavy_s=30,clean_t95=320,heavy_t95=375,clean_cn=60,heavy_cn=43,demand=90,crude_flow=500,clean_capacity=30,heavy_capacity=20,sulfur_limit=10,t95_limit=355,cn_min=51)
        a=np.array([[350,4,100,.2,0,.01],[340,4,100,0,0,0]])
        return s,c,a,np.array([[1,0,1,1,1,1]],float)
    def test_nominal_hand_value(self):
        s,c,a,w=self.fixture();r=matrix(s,c,a,w)
        self.assertAlmostEqual(r['sulfur'][0,1],4000*np.exp(-5.8));self.assertAlmostEqual(r['cost'][0,1],1.06)
    def test_axes_act_on_intended_terms(self):
        s,c,a,w=self.fixture();base=matrix(s,c,a,w)
        for axis,value,field,sign in [(0,.9,'sulfur',1),(1,5,'t95',1),(2,.75,'cn',-1),(3,1.25,'cost',1),(4,1.2,'cost',1)]:
            v=w.copy();v[0,axis]=value;r=matrix(s,c,a,v)
            self.assertGreater(sign*(r[field][0,0]-base[field][0,0]),0)
        v=w.copy();v[0,1]=5;self.assertEqual(matrix(s,c,a,v)['t95'][0,1],base['t95'][0,1])
    def test_before_delay_commands_have_no_effect(self):
        s,c,a,w=self.fixture();w[0,5]=3;a[1,:3]=[370,5,120];a[0,3:]=0
        r=matrix(s,c,a,w);self.assertEqual(r['sulfur'][0,0],r['sulfur'][0,1]);self.assertEqual(r['cost'][0,0],r['cost'][0,1])
    def test_infeasible_never_beats_feasible_and_ties_distance(self):
        i,v=choose_nominal(np.array([.1,2.,2.]),np.array([False,True,True]),np.array([0,2,1]));self.assertEqual(i,2);self.assertEqual(v,2)
        self.assertEqual(choose_nominal(np.array([.1]),np.array([False]),np.array([0])),(-1,None))
    def test_gate_quality_failure_bits(self):
        s,c,a,w=self.fixture();s['sulfur_limit']=.1;s['cn_min']=75;s['t95_limit']=280
        r=matrix(s,c,a,w);self.assertTrue(((r['failures']&(1<<4))>0).all());self.assertTrue(((r['failures']&(1<<5))>0).all());self.assertTrue(((r['failures']&(1<<6))>0).all())
if __name__=='__main__':unittest.main()
