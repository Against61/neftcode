import copy
import unittest

import numpy as np
import pandas as pd

from neft.historical_intelligence import (analog_predictions, interval_metrics,
    infer_historical_context, last_eligible_lims, masks, select_candidate,
    target_at_times, unsafe_low_count, validate_config)


class ConstantModel:
    def __init__(self, value): self.value=value
    def predict(self, x): return np.full(len(x),self.value)


class HistoricalIntelligenceTests(unittest.TestCase):
    def test_lims_is_hidden_until_explicit_plus_four_hour_boundary(self):
        times = pd.DatetimeIndex(['2024-01-01 12:00', '2024-01-02 12:00'])
        values = np.array([7., 9.])
        pred, ids = last_eligible_lims(times, values,
            pd.DatetimeIndex(['2024-01-01 15:59:59', '2024-01-01 16:00:00', '2024-01-02 15:00:00']))
        self.assertTrue(np.isnan(pred[0])); self.assertEqual(ids[0], -1)
        self.assertEqual(pred[1], 7.); self.assertEqual(ids[1], 0)
        self.assertEqual(pred[2], 7.); self.assertEqual(ids[2], 0)

    def test_stale_lims_is_not_silently_carried(self):
        pred, _ = last_eligible_lims(pd.DatetimeIndex(['2024-01-01']), [7],
            pd.DatetimeIndex(['2024-01-09 00:00:01']), maximum_age_hours=168)
        self.assertTrue(np.isnan(pred[0]))

    def test_analogs_have_provenance_and_respect_minimum_separation(self):
        x = np.array([[0., 0.], [1., 1.], [2., 2.]])
        p, proof, _ = analog_predictions(x, [5., 6., 7.], [[1.1, 1.1]],
            pd.DatetimeIndex(['2024-01-01 00:00', '2024-01-02 00:00', '2024-01-03 00:00']),
            pd.DatetimeIndex(['2024-01-02 00:00']), neighbors=2, minimum_time_separation_hours=24)
        self.assertEqual(p[0], 6.)
        self.assertEqual([r['train_index'] for r in proof[0]], [2, 0])
        self.assertTrue(all(abs((pd.Timestamp(r['event_time'])-pd.Timestamp('2024-01-02')).total_seconds()) >= 86400 for r in proof[0]))
        with self.assertRaisesRegex(ValueError, 'METRIC'):
            analog_predictions(x, [5.,6.,7.], [[1.,1.]], pd.date_range('2024-01-01',periods=3),
                               pd.DatetimeIndex(['2024-02-01']), metric='cosine')

    def test_selection_keeps_baseline_on_tie(self):
        self.assertEqual(select_candidate({'baseline': {'mae': 1.}, 'model': {'mae': 1.}}), 'baseline')
        self.assertEqual(select_candidate({'baseline': {'mae': 2.}, 'model': {'mae': 1.}}), 'model')

    def test_intervals_use_fixed_radius(self):
        self.assertEqual(interval_metrics([1, 4], [2, 3], 1), {'coverage': 1.0, 'mean_width': 2.0, 'n': 2})
        self.assertEqual(unsafe_low_count([11], [9], radius=0), 1)
        self.assertEqual(unsafe_low_count([11], [9], radius=3), 0)

    def test_future_control_target_is_separate_from_no_change_cutoff(self):
        index = pd.date_range('2024-01-01', periods=4, freq='10min')
        telemetry = pd.DataFrame({'ht:P8': [1., 2., 4., 8.]}, index=index)
        target = target_at_times(telemetry, pd.DatetimeIndex(['2024-01-01 00:15']), ['ht:P8'])
        self.assertEqual(target[0, 0], 3.)

    def test_splits_are_disjoint_and_holdout_is_q4(self):
        cfg = {'splits': {'fit_start':'2023-01-01','calibration_start':'2024-07-01',
                          'holdout_start':'2024-10-01','holdout_end_exclusive':'2025-01-01'}}
        result = masks(pd.DatetimeIndex(['2024-06-30','2024-07-01','2024-10-01','2025-01-01']), cfg)
        self.assertEqual(result['fit'].tolist(), [True,False,False,False])
        self.assertEqual(result['calibration'].tolist(), [False,True,False,False])
        self.assertEqual(result['holdout'].tolist(), [False,False,True,False])
        self.assertTrue(np.all(sum(v.astype(int) for v in result.values()) <= 1))

    def test_contract_rejects_pac_or_fit_budget_change(self):
        cfg = {'experiment_id':'EXP-0020','mode':'offline_research_only',
               'horizons_minutes':[15,30,60,120,180],
               'splits':{'fit_start':'2023-01-01','calibration_start':'2024-07-01','holdout_start':'2024-10-01','holdout_end_exclusive':'2025-01-01'},
               'control_targets':['ht:P8','ht:T11','ht:F19'],
               'agent_contract':{'pac_included':False},
               'quality_baseline':{'availability_rule':'sample_time_plus_4h_conservative_upper_bound'},
               'budget':{'max_real_data_model_fits':20}}
        validate_config(cfg)
        bad=copy.deepcopy(cfg);bad['agent_contract']['pac_included']=True
        with self.assertRaisesRegex(ValueError,'PAC'):validate_config(bad)
        bad=copy.deepcopy(cfg);bad['budget']['max_real_data_model_fits']=21
        with self.assertRaisesRegex(ValueError,'BUDGET'):validate_config(bad)

    def test_agent_context_keeps_typical_controls_separate_from_recommendation(self):
        cfg={'experiment_id':'EXP-0020','mode':'offline_research_only','horizons_minutes':[15,30,60,120,180],
             'splits':{'fit_start':'2023-01-01','calibration_start':'2024-07-01','holdout_start':'2024-10-01','holdout_end_exclusive':'2025-01-01'},
             'control_targets':['ht:P8','ht:T11','ht:F19'],'agent_contract':{'pac_included':False},
             'quality_baseline':{'availability_rule':'sample_time_plus_4h_conservative_upper_bound'},
             'budget':{'max_real_data_model_fits':20},
             'feature_spec':{'assumed_telemetry_latency_minutes':10,'max_telemetry_age_minutes':20,
                             'excluded_channels':[],'lags_minutes':[0],'windows_minutes':[60]},
             'candidate_models':{'historical_analogs':{'minimum_time_separation_hours':24}},
             'uncertainty':{'nominal_coverage':.9,'scope':'test interval'}}
        names=[f'ht:{c}__{suffix}' for suffix in ('lag_0m','mean_60m') for c in ('P8','T11','F19')]
        fit_x=np.array([[1,2,3,1,2,3],[2,3,4,2,3,4]],float)
        quality={str(h):{'selected':'hgb','model':ConstantModel(8),'radius':3,'fit_x':fit_x,
                         'fit_y':np.array([7.,9.]),'fit_times':np.array(['2023-01-01','2023-02-01'],dtype='datetime64[D]'),
                         'feature_names':names} for h in cfg['horizons_minutes']}
        controls={str(h):{c:{'selected':'no_change','model':ConstantModel(0),'radius':1}
                          for c in cfg['control_targets']} for h in cfg['horizons_minutes']}
        bundle={'schema':'neft-historical-intelligence-v1','config':cfg,'quality':quality,
                'controls':controls,'limitations':['DIAGNOSTIC_ONLY']}
        idx=pd.date_range('2024-01-01',periods=20,freq='10min')
        telemetry=pd.DataFrame({'ht:P8':1.,'ht:T11':2.,'ht:F19':3.},index=idx)
        result=infer_historical_context(bundle,telemetry,idx[-1],60)
        self.assertIsNone(result['recommendation']);self.assertFalse(result['industrial_command'])
        self.assertFalse(result['quality_forecast']['full_range_within_sulfur_limit'])
        self.assertEqual(result['historically_typical_controls']['ht:P8']['meaning'],
                         'historically_typical_telemetry_not_command_or_optimum')


if __name__ == '__main__': unittest.main()
