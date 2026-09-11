"""Print connection configuration or launch a local Codex session with the tool.

No user configuration file is modified. The current Codex model is preserved.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]


def toml(value):
    if isinstance(value,str):return json.dumps(value,ensure_ascii=False)
    if isinstance(value,bool):return 'true' if value else 'false'
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,list):return '['+','.join(toml(v) for v in value)+']'
    return '{'+','.join(k+'='+toml(v) for k,v in value.items())+'}'


def configuration(audit_root):
    return {'command':sys.executable,'args':['-B',str(ROOT/'scripts/serve_agent_tool.py'),
            '--audit-root',str(Path(audit_root).resolve())],
            'enabled_tools':['get_refinery_contract','recommend_refinery_plan'],
            'startup_timeout_sec':20,'tool_timeout_sec':30,'required':True}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--format',choices=['json','codex'],default='codex')
    ap.add_argument('--audit-root',type=Path,default=ROOT/'agent-calls')
    ap.add_argument('--launch',action='store_true')
    ap.add_argument('--codex-command',default=str(ROOT/'.runtime/codex-agent-cli/node_modules/.bin/codex') if (ROOT/'.runtime/codex-agent-cli/node_modules/.bin/codex').exists() else 'codex')
    args=ap.parse_args();config=configuration(args.audit_root)
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
