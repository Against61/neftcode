"""Read-only MCP context from a hash-pinned historical-intelligence bundle."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid

import joblib

from .agent_tool import encoded, POLICY as TOOL_POLICY
from .historical_intelligence import infer_historical_context, telemetry_from_history_rows
from .history_adapter import HistoryArchive, bounds, sha

INTELLIGENCE = 'get_refinery_historical_intelligence'


def description():
    return {'name':INTELLIGENCE,
            'description':('Return a diagnostic sulfur forecast with uncertainty and historically typical future P8/T11/F19 telemetry. '
                           'A locally trained bundle also returns five source-timestamped analogs; the distributed prediction-only bundle '
                           'returns an explicit analog-index status instead of embedding training rows. This tool never returns a plant command '
                           'or optimizer recommendation. Typical controls have unconfirmed units and must not be copied into the scenario optimizer. '
                           'Use quality for a main recommendation only when the full returned range satisfies the hard sulfur limit.'),
            'inputSchema':{'type':'object','properties':{
                'as_of':{'type':'string','description':'ISO source-local time in configured 2023–2024 history'},
                'horizon_minutes':{'type':'integer','enum':[15,30,60,120,180]},
                'sulfur_limit':{'type':'number','exclusiveMinimum':0,'maximum':10,'default':10}},
                'required':['as_of','horizon_minutes'],'additionalProperties':False}}


class HistoricalIntelligenceToolService:
    def __init__(self,audit_root,data_root,sources,bundle_path,bundle_sha256):
        self.audit_root=Path(audit_root).resolve();self.audit_root.mkdir(parents=True,exist_ok=True)
        self.data_root=Path(data_root).resolve();self.sources=Path(sources).resolve()
        self.bundle_path=Path(bundle_path).resolve()
        if not isinstance(bundle_sha256,str) or len(bundle_sha256)!=64 or sha(self.bundle_path)!=bundle_sha256:
            raise ValueError('HISTORICAL_BUNDLE_MISSING_OR_HASH_MISMATCH')
        self.bundle_sha256=bundle_sha256;self.bundle=joblib.load(self.bundle_path)
        if self.bundle.get('schema')!='neft-historical-intelligence-v1':raise ValueError('INVALID_HISTORICAL_BUNDLE')

    def call(self,arguments):
        call_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:12]
        folder=self.audit_root/call_id;folder.mkdir();started=time.monotonic()
        def save(name,value):(folder/name).write_bytes(encoded(value)+b'\n')
        result={'tool':INTELLIGENCE,'call_id':call_id,'scope':'historical_diagnostic_context',
                'recommendation':None,'industrial_command':False,'audit_manifest':str(folder/'manifest.json')}
        error=None
        try:
            raw=encoded(arguments)
            if len(raw)>TOOL_POLICY['max_request_bytes']:raise ValueError('REQUEST_TOO_LARGE')
            save('arguments.json',arguments)
            if not isinstance(arguments,dict) or set(arguments)-{'as_of','horizon_minutes','sulfur_limit'}:
                raise ValueError('HISTORICAL_INTELLIGENCE_ARGUMENTS_SCHEMA')
            if set(arguments)<{'as_of','horizon_minutes'}:raise ValueError('AS_OF_AND_HORIZON_REQUIRED')
            bounds(arguments['as_of'])
            if arguments['horizon_minutes'] not in (15,30,60,120,180):raise ValueError('UNSUPPORTED_HORIZON')
            limit=arguments.get('sulfur_limit',10)
            if isinstance(limit,bool) or not isinstance(limit,(int,float)) or not math.isfinite(limit) or not 0<limit<=10:
                raise ValueError('SULFUR_HARD_LIMIT_10')
            if sha(self.bundle_path)!=self.bundle_sha256:raise ValueError('HISTORICAL_BUNDLE_CHANGED')
            source_config=json.loads(self.sources.read_text())
            archive=HistoryArchive(self.data_root,source_config,[arguments['as_of']])
            telemetry=telemetry_from_history_rows(archive.rows)
            context=infer_historical_context(self.bundle,telemetry,arguments['as_of'],arguments['horizon_minutes'],sulfur_limit=limit)
            archive.verify()
            if sha(self.bundle_path)!=self.bundle_sha256:raise ValueError('HISTORICAL_BUNDLE_CHANGED')
            result.update(status='HISTORICAL_CONTEXT',context=context,
                          data_provenance={'telemetry_sha256':source_config['telemetry']['sha256'],
                                           'bundle_sha256':self.bundle_sha256,
                                           'telemetry_rows_read':len(archive.rows),
                                           'later_numeric_values_parsed':archive.telemetry_audit['later_numeric_values_parsed'],
                                           'pac_values_read':0},
                          limitations=context['limitations'])
        except (ValueError,TypeError,OSError,KeyError,RecursionError) as exc:
            error=str(exc);result.update(status='TOOL_ERROR',reasons=[error],context=None)
        save('response.json',result)
        save('manifest.json',{'call_id':call_id,'tool':INTELLIGENCE,
            'status':'tool_error' if error else 'completed','seconds':time.monotonic()-started,
            'bundle_sha256':self.bundle_sha256,'model_fits':0,'industrial_commands':0,
            'artifacts_sha256':{str(p.relative_to(folder)):sha(p) for p in sorted(folder.rglob('*')) if p.is_file()}})
        return {'is_error':bool(error),'output':result}
