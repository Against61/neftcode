"""Local MCP stdio server: tool discovery and bounded refinery calculations."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool,ToolAnnotations,CallToolResult,TextContent
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from neft.agent_tool import ToolService,descriptions,strict_loads,POLICY


class StrictStdin:
    """Validate raw protocol JSON before the SDK can collapse duplicate keys."""
    def __init__(self,audit_root):self.audit_root=audit_root
    def __aiter__(self):return self
    async def __anext__(self):
        limit=POLICY['max_request_bytes']+8192
        line=await anyio.to_thread.run_sync(sys.stdin.buffer.readline,limit+1)
        if not line:raise StopAsyncIteration
        length=len(line);h=hashlib.sha256(line);reason=None
        if len(line)>limit:
            reason='PROTOCOL_REQUEST_TOO_LARGE'
            while not line.endswith(b'\n'):
                line=await anyio.to_thread.run_sync(sys.stdin.buffer.readline,limit+1)
                length+=len(line);h.update(line)
                if not line:break
        try:
            if reason:raise ValueError(reason)
            text=line.decode('utf-8');strict_loads(text)
            return text
        except (ValueError,UnicodeDecodeError,RecursionError) as exc:
            reason=reason or ('NONFINITE_JSON' if 'NONFINITE' in str(exc) else 'DUPLICATE_JSON_KEY' if 'DUPLICATE' in str(exc) else 'MALFORMED_JSON')
            with (self.audit_root/'transport-errors.jsonl').open('a') as f:
                f.write(json.dumps({'reason':reason,'bytes':length,'sha256':h.hexdigest()})+'\n')
            # SDK emits an error notification for a malformed transport frame; no tool sees it.
            return '{invalid-json}\n'


async def serve(audit_root, data_root=None, history_sources=None, intelligence_bundle=None, intelligence_sha256=None):
    service=ToolService(audit_root)
    history_service=scenario_service=intelligence_service=None
    if history_sources is not None:
        from neft.history_tool import HistoryToolService,HISTORY,description
        from neft.operator_tool import HistoryScenarioToolService,SCENARIO,description as scenario_description
        history_service=HistoryToolService(audit_root,data_root,history_sources)
        scenario_service=HistoryScenarioToolService(audit_root,data_root,history_sources)
        if intelligence_bundle is not None:
            from neft.historical_intelligence_tool import HistoricalIntelligenceToolService
            intelligence_service=HistoricalIntelligenceToolService(audit_root,data_root,history_sources,
                                                                    intelligence_bundle,intelligence_sha256)
    server=Server('neft-refinery',version=POLICY['id'],instructions=POLICY['agent_contract'])
    lock=anyio.Lock()
    @server.list_tools()
    async def list_tools():
        items=descriptions()+([description(),scenario_description()] if history_service else [])
        if intelligence_service:
            from neft.historical_intelligence_tool import description as intelligence_description
            items.append(intelligence_description())
        return [Tool(**item,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,
                                                       idempotentHint=False,openWorldHint=False)) for item in items]
    @server.call_tool(validate_input=False)
    async def call_tool(name,arguments):
        # Adapter owns validation so rejected calls get the same audit trail.
        async with lock:
            if history_service and name==HISTORY:
                result=await anyio.to_thread.run_sync(history_service.call,arguments)
            elif scenario_service and name==SCENARIO:
                result=await anyio.to_thread.run_sync(scenario_service.call,arguments)
            elif intelligence_service and name=='get_refinery_historical_intelligence':
                result=await anyio.to_thread.run_sync(intelligence_service.call,arguments)
            else:
                result=await anyio.to_thread.run_sync(service.call,name,arguments)
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(result['output'],ensure_ascii=False,allow_nan=False))],
                              structuredContent=result['output'],isError=result['is_error'])
    async with stdio_server(stdin=StrictStdin(service.audit_root)) as (read,write):
        await server.run(read,write,server.create_initialization_options())


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--audit-root',type=Path,default=ROOT/'agent-calls')
    ap.add_argument('--data-root',type=Path)
    ap.add_argument('--history-sources',type=Path)
    ap.add_argument('--historical-intelligence-bundle',type=Path)
    ap.add_argument('--historical-intelligence-sha256')
    args=ap.parse_args()
    if bool(args.data_root)!=bool(args.history_sources):ap.error('--data-root and --history-sources are required together')
    if bool(args.historical_intelligence_bundle)!=bool(args.historical_intelligence_sha256):ap.error('--historical-intelligence-bundle and --historical-intelligence-sha256 are required together')
    if args.historical_intelligence_bundle and not args.history_sources:ap.error('historical intelligence requires configured history sources')
    anyio.run(serve,args.audit_root,args.data_root,args.history_sources,args.historical_intelligence_bundle,args.historical_intelligence_sha256)


if __name__=='__main__':main()
