"""Small stdio MCP bridge. Each instance acts only as its own AGENT_NAME."""
import json, os, sys, urllib.request, urllib.error
BASE=os.environ.get('COMPANY_URL','http://server:8000')
NAME=os.environ['AGENT_NAME']
TOOLS=[
 ('create_thread','创建讨论串并邀请成员',{'title':{'type':'string'},'members':{'type':'array','items':{'type':'string'}},'kind':{'type':'string'}},['title','members']),
 ('post_message','在自己参与的讨论串发消息',{'thread_id':{'type':'integer'},'body':{'type':'string'}},['thread_id','body']),
 ('add_member','邀请 Agent 加入讨论串',{'thread_id':{'type':'integer'},'name':{'type':'string'}},['thread_id','name']),
 ('set_my_thread_mode','将自己设为参与者或旁听者',{'thread_id':{'type':'integer'},'mode':{'type':'string','enum':['participant','observer']}},['thread_id','mode']),
 ('create_board','创建共享看板',{'title':{'type':'string'}},['title']),
 ('create_task','创建并可指派看板任务；remind_at 为 ISO 时间，默认 30 分钟后',{'board_id':{'type':'integer'},'title':{'type':'string'},'description':{'type':'string'},'assignee':{'type':'string'},'remind_at':{'type':'string'}},['board_id','title']),
 ('update_task','更新任务进度、分支、审核结果或提醒时间',{'task_id':{'type':'integer'},'status':{'type':'string'},'assignee':{'type':'string'},'branch_url':{'type':'string'},'reviewer':{'type':'string'},'review_result':{'type':'string'},'description':{'type':'string'},'remind_at':{'type':'string'}},['task_id']),
 ('add_startup_memory','给自己的日常启动词添加一段持久记忆；入职设定第0段不可改',{'body':{'type':'string'}},['body']),
 ('remove_startup_memory','按编号删除自己添加的启动记忆；不能删除第0段',{'memory_id':{'type':'integer'}},['memory_id']),
]
def request(path,body):
    data=json.dumps(body,ensure_ascii=False).encode()
    req=urllib.request.Request(BASE+path,data=data,headers={'Content-Type':'application/json','X-Agent-Name':NAME},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        try:msg=json.load(e).get('error',str(e))
        except:msg=str(e)
        raise ValueError(msg)
def call(name,a):
    if name=='create_thread':return request('/api/threads',{'title':a['title'],'members':a['members'],'kind':a.get('kind','group')})
    if name=='post_message':return request(f"/api/threads/{a['thread_id']}/messages",{'body':a['body']})
    if name=='add_member':return request(f"/api/threads/{a['thread_id']}/members",{'name':a['name']})
    if name=='set_my_thread_mode':return request(f"/api/threads/{a['thread_id']}/mode/{NAME}",{'mode':a['mode']})
    if name=='create_board':return request('/api/boards',{'title':a['title']})
    if name=='create_task':return request('/api/tasks',{k:v for k,v in a.items() if k!='task_id'})
    if name=='update_task':return request(f"/api/tasks/{a['task_id']}",{k:v for k,v in a.items() if k!='task_id'})
    if name=='add_startup_memory':return request(f'/api/agents/{NAME}/memories',{'body':a['body']})
    if name=='remove_startup_memory':return request(f"/api/agents/{NAME}/memories/{a['memory_id']}",{})
    raise ValueError('未知工具')
def emit(x):sys.stdout.write(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n');sys.stdout.flush()
for line in sys.stdin:
    try:
        msg=json.loads(line);method=msg.get('method');id=msg.get('id')
        if method=='initialize':result={'protocolVersion':msg.get('params',{}).get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'cybercompany','version':'0.1'}}
        elif method=='tools/list':result={'tools':[{'name':n,'description':desc,'inputSchema':{'type':'object','properties':props,'required':required}} for n,desc,props,required in TOOLS]}
        elif method=='resources/list':result={'resources':[]}
        elif method=='resources/templates/list':result={'resourceTemplates':[]}
        elif method=='prompts/list':result={'prompts':[]}
        elif method=='tools/call':
            try:out=call(msg['params']['name'],msg['params'].get('arguments',{}));result={'content':[{'type':'text','text':json.dumps(out,ensure_ascii=False)}]}
            except Exception as e:result={'content':[{'type':'text','text':str(e)}],'isError':True}
        elif method and method.startswith('notifications/'):continue
        else:result={}
        if id is not None:emit({'jsonrpc':'2.0','id':id,'result':result})
    except Exception as e:
        if 'id' in locals() and id is not None:emit({'jsonrpc':'2.0','id':id,'error':{'code':-32603,'message':str(e)}})
