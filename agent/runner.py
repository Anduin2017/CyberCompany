import asyncio, json, os, shutil, urllib.request
from pathlib import Path

NAME=os.environ['AGENT_NAME']
BASE=os.environ.get('COMPANY_URL','http://server:8000')
HOME=Path('/home/agent')
MODEL=os.environ.get('CODEX_MODEL','gpt-6-luna')
REASONING_EFFORT=os.environ.get('CODEX_REASONING_EFFORT','medium')
SESSION=HOME/'.codex-session-id'
CURSOR=HOME/'.notification-cursor'
DEFERRED=HOME/'.deferred-reminders.json'

def http(path,body=None):
    headers={'X-Agent-Name':NAME}
    if body is None:req=urllib.request.Request(BASE+path,headers=headers)
    else:req=urllib.request.Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode(),headers={**headers,'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
async def api(path,body=None):return await asyncio.to_thread(http,path,body)

class Codex:
    def __init__(self):self.proc=None;self.pending={};self.seq=0;self.events=asyncio.Queue();self.reader_task=None;self.stderr_task=None;self.thread_id=None;self.turn_id=None
    async def start_server(self):
        self.proc=await asyncio.create_subprocess_exec('codex','app-server','--stdio',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=str(HOME/'work'),limit=16*1024*1024)
        self.reader_task=asyncio.create_task(self.read())
        self.stderr_task=asyncio.create_task(self.stderr())
        await self.call('initialize',{'clientInfo':{'name':'cybercompany-agent','version':'0.1'},'capabilities':{'experimentalApi':True}})
        self.proc.stdin.write(b'{"method":"initialized","params":{}}\n');await self.proc.stdin.drain()
    async def open(self):
        await self.start_server()
        if SESSION.exists():
            sid=SESSION.read_text().strip()
            try:
                print(f'[{NAME}] checking saved session {sid}',flush=True)
                state=await self.call('thread/read',{'threadId':sid},15)
                status=state['result']['thread'].get('status',{})
                print(f'[{NAME}] saved session status: {status}',flush=True)
                if status.get('type')=='active' and not status.get('activeFlags',[]):
                    fork=await self.call('thread/fork',{'threadId':sid,'cwd':str(HOME/'work'),'model':MODEL},30)
                    sid=fork['result']['thread']['id']
                    SESSION.write_text(sid)
                    print(f'[{NAME}] forked stale active session to preserve history: {sid}',flush=True)
                print(f'[{NAME}] resuming {sid}',flush=True)
                try:
                    response=await self.call('thread/resume',{'threadId':sid,'cwd':str(HOME/'work'),'model':MODEL,'sandbox':'danger-full-access','approvalPolicy':'never'},30)
                except asyncio.TimeoutError:
                    print(f'[{NAME}] resume timed out; forking saved history {sid}',flush=True)
                    await self.close()
                    self.__init__()
                    await self.start_server()
                    fork=await self.call('thread/fork',{'threadId':sid,'cwd':str(HOME/'work'),'model':MODEL},30)
                    sid=fork['result']['thread']['id']
                    SESSION.write_text(sid)
                    response=await self.call('thread/resume',{'threadId':sid,'cwd':str(HOME/'work'),'model':MODEL,'sandbox':'danger-full-access','approvalPolicy':'never'},30)
                self.thread_id=response['result']['thread']['id']
                print(f'[{NAME}] resumed {self.thread_id}',flush=True)
            except Exception as e:
                if 'no rollout found' in str(e):
                    print(f'[{NAME}] saved session missing; creating a replacement: {e}',flush=True)
                else:
                    raise RuntimeError(f'Could not resume saved session {sid}; preserving it: {e}') from e
        if not self.thread_id:
            response=await self.call('thread/start',{'cwd':str(HOME/'work'),'model':MODEL,'sandbox':'danger-full-access','approvalPolicy':'never','ephemeral':False},45)
            self.thread_id=response['result']['thread']['id'];SESSION.write_text(self.thread_id)
            print(f'[{NAME}] new session {self.thread_id}',flush=True)
    async def read(self):
        error='Codex exited'
        try:
            while True:
                line=await self.proc.stdout.readline()
                if not line:break
                try:msg=json.loads(line)
                except:continue
                if 'id' in msg and msg['id'] in self.pending:
                    fut=self.pending.pop(msg['id'])
                    if not fut.done():fut.set_result(msg)
                else:await self.events.put(msg)
        except Exception as e:
            error=f'Codex protocol reader failed: {e}'
            print(f'[{NAME}] {error}',flush=True)
        finally:
            for fut in self.pending.values():
                if not fut.done():fut.set_exception(RuntimeError(error))
    async def stderr(self):
        while True:
            line=await self.proc.stderr.readline()
            if not line:break
            print(f'[{NAME}] codex stderr: {line.decode(errors="replace").strip()[:300]}',flush=True)
    async def call(self,method,params,timeout=30):
        self.seq+=1;ident=self.seq;fut=asyncio.get_running_loop().create_future();self.pending[ident]=fut
        self.proc.stdin.write((json.dumps({'id':ident,'method':method,'params':params},ensure_ascii=False)+'\n').encode());await self.proc.stdin.drain()
        try:msg=await asyncio.wait_for(fut,timeout)
        finally:self.pending.pop(ident,None)
        if 'error' in msg:raise RuntimeError(f'{method}: {msg["error"]}')
        return msg
    async def start(self,prompt):
        x=await self.call('turn/start',{'threadId':self.thread_id,'input':[{'type':'text','text':prompt}]},30)
        self.turn_id=x['result']['turn']['id']
    async def steer(self,prompt):
        return await self.call('turn/steer',{'threadId':self.thread_id,'expectedTurnId':self.turn_id,'input':[{'type':'text','text':prompt}]},30)
    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:await asyncio.wait_for(self.proc.wait(),5)
            except asyncio.TimeoutError:self.proc.kill();await self.proc.wait()
        for t in (self.reader_task,self.stderr_task):
            if t:t.cancel()

def prompt_for(e):
    if e['kind']=='patrol':return '这是你的定时巡查。查看只读的 chats、members、kanban；有具体问题可联系同事或创建带提醒时间的任务。最终回复不会发到群里；需要发言必须调用 cybercompany MCP。'
    if e['kind']=='startup':return '容器刚刚启动或重启。检查自己的工作区、可见讨论和看板，继续此前中断且仍有价值的工作；没有待办就安静结束。收到老板要求长期记住工作原则时，使用 cybercompany MCP 的 add_startup_memory；只能用 remove_startup_memory 删除自己添加的记忆，不能删除入职设定第 0 段。'
    if e['kind']=='task':return f"看板 board-{e['resource_id']} 的 TASK-{e['source_id']} 指派或更新给你。读取 /home/agent/kanban/board-{e['resource_id']}/tasks.md 并按自己的职责工作。需要联系同事或更新任务时调用 cybercompany MCP。最终回复不会自动显示在网页。"
    if e['kind']=='reminder':return f"你设置的看板任务提醒已到：board-{e['resource_id']} 的 TASK-{e['source_id']}。请读取 /home/agent/kanban/board-{e['resource_id']}/tasks.md，检查任务是否到期或需要下一步，再采取行动；不需要时安静结束。"
    example=json.dumps({'thread_id':e['resource_id'],'body':'你的回复'},ensure_ascii=False)
    return f"thread-{e['resource_id']} 有新消息（消息 #{e['source_id'] or '加入讨论串'}）。请阅读 /home/agent/chats/thread-{e['resource_id']}/introduction.md 和 messages.md，再按职责判断是否需要行动。需要在网页发言时调用 cybercompany MCP 的 post_message；若直接 MCP 工具不可见，使用 `python3 /app/company.py post_message '{example}'`，它会调用同一个 MCP。最终回复不会自动发到群里。无需行动可直接结束。"

def waking_prompt(e):
    filename='patrol.md' if e['kind']=='patrol' else 'role.md'
    startup=(HOME/'private'/filename).read_text(encoding='utf-8')
    return f'本次从闲着状态醒来。以下是你的启动词：\n\n{startup}\n\n本次事件：\n{prompt_for(e)}'

ITEM_TITLES={'agentMessage':'Codex 回复','reasoning':'思路摘要','commandExecution':'执行命令','mcpToolCall':'调用 MCP 工具','fileChange':'修改文件','webSearch':'搜索网页','plan':'工作计划'}
def as_text(value):
    if isinstance(value,str):return value
    if value is None:return ''
    return json.dumps(value,ensure_ascii=False)
def activity_from(msg):
    method=msg.get('method','');p=msg.get('params') or {}
    turn=p.get('turn') or {}
    turn_id=p.get('turnId') or turn.get('id') or ''
    if method.startswith('turn/') and method in ('turn/started','turn/completed','turn/failed'):
        phase=method.split('/')[1]
        return {'turn_id':turn_id,'kind':'turn','phase':phase,'title':{'started':'开始处理','completed':'本轮完成','failed':'本轮失败'}[phase],'text':as_text(turn.get('error') or p.get('error'))}
    item=p.get('item') or {}
    if method in ('item/started','item/completed') and isinstance(item,dict):
        kind=item.get('type','item');phase=method.split('/')[1]
        title=ITEM_TITLES.get(kind,kind)
        if kind=='commandExecution':title=item.get('command') or title
        if kind=='mcpToolCall':title=f"{item.get('server') or item.get('serverName') or 'MCP'} · {item.get('tool') or item.get('toolName') or '工具'}"
        if kind=='agentMessage':content=item.get('text') or ''
        elif kind=='reasoning':content=as_text(item.get('summary') or '')
        elif kind=='commandExecution':content=item.get('aggregatedOutput') or ''
        elif kind=='mcpToolCall':content=as_text(item.get('result') or item.get('arguments') or '')
        else:content=as_text(item.get('text') or item.get('changes') or '')
        return {'turn_id':turn_id,'item_id':item.get('id',''),'kind':kind,'phase':phase,'title':title,'text':content}
    if method.startswith('item/') and (method.endswith('Delta') or method.endswith('/delta')):
        kind=method.split('/')[1]
        delta=p.get('delta')
        if delta is None:delta=p.get('textDelta')
        if delta is None:delta=p.get('outputDelta')
        if delta is None:return None
        return {'turn_id':turn_id,'item_id':p.get('itemId',''),'kind':kind,'phase':'delta','title':ITEM_TITLES.get(kind,kind),'text':as_text(delta)}
    return None

async def main():
    (HOME/'work').mkdir(parents=True,exist_ok=True);(HOME/'.codex').mkdir(exist_ok=True)
    auth=Path('/run/secrets/codex-auth.json')
    if auth.exists():shutil.copyfile(auth,HOME/'.codex'/'auth.json');os.chmod(HOME/'.codex'/'auth.json',0o600)
    config=f'''model = "{MODEL}"\nmodel_reasoning_effort = "{REASONING_EFFORT}"\nsandbox_mode = "danger-full-access"\napproval_policy = "never"\n\n[mcp_servers.cybercompany]\ncommand = "python3"\nargs = ["/app/mcp.py"]\nstartup_timeout_sec = 15\ntool_timeout_sec = 30\n'''
    config+=f'\n[mcp_servers.cybercompany.env]\nAGENT_NAME = "{NAME}"\nCOMPANY_URL = "{BASE}"\n'
    (HOME/'.codex'/'config.toml').write_text(config)
    cursor=int(CURSOR.read_text()) if CURSOR.exists() else 0
    deferred=json.loads(DEFERRED.read_text()) if DEFERRED.exists() else []
    await api(f'/api/agents/{NAME}/status',{'status':'idle'})
    await api(f'/api/agents/{NAME}/wake',{})
    agent=None;turn_active=False;just_woke=False;pending_activity=[];recovery_needed=False
    while True:
      try:
        if recovery_needed:
            # A server outage may have interrupted a turn after its notification
            # cursor was saved. Reconcile status and wake the saved session once.
            await api(f'/api/agents/{NAME}/status',{'status':'idle'})
            await api(f'/api/agents/{NAME}/wake',{})
            recovery_needed=False
        result=await api(f'/api/agents/{NAME}/events?after={cursor}')
        events=result['events']
        if events or deferred:
            if not agent:
                agent=Codex()
                await api(f'/api/agents/{NAME}/status',{'status':'busy'})
                pending_activity.append({'kind':'runtime','phase':'started','title':'正在启动 Codex','text':'恢复个人会话并读取新通知'})
                await agent.open()
                just_woke=True
            if deferred and not turn_active:
                e=deferred[0]
                await agent.start(waking_prompt(e) if just_woke else prompt_for(e))
                deferred.pop(0);DEFERRED.write_text(json.dumps(deferred,ensure_ascii=False))
                just_woke=False;turn_active=True
                print(f'[{NAME}] started deferred reminder {e["id"]}',flush=True)
                pending_activity.append({'kind':'notification','phase':'started','title':'任务提醒到期','text':f"TASK-{e['source_id']}"})
            for e in events:
                prompt=prompt_for(e)
                if turn_active and e['kind']=='reminder':
                    deferred.append(e);DEFERRED.write_text(json.dumps(deferred,ensure_ascii=False))
                    print(f'[{NAME}] deferred reminder {e["id"]} until current turn ends',flush=True)
                elif turn_active:
                    try:await agent.steer(prompt);print(f'[{NAME}] steered notification {e["id"]}',flush=True)
                    except Exception as ex:print(f'[{NAME}] steer failed, will retry: {ex}',flush=True);break
                else:
                    await agent.start(waking_prompt(e) if just_woke else prompt);just_woke=False;turn_active=True;print(f'[{NAME}] started notification {e["id"]}',flush=True)
                cursor=e['id'];CURSOR.write_text(str(cursor))
                pending_activity.append({'kind':'notification','phase':'received','title':{'message':'收到聊天消息','task':'收到看板任务','reminder':'任务提醒到期','patrol':'定时巡查','startup':'容器启动检查','joined':'加入讨论串'}.get(e['kind'],e['kind']),'text':f"通知 #{e['id']} · thread/board {e['resource_id'] or '-'} · 消息/任务 {e['source_id'] or '-'}"})
        if agent:
            while not agent.events.empty():
                msg=agent.events.get_nowait()
                activity=activity_from(msg)
                if activity:pending_activity.append(activity)
                method=msg.get('method','')
                if method=='turn/completed':
                    print(f'[{NAME}] turn completed',flush=True)
                    turn_active=False
                elif method=='turn/failed':
                    print(f'[{NAME}] turn failed: {str(msg.get("params"))[:500]}',flush=True)
                    turn_active=False
                elif method=='item/completed' and msg.get('params',{}).get('item',{}).get('type')=='agentMessage':
                    item=msg['params']['item']
                    if item.get('phase')=='final_answer':print(f'[{NAME}] final: {item.get("text","")[:500]}',flush=True)
        if agent and not turn_active:
            if deferred:
                e=deferred[0]
                await agent.start(prompt_for(e));turn_active=True
                deferred.pop(0);DEFERRED.write_text(json.dumps(deferred,ensure_ascii=False))
                print(f'[{NAME}] started deferred reminder {e["id"]}',flush=True)
                pending_activity.append({'kind':'notification','phase':'started','title':'任务提醒到期','text':f"TASK-{e['source_id']}"})
                continue
            # Do not close until the notification queue has been checked once more.
            pending=await api(f'/api/agents/{NAME}/events?after={cursor}')
            if not pending['events']:
                await asyncio.sleep(2)  # Let Codex flush its durable session before exit.
                pending=await api(f'/api/agents/{NAME}/events?after={cursor}')
                if not pending['events']:
                    await agent.close();agent=None
                    await api(f'/api/agents/{NAME}/status',{'status':'idle'})
                    pending_activity.append({'kind':'runtime','phase':'completed','title':'员工已闲着','text':'Codex 进程已退出'})
                    print(f'[{NAME}] idle',flush=True)
        for _ in range(5):
            if not pending_activity:break
            batch=pending_activity[:200]
            try:await api(f'/api/agents/{NAME}/activity',{'events':batch})
            except Exception as ex:print(f'[{NAME}] activity upload will retry: {ex}',flush=True);break
            del pending_activity[:len(batch)]
        await asyncio.sleep(1)
      except Exception as e:
        print(f'[{NAME}] runner error: {e}',flush=True)
        if agent:
            await agent.close();agent=None;turn_active=False
            recovery_needed=True
            pending_activity.append({'kind':'runtime','phase':'failed','title':'运行器遇到错误','text':str(e)[:1000]})
            try:await api(f'/api/agents/{NAME}/status',{'status':'idle'})
            except:pass
        await asyncio.sleep(5)

asyncio.run(main())
