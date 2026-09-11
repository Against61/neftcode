import json
from pathlib import Path
import unittest
from neft.expert_contracts import SOURCE_SHA,LIMS_NAME,publication_gate,evaluate_formula

class ExpertContractTests(unittest.TestCase):
    def test_upper_bound_boundary_and_no_fake_publication(self):
        for origin,allowed in [('2024-01-01T13:59:59',False),('2024-01-01T14:00:00',True)]:
            r=publication_gate('lims','2024-01-01T10:00:00',origin,use_reported_upper_bound=True,source_sha=SOURCE_SHA)
            self.assertEqual(r['allowed'],allowed);self.assertIsNone(r['actual_publication'])
            self.assertEqual(r['eligibility_time'],'2024-01-01T14:00:00')
    def test_pac_never_inherits_lims_bound(self):
        self.assertFalse(publication_gate('pac','2024-01-01T10:00:00','2024-01-02',use_reported_upper_bound=True,source_sha=SOURCE_SHA)['allowed'])
    def test_explicit_publication_and_later_receipt(self):
        r=publication_gate('lims','2024-01-01T10:00:00','2024-01-01T15:00:00',published='2024-01-01T12:00:00',received='2024-01-01T16:00:00')
        self.assertFalse(r['allowed']);self.assertEqual(r['actual_publication'],'2024-01-01T12:00:00')
    def test_unknown_and_wrong_source_remain_unavailable(self):
        for kw in ({},{'use_reported_upper_bound':True,'source_sha':'other'}):
            self.assertFalse(publication_gate('lims','2024-01-01','2024-01-03',**kw)['allowed'])
    def test_clock_and_bad_order(self):
        with self.assertRaises(ValueError):publication_gate('lims','2024-01-01','2024-01-03',published='2023-12-31')
        with self.assertRaises(ValueError):publication_gate('lims','2024-01-01T00:00:00+00:00','2024-01-03')
    def test_arithmetic_rejects_code_and_bad_values(self):
        for x in ('__import__("os")','x.real','x[0]','x**2','[x]'):
            with self.assertRaises(ValueError):evaluate_formula(x,{'x':1})
        for v in (float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):evaluate_formula('x+1',{'x':v})
        with self.assertRaises(ValueError):evaluate_formula('x/0',{'x':1})
        with self.assertRaises(ValueError):evaluate_formula('absent',{})
    def test_lims_gate_required(self):
        with self.assertRaises(ValueError):evaluate_formula(LIMS_NAME,{LIMS_NAME:350})
        r=publication_gate('lims','2024-01-01','2024-01-01T04:00:00',use_reported_upper_bound=True,source_sha=SOURCE_SHA)
        self.assertEqual(evaluate_formula(LIMS_NAME,{LIMS_NAME:350},lims_evidence=r),350)
    def test_formula_arithmetic_against_independent_calculation(self):
        values={'x':3.5,'y':8,'z':2}
        expected=2*values['x']+values['y']/values['z']-1.25
        self.assertAlmostEqual(
            evaluate_formula('2*x+y/z-1.25',values),expected,places=12)
if __name__=='__main__':unittest.main()
