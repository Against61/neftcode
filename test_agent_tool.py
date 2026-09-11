import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from neft.agent_tool import ToolService,CALCULATE,CONTRACT,strict_loads,output_admissible,POLICY
from neft.decision_cycle import example_request,run_cycle


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='neft-tool-tests-')
        self.addCleanup(self.temp.cleanup)
        self.service=ToolService(self.temp.name)

    def test_contract_is_explicitly_synthetic(self):
        r=self.service.call(CONTRACT,{})
        self.assertFalse(r['is_error']);self.assertEqual(r['output']['example_label'],'SYNTHETIC_EXAMPLE_NOT_PLANT_OBSERVATION')
        self.assertEqual(r['output']['input_schema']['properties']['state']['properties']['sulfur_limit']['maximum'],10)

    def test_worker_and_direct_api_match(self):
        request=example_request('bridge');r=self.service.call(CALCULATE,{'request':request})
        self.assertFalse(r['is_error']);self.assertEqual(r['output']['status'],'MODEL_PLAN')
        d=json.loads(Path(r['output']['artifacts']['decision_json']).read_text())
        self.assertEqual(d,json.loads(json.dumps(run_cycle(request),allow_nan=False)));self.assertEqual(r['output']['recommendation'],d['recommendation'])

    def test_unknown_tool(self):
        r=self.service.call('write_file',{'path':'bad'})
        self.assertTrue(r['is_error']);self.assertEqual(r['output']['reasons'],['UNKNOWN_TOOL'])

    def test_cannot_set_output_path_timeout_or_policy(self):
        for key in ('audit_root','timeout','model','policy','output','command'):
            with self.subTest(key=key):
                r=self.service.call(CALCULATE,{'request':example_request(),key:'arbitrary'})
                self.assertTrue(r['is_error']);self.assertIsNone(r['output']['recommendation'])

    def test_hard_limit_is_business_refusal(self):
        r=example_request();r['state']['sulfur_limit']=30
        result=self.service.call(CALCULATE,{'request':r})
        self.assertFalse(result['is_error']);self.assertEqual(result['output']['status'],'INVALID_INPUT')
        self.assertIn('SULFUR_HARD_LIMIT_10',result['output']['reasons']);self.assertIsNone(result['output']['recommendation'])

    def test_nonfinite_arguments(self):
        r=example_request();r['state']['sulfur_limit']=float('nan')
        self.assertEqual(self.service.call(CALCULATE,{'request':r})['output']['reasons'],['NONFINITE_OR_NON_JSON_ARGUMENTS'])

    def test_oversize_request_not_executed(self):
        with patch('neft.agent_tool.subprocess.run') as worker:
            r=self.service.call(CALCULATE,{'request':{'oversize':'x'*(POLICY['max_request_bytes']+1)}})
            worker.assert_not_called();self.assertEqual(r['output']['reasons'],['REQUEST_TOO_LARGE'])

    def test_no_conditional_promotion(self):
        r=self.service.call(CALCULATE,{'request':example_request('limited-stock')})['output']
        self.assertIsNone(r['recommendation']);self.assertEqual(r['status'],'ABSTAIN_NO_FULL_PLAN')
        self.assertTrue(r['conditional_diagnostic']['primary_plan_available'])
        self.assertNotIn('command',r['conditional_diagnostic'])

    def test_timeout_has_audit_and_no_stale_result(self):
        with patch('neft.agent_tool.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',20)):
            r=self.service.call(CALCULATE,{'request':example_request()})
        self.assertTrue(r['is_error']);self.assertEqual(r['output']['reasons'],['WORKER_TIMEOUT'])
        self.assertIsNone(r['output']['recommendation'])
        m=json.loads(Path(r['output']['audit_manifest']).read_text());self.assertEqual(m['status'],'tool_error')

    def test_failed_worker_not_reported_as_business_refusal(self):
        with patch('neft.agent_tool.subprocess.run',return_value=subprocess.CompletedProcess([],9)):
            r=self.service.call(CALCULATE,{'request':example_request()})
        self.assertTrue(r['is_error']);self.assertEqual(r['output']['reasons'],['WORKER_FAILED'])

    def test_duplicate_json_rejected_before_parse(self):
        for text in ('{"request":{},"request":{}}','{"value":NaN}','{"value":Infinity}','{"value":1e999}'):
            with self.assertRaises(ValueError):strict_loads(text)

    def test_unique_calls_and_audit_hashes(self):
        import hashlib
        ids=[self.service.call(CONTRACT,{})['output']['call_id'] for _ in range(2)]
        self.assertEqual(len(set(ids)),2)
        for call_id in ids:
            folder=Path(self.temp.name)/call_id;m=json.loads((folder/'manifest.json').read_text())
            for name,digest in m['artifacts_sha256'].items():self.assertEqual(hashlib.sha256((folder/name).read_bytes()).hexdigest(),digest)

    def test_output_gate_rejects_forged_promotion(self):
        d=run_cycle(example_request('limited-stock'));self.assertTrue(output_admissible(d))
        d['status']='MODEL_PLAN';d['recommendation']=d['delay_le_1']['primary']['dynamic']
        self.assertFalse(output_admissible(d))

    def test_empty_or_wrong_argument_shape(self):
        for arg in ({},[],{'request':[]},{'request':None}):
            self.assertTrue(self.service.call(CALCULATE,arg)['is_error'])


if __name__=='__main__':unittest.main()
