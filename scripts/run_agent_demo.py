"""Bounded real Codex/MCP smoke test; no persistent client configuration changes."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]

PROMPT='''Проверь подключённый инструмент neft. Это тест только на синтетических данных.
Не читай файлы, не используй shell, веб или другие инструменты и ничего не редактируй.
1. Вызови get_refinery_contract ровно один раз.
2. Возьми example_request без изменений и передай его в recommend_refinery_plan.
3. Сделай отдельный тест отказа: тот же example_request, но state.sulfur_limit=30.
Всего ровно три вызова MCP. Затем остановись. Не исправляй отклонённое задание автоматически.
Ответь по-русски кратко: статус первого расчёта, его основная команда ГТ и выпуск;
статус второго и причина отказа. Отметь, что это модельный расчёт, а условный
результат delay<=1ч не является основной рекомендацией. Укажи call_id обоих расчётов.
Не заявляй доказанный промышленный эффект. Не создавай новые задачи и не запускай подагентов.'''


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--codex-command',default=str(ROOT/'.runtime/codex-agent-cli/node_modules/.bin/codex') if (ROOT/'.runtime/codex-agent-cli/node_modules/.bin/codex').exists() else 'codex');args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    (out/'prompt.txt').write_text(PROMPT)
    config={'command':sys.executable,'args':['-B',str(ROOT/'scripts/serve_agent_tool.py'),'--audit-root',str(out/'calls')],
            'enabled_tools':['get_refinery_contract','recommend_refinery_plan'],'startup_timeout_sec':20,'tool_timeout_sec':30,'required':True}
    def toml(x):
        if isinstance(x,str):return json.dumps(x,ensure_ascii=False)
        if isinstance(x,bool):return 'true' if x else 'false'
        if isinstance(x,(int,float)):return str(x)
        if isinstance(x,list):return '['+','.join(toml(i) for i in x)+']'
        return '{'+','.join(k+'='+toml(v) for k,v in x.items())+'}'
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='neft-codex-demo-') as cwd:
        cmd=[args.codex_command,'exec','--ephemeral','--skip-git-repo-check','--sandbox','read-only','--json',
             '-C',cwd,'-c','mcp_servers='+toml({'neft':config}),'-o',str(out/'answer.md'),'-']
        (out/'command.json').write_text(json.dumps(cmd,ensure_ascii=False,indent=2))
        with (out/'events.jsonl').open('w') as stdout,(out/'stderr.txt').open('w') as stderr:
            child=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,text=True,start_new_session=True)
            try:child.communicate(PROMPT,timeout=180);status='completed' if child.returncode==0 else 'failed'
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                status='timeout'
    result={'status':status,'returncode':child.returncode,'seconds':time.monotonic()-started,
            'persistent_config_changed':False,'language_model_invoked':True,'model_override':None,
            'production_rows_read':0,'model_fits':0}
    (out/'execution.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result));return 0 if status=='completed' else 1


if __name__=='__main__':raise SystemExit(main())
