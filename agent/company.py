"""CLI bridge to the same stdio MCP server when the Codex client hides MCP tools."""
import json, subprocess, sys
if len(sys.argv)<3:
    raise SystemExit('用法: python3 /app/company.py TOOL JSON_ARGUMENTS，例如 post_message {"thread_id":2,"body":"你好"}')
name=sys.argv[1]
try:args=json.loads(sys.argv[2])
except json.JSONDecodeError as e:raise SystemExit(f'JSON 参数无效: {e}')
p=subprocess.Popen(['python3','/app/mcp.py'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','clientInfo':{'name':'company-cli','version':'0.1'},'capabilities':{}}},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':name,'arguments':args}}]
out,err=p.communicate(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in requests),timeout=35)
if err:print(err,file=sys.stderr)
for line in out.splitlines():
    x=json.loads(line)
    if x.get('id')==2:
        result=x.get('result',{})
        print(json.dumps(result,ensure_ascii=False))
        if result.get('isError') or 'error' in x:raise SystemExit(1)
        break
else:raise SystemExit('MCP 没有返回工具结果')
