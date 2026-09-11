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


async def serve(audit_root):
    service=ToolService(audit_root)
    server=Server('neft-refinery',version=POLICY['id'],instructions=POLICY['agent_contract'])
    lock=anyio.Lock()
    @server.list_tools()
    async def list_tools():
        return [Tool(**item,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,
                                                       idempotentHint=False,openWorldHint=False)) for item in descriptions()]
    @server.call_tool(validate_input=False)
    async def call_tool(name,arguments):
        # Adapter owns validation so rejected calls get the same audit trail.
        async with lock:
            result=await anyio.to_thread.run_sync(service.call,name,arguments)
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(result['output'],ensure_ascii=False,allow_nan=False))],
                              structuredContent=result['output'],isError=result['is_error'])
    async with stdio_server(stdin=StrictStdin(service.audit_root)) as (read,write):
        await server.run(read,write,server.create_initialization_options())


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--audit-root',type=Path,default=ROOT/'agent-calls')
    args=ap.parse_args();anyio.run(serve,args.audit_root)


if __name__=='__main__':main()
