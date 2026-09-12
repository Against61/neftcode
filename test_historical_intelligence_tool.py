import json
from pathlib import Path
import tempfile
import unittest

import joblib
import numpy as np

from neft.features import build_features
from neft.historical_intelligence import telemetry_from_history_rows
from neft.historical_intelligence_tool import HistoricalIntelligenceToolService, INTELLIGENCE
from neft.history_adapter import HistoryArchive, sha
from scripts.agent_connection import configuration
from test_history_adapter import fixture, ORIGIN


class ConstantModel:
    def __init__(self,value):self.value=value
    def predict(self,x):return np.full(len(x),self.value)


class HistoricalIntelligenceToolTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='neft-intelligence-tool-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.sources=fixture(self.root)
        self.source_file=self.root/'sources.json';self.source_file.write_text(json.dumps(self.sources))
        cfg={'experiment_id':'EXP-0020','mode':'offline_research_only','horizons_minutes':[15,30,60,120,180],
             'splits':{'fit_start':'2023-01-01','calibration_start':'2024-07-01','holdout_start':'2024-10-01','holdout_end_exclusive':'2025-01-01'},
             'control_targets':['ht:P8','ht:T11','ht:F19'],'agent_contract':{'pac_included':False},
             'quality_baseline':{'availability_rule':'sample_time_plus_4h_conservative_upper_bound'},
             'budget':{'max_real_data_model_fits':20},
             'feature_spec':{'assumed_telemetry_latency_minutes':10,'max_telemetry_age_minutes':20,
                             'excluded_channels':[],'lags_minutes':[0],'windows_minutes':[60]},
             'candidate_models':{'historical_analogs':{'minimum_time_separation_hours':24}},
             'uncertainty':{'nominal_coverage':.9,'scope':'empirical test interval'}}
        archive=HistoryArchive(self.root,self.sources,[ORIGIN]);telemetry=telemetry_from_history_rows(archive.rows)
        feature=build_features(telemetry,[ORIGIN],cfg['feature_spec'])
        fit_x=np.vstack([feature.to_numpy()[0],feature.to_numpy()[0]+.01])
        quality={str(h):{'selected':'hgb','model':ConstantModel(8),'radius':3,'fit_x':fit_x,
                         'fit_y':np.array([7.,9.]),'fit_times':np.array(['2023-01-01','2023-02-01'],dtype='datetime64[D]'),
                         'feature_names':feature.columns.tolist()} for h in cfg['horizons_minutes']}
        controls={str(h):{c:{'selected':'no_change','model':ConstantModel(0),'radius':1}
                          for c in cfg['control_targets']} for h in cfg['horizons_minutes']}
        self.bundle=self.root/'bundle.joblib';joblib.dump({'schema':'neft-historical-intelligence-v1','config':cfg,
            'quality':quality,'controls':controls,'limitations':['DIAGNOSTIC_ONLY']},self.bundle)
        self.service=HistoricalIntelligenceToolService(self.root/'calls',self.root,self.source_file,self.bundle,sha(self.bundle))

    def test_read_only_context_has_forecast_controls_analogs_and_audit(self):
        result=self.service.call({'as_of':ORIGIN,'horizon_minutes':60})
        self.assertFalse(result['is_error']);out=result['output'];self.assertEqual(out['tool'],INTELLIGENCE)
        self.assertEqual(out['status'],'HISTORICAL_CONTEXT');self.assertIsNone(out['recommendation']);self.assertFalse(out['industrial_command'])
        self.assertFalse(out['context']['quality_forecast']['full_range_within_sulfur_limit'])
        self.assertEqual(len(out['context']['analogs']),2);self.assertEqual(out['context']['pac'],'EXCLUDED')
        self.assertFalse(out['data_provenance']['later_numeric_values_parsed'])
        self.assertTrue(Path(out['audit_manifest']).is_file())

    def test_agent_cannot_change_bundle_paths_horizon_or_hard_limit(self):
        for args in ({'as_of':ORIGIN,'horizon_minutes':45},
                     {'as_of':ORIGIN,'horizon_minutes':60,'sulfur_limit':30},
                     {'as_of':ORIGIN,'horizon_minutes':60,'bundle':'other'}):
            result=self.service.call(args);self.assertTrue(result['is_error']);self.assertIsNone(result['output']['recommendation'])

    def test_bundle_is_hash_pinned(self):
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):
            HistoricalIntelligenceToolService(self.root/'bad',self.root,self.source_file,self.bundle,'0'*64)

    def test_agent_connection_exposes_intelligence_only_with_pinned_bundle(self):
        digest=sha(self.bundle)
        config=configuration(self.root/'audit',self.root,self.source_file,self.bundle,digest)
        self.assertIn(INTELLIGENCE,config['enabled_tools'])
        self.assertIn('--historical-intelligence-bundle',config['args'])
        self.assertIn(digest,config['args'])


if __name__=='__main__':unittest.main()
