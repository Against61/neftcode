"""Print connection configuration or launch a local Codex session with the tool.

No user configuration file is modified. The current Codex model is preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
DEFAULT_MODEL_MANIFEST=ROOT/'models/manifest.json'
DEFAULT_GENERATED_SOURCES=ROOT/'output/agent-deployment/history_sources.local.json'


def toml(value):
    if isinstance(value,str):return json.dumps(value,ensure_ascii=False)
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,list):return '['+','.join(toml(v) for v in value)+']'
    return '{'+','.join(k+'='+toml(v) for k,v in value.items())+'}'


def configuration(audit_root,data_root=None,history_sources=None,historical_intelligence_bundle=None,historical_intelligence_sha256=None):
    config={'command':sys.executable,'args':['-B',str(ROOT/'scripts/serve_agent_tool.py'),
            '--audit-root',str(Path(audit_root).resolve())],
            'enabled_tools':['get_refinery_contract','recommend_refinery_plan'],
            'startup_timeout_sec':20,'tool_timeout_sec':30,'required':True}
    if history_sources:
        config['args']+=['--data-root',str(Path(data_root).resolve()),'--history-sources',str(Path(history_sources).resolve())]
        config['enabled_tools']+=['get_refinery_history','evaluate_refinery_scenario_with_history']
    if historical_intelligence_bundle:
        config['args']+=['--historical-intelligence-bundle',str(Path(historical_intelligence_bundle).resolve()),
                         '--historical-intelligence-sha256',historical_intelligence_sha256]
        config['enabled_tools']+=['get_refinery_historical_intelligence']
    return config


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def bundled_model(manifest_path=DEFAULT_MODEL_MANIFEST):
    manifest_path=Path(manifest_path).resolve()
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema')!='neft-model-package-v1':
        raise ValueError('INVALID_BUNDLED_MODEL_MANIFEST')
    artifact=manifest.get('artifact',{})
    relative=Path(artifact.get('file',''))
    path=(manifest_path.parent/relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(manifest_path.parent) or not path.is_file():
        raise ValueError('BUNDLED_MODEL_MISSING')
    digest=file_sha256(path)
    if digest!=artifact.get('sha256') or path.stat().st_size!=artifact.get('size_bytes'):
        raise ValueError('BUNDLED_MODEL_HASH_OR_SIZE_MISMATCH')
    return path,digest


def generated_history_sources(data_root,output=DEFAULT_GENERATED_SOURCES):
    from scripts.configure_local_data import build_config
    config=build_config(data_root)
    output=Path(output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix(output.suffix+'.tmp')
    temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(output)
    return output


def resolve_resources(data_root=None,history_sources=None,historical_intelligence_bundle=None,
                      historical_intelligence_sha256=None,*,generated_sources=DEFAULT_GENERATED_SOURCES,
                      use_bundled_intelligence=True):
    if history_sources and not data_root:
        raise ValueError('--history-sources requires --data-root')
    if data_root and not history_sources:
        history_sources=generated_history_sources(data_root,generated_sources)
    if bool(historical_intelligence_bundle)!=bool(historical_intelligence_sha256):
        raise ValueError('--historical-intelligence-bundle and --historical-intelligence-sha256 are required together')
    if history_sources and not historical_intelligence_bundle and use_bundled_intelligence:
        historical_intelligence_bundle,historical_intelligence_sha256=bundled_model()
    if historical_intelligence_bundle and not history_sources:
        raise ValueError('historical intelligence requires history sources')
    return data_root,history_sources,historical_intelligence_bundle,historical_intelligence_sha256


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--format',choices=['json','codex'],default='codex')
    ap.add_argument('--audit-root',type=Path,default=ROOT/'agent-calls')
    ap.add_argument('--launch',action='store_true')
    ap.add_argument('--data-root',type=Path)
    ap.add_argument('--history-sources',type=Path)
    ap.add_argument('--generated-history-sources',type=Path,default=DEFAULT_GENERATED_SOURCES,
                    help='Ignored local config created automatically when only --data-root is given')
    ap.add_argument('--historical-intelligence-bundle',type=Path)
    ap.add_argument('--historical-intelligence-sha256')
    ap.add_argument('--without-bundled-intelligence',action='store_true',
                    help='Expose archive tools without the repository prediction package')
    ap.add_argument('--codex-command',default=str(ROOT/'.runtime/codex-agent-cli/node_modules/.bin/codex') if (ROOT/'.runtime/codex-agent-cli/node_modules/.bin/codex').exists() else 'codex')
    args=ap.parse_args()
    try:
        data_root,history_sources,bundle,digest=resolve_resources(
            args.data_root,args.history_sources,args.historical_intelligence_bundle,
            args.historical_intelligence_sha256,generated_sources=args.generated_history_sources,
            use_bundled_intelligence=not args.without_bundled_intelligence)
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        ap.error(str(exc))
    config=configuration(args.audit_root,data_root,history_sources,bundle,digest)
    if args.launch:
        return subprocess.call([args.codex_command,'--sandbox','read-only','-C',str(ROOT),
                               '-c','mcp_servers='+toml({'neft':config})])
    if args.format=='json':
        print(json.dumps({'mcpServers':{'neft':{k:config[k] for k in ('command','args')}}},ensure_ascii=False,indent=2))
    else:
        print('[mcp_servers.neft]')
        for k,v in config.items():print(k+' = '+toml(v))
    return 0


if __name__=='__main__':raise SystemExit(main())
