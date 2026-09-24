import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path('/data')
DB = ROOT / 'company.sqlite3'
VIEW_LOCK = threading.RLock()
NAMES = ['manager', 'dev1', 'dev2', 'marketing', 'pm', 'legal', 'security', 'reviewer']
TITLES = {'manager':'经理','dev1':'开发一','dev2':'开发二','marketing':'市场','pm':'产品经理','legal':'法务','security':'安全','reviewer':'代码审核'}
DESCRIPTIONS = {
 'manager':'组织团队、拆分任务、安排交接，重大决策向老板汇报。',
 'dev1':'开发产品功能，提交可验证的代码和分支。',
 'dev2':'开发产品功能，协助处理并行开发任务。',
 'marketing':'调查市场、用户和竞品，引用可核验来源。',
 'pm':'定义需求、验收标准和优先级。',
 'legal':'巡查合同、授权、隐私和合规风险，指出证据与适用范围。',
 'security':'巡查安全风险、权限和依赖，提出可执行修复建议。',
 'reviewer':'审查开发分支、质量和测试证据，给出明确结论。',
}
RULES = {
 'manager':'收到老板的任务后判断负责人，必要时创建讨论串和看板任务，给相关同事清晰派工。不要为了保持活跃而闲聊。',
 'dev1':'完成代码后必须提供 Git 分支或提交链接，并交给 reviewer 审核。审核通过前不要声称任务完成。',
 'dev2':'完成代码后必须提供 Git 分支或提交链接，并交给 reviewer 审核。审核通过前不要声称任务完成。',
 'marketing':'调查必须区分事实与推测；引用亲自查看过的来源。发现产品机会时通知 manager 或 pm。',
 'pm':'把想法变成有验收标准的任务，和开发、市场沟通范围。',
 'legal':'指出具体风险和依据；无证据时不要宣称违规。需要别人处理时创建任务或发消息。',
 'security':'检查具体证据和影响范围；高风险及时通知 manager 并创建任务。',
 'reviewer':'审查前要求开发提供可访问的分支或提交。通过后通知开发去找老板或 pm 验收。',
}

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def in_minutes(minutes): return (datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat(timespec='seconds')
def connect():
    c=sqlite3.connect(DB, timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    ROOT.mkdir(parents=True,exist_ok=True)
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS members(name TEXT PRIMARY KEY, public_md TEXT NOT NULL, role_md TEXT NOT NULL, patrol_md TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle', idle_since TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS threads(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'group',created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS thread_members(thread_id INTEGER NOT NULL,name TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'participant',PRIMARY KEY(thread_id,name));
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,thread_id INTEGER NOT NULL,sender TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS boards(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,board_id INTEGER NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',assignee TEXT,status TEXT NOT NULL DEFAULT 'todo',branch_url TEXT,reviewer TEXT,review_result TEXT,remind_at TEXT,reminder_sent_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT NOT NULL,kind TEXT NOT NULL,resource_id INTEGER,source_id INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS startup_memories(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_activity(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT NOT NULL,turn_id TEXT,item_id TEXT,kind TEXT NOT NULL,phase TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_agent_activity_agent_id ON agent_activity(agent,id);
        ''')
        columns={x['name'] for x in c.execute('PRAGMA table_info(tasks)')}
        if 'remind_at' not in columns:c.execute('ALTER TABLE tasks ADD COLUMN remind_at TEXT')
        if 'reminder_sent_at' not in columns:c.execute('ALTER TABLE tasks ADD COLUMN reminder_sent_at TEXT')
        c.execute("UPDATE tasks SET remind_at=datetime(created_at,'+30 minutes') WHERE remind_at IS NULL")
        for name in NAMES:
            public=f'# {name} · {TITLES[name]}\n\n{DESCRIPTIONS[name]}\n\n需要{TITLES[name]}协助时，在讨论串中 @{name} 或给它分派任务。\n'
            role=f'''# {TITLES[name]}的工作原则\n\n你是赛博公司的 {name}。你的私有工作区是 /home/agent/work。根目录的 chats、members、kanban 是只读视图。通过 cybercompany MCP 创建讨论串、发消息、修改看板、调整旁听状态；不要直接修改只读目录。若 Codex 没有列出 MCP 工具，使用命令 `python3 /app/company.py post_message '{{"thread_id":1,"body":"你好"}}'` 调用同一个 MCP 服务；其他工具名与参数参见 /app/mcp.py。\n\n{RULES[name]}\n\n有新消息时，先阅读通知指向的讨论串或任务，再判断是否需要行动。无需行动就安静结束。只汇报实际做过且可核验的工作。不要因同事回复而无意义地重复发言。需要创建任务或交接时使用 MCP。\n'''
            patrol=f'''# {TITLES[name]}的定时巡查\n\n你已闲置约 8 小时。查看自己可见的近期讨论和看板，按 {TITLES[name]} 的职责寻找遗漏或下一步工作。可以主动联系同事讨论具体问题，也可以给自己创建带提醒时间的任务。发现风险要说明证据；没有值得跟进的事就直接结束。\n'''
            c.execute('INSERT OR IGNORE INTO members(name,public_md,role_md,patrol_md,status,idle_since) VALUES(?,?,?,?,?,?)',(name,public,role,patrol,'idle',now()))
        c.execute('INSERT OR IGNORE INTO members(name,public_md,role_md,patrol_md,status,idle_since) VALUES(?,?,?,?,?,?)',('human','# human · 老板\n\n唯一的人类用户，可以发消息和管理团队。\n','','','idle',now()))
        if not c.execute("SELECT 1 FROM sqlite_sequence WHERE name='threads'").fetchone():
            cur=c.execute('INSERT INTO threads(title,kind,created_at) VALUES(?,?,?)',('公司大厅','group',now()))
            tid=cur.lastrowid
            for name in ['human']+NAMES:c.execute('INSERT INTO thread_members(thread_id,name,mode) VALUES(?,?,?)',(tid,name,'participant'))
        if not c.execute('SELECT 1 FROM boards').fetchone():c.execute('INSERT INTO boards(title,created_at) VALUES(?,?)',('公司任务',now()))
    sync_all()

def atomic_write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(value,encoding='utf-8')
    os.replace(tmp,path)

def sync_all():
  with VIEW_LOCK:
    sync_all_impl()

def sync_all_impl():
    with connect() as c:
      members=c.execute('SELECT * FROM members').fetchall()
      boards=c.execute('SELECT * FROM boards ORDER BY id').fetchall()
      for agent in NAMES:
        home=ROOT/'agents'/agent
        (home/'work').mkdir(parents=True,exist_ok=True)
        (home/'private').mkdir(parents=True,exist_ok=True)
        own=next(x for x in members if x['name']==agent)
        memories=c.execute('SELECT id,body FROM startup_memories WHERE agent=? ORDER BY id',(agent,)).fetchall()
        startup='# 启动词\n\n## 第 0 段 · 入职设定（仅老板可修改）\n\n'+own['role_md']+'\n'+''.join(f'\n## 可调整记忆 #{memory["id"]}\n\n{memory["body"]}\n' for memory in memories)
        atomic_write(home/'private'/'role.md',startup)
        atomic_write(home/'private'/'patrol.md',own['patrol_md'])
        atomic_write(home/'work'/'AGENTS.md',startup)
        view=ROOT/'views'/agent
        for m in members:
            status=f"\n状态：{'忙着' if m['status']=='busy' else '闲着'}\n" if m['name']!='human' else ''
            atomic_write(view/'members'/f"{m['name']}.md",m['public_md']+status)
        threads=c.execute('SELECT t.*,tm.mode FROM threads t JOIN thread_members tm ON tm.thread_id=t.id WHERE tm.name=? ORDER BY t.id',(agent,)).fetchall()
        allowed={f'thread-{t["id"]}' for t in threads}
        chats=view/'chats';chats.mkdir(parents=True,exist_ok=True)
        for old in chats.iterdir():
            if old.is_dir() and old.name not in allowed:
                for f in old.iterdir(): f.unlink()
                old.rmdir()
        for t in threads:
            tid=t['id'];d=chats/f'thread-{tid}'
            peers=c.execute('SELECT tm.name,tm.mode FROM thread_members tm WHERE tm.thread_id=? ORDER BY tm.name',(tid,)).fetchall()
            intro=f"# {t['title']}\n\nThread ID: {tid}\n类型：{t['kind']}\n你的模式：{t['mode']}\n\n## 成员\n"+''.join(f"- {p['name']}：{p['mode']}\n" for p in peers)
            atomic_write(d/'introduction.md',intro)
            msgs=c.execute('SELECT * FROM messages WHERE thread_id=? ORDER BY id',(tid,)).fetchall()
            history=f"# {t['title']} 消息历史\n\n"+''.join(f"## #{m['id']} · {m['sender']} · {m['created_at']}\n\n{m['body']}\n\n" for m in msgs)
            atomic_write(d/'messages.md',history)
        kan=view/'kanban';kan.mkdir(parents=True,exist_ok=True)
        for b in boards:
            d=kan/f"board-{b['id']}"
            atomic_write(d/'introduction.md',f"# {b['title']}\n\nBoard ID: {b['id']}\n")
            tasks=c.execute('SELECT * FROM tasks WHERE board_id=? ORDER BY id',(b['id'],)).fetchall()
            atomic_write(d/'tasks.md','# 任务\n\n'+''.join(f"## TASK-{x['id']} · {x['title']}\n\n状态：{x['status']}  负责人：{x['assignee'] or '未分派'}  提醒时间：{x['remind_at'] or '无'}  分支：{x['branch_url'] or '无'}  审核：{x['review_result'] or '未审核'}\n\n{x['description']}\n\n" for x in tasks))

def notify(c,agent,kind,resource_id,source_id):
    if agent in NAMES:c.execute('INSERT INTO notifications(agent,kind,resource_id,source_id,created_at) VALUES(?,?,?,?,?)',(agent,kind,resource_id,source_id,now()))

def mentions(body): return set(re.findall(r'(?<!\w)@([A-Za-z][A-Za-z0-9_]*)',body))

def create_thread(c,title,members,kind='group',actor='human'):
    names=list(dict.fromkeys([actor]+[x for x in members if x in NAMES or x=='human']))
    cur=c.execute('INSERT INTO threads(title,kind,created_at) VALUES(?,?,?)',(title,kind,now()))
    for n in names:c.execute('INSERT INTO thread_members(thread_id,name,mode) VALUES(?,?,?)',(cur.lastrowid,n,'participant'))
    return cur.lastrowid

def post_message(c,tid,sender,body):
    if not c.execute('SELECT 1 FROM thread_members WHERE thread_id=? AND name=?',(tid,sender)).fetchone():raise ValueError('发送者不是讨论串成员')
    thread=c.execute('SELECT kind FROM threads WHERE id=?',(tid,)).fetchone()
    cur=c.execute('INSERT INTO messages(thread_id,sender,body,created_at) VALUES(?,?,?,?)',(tid,sender,body,now()))
    peers=c.execute('SELECT name,mode FROM thread_members WHERE thread_id=?',(tid,)).fetchall()
    tagged=mentions(body)
    for p in peers:
        if p['name']!=sender and (thread['kind']=='dm' or p['mode']=='participant' or p['name'] in tagged):notify(c,p['name'],'message',tid,cur.lastrowid)
    return cur.lastrowid

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
    def body(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>1_000_000:raise ValueError('请求太大')
        return json.loads(self.rfile.read(n) or b'{}')
    def actor(self):return self.headers.get('X-Agent-Name','human')
    def do_GET(self):
      try:
        u=urlparse(self.path);parts=[p for p in u.path.split('/') if p]
        if u.path=='/' or u.path.startswith('/static/'):
            f=Path('/web/index.html') if u.path=='/' else Path('/web')/Path(u.path).name
            if not f.is_file():self.send_error(404);return
            content=f.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if f.suffix=='.html' else 'text/css' if f.suffix=='.css' else 'text/javascript; charset=utf-8');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
        with connect() as c:
            if len(parts)==3 and parts[:2]==['api','members']:
                if self.actor()!='human':raise ValueError('只有老板可查看岗位私有设定')
                member=c.execute('SELECT name,public_md,role_md,patrol_md FROM members WHERE name=?',(parts[2],)).fetchone()
                if not member:raise ValueError('成员不存在')
                self.send({**dict(member),'memories':[dict(x) for x in c.execute('SELECT id,body,created_at FROM startup_memories WHERE agent=? ORDER BY id',(parts[2],))]});return
            if parts==['api','bootstrap']:
                members=[{'name':x['name'],'public_md':x['public_md'],'status':x['status']} for x in c.execute('SELECT * FROM members ORDER BY name')]
                threads=[dict(x) for x in c.execute('SELECT * FROM threads ORDER BY id')]
                boards=[dict(x) for x in c.execute('SELECT * FROM boards ORDER BY id')]
                self.send({'members':members,'threads':threads,'boards':boards});return
            if len(parts)==4 and parts[:2]==['api','threads'] and parts[3]=='messages':
                tid=int(parts[2]);self.send({'thread':dict(c.execute('SELECT * FROM threads WHERE id=?',(tid,)).fetchone()),'members':[dict(x) for x in c.execute('SELECT * FROM thread_members WHERE thread_id=? ORDER BY name',(tid,))],'messages':[dict(x) for x in c.execute('SELECT * FROM messages WHERE thread_id=? ORDER BY id',(tid,))]});return
            if len(parts)==4 and parts[:2]==['api','agents'] and parts[3]=='events':
                name=parts[2];after=int(parse_qs(u.query).get('after',['0'])[0]);self.send({'events':[dict(x) for x in c.execute('SELECT * FROM notifications WHERE agent=? AND id>? ORDER BY id LIMIT 100',(name,after))]});return
            if len(parts)==4 and parts[:2]==['api','agents'] and parts[3]=='activity':
                if self.actor()!='human':raise ValueError('只有老板可查看员工工作流')
                name=parts[2]
                if name not in NAMES:raise ValueError('未知 Agent')
                query=parse_qs(u.query);after=int(query.get('after',['0'])[0])
                if after:
                    rows=c.execute('SELECT * FROM agent_activity WHERE agent=? AND id>? ORDER BY id LIMIT 300',(name,after)).fetchall()
                else:
                    limit=min(max(int(query.get('limit',['200'])[0]),1),500)
                    rows=list(reversed(c.execute('SELECT * FROM agent_activity WHERE agent=? ORDER BY id DESC LIMIT ?',(name,limit)).fetchall()))
                self.send({'events':[dict(x) for x in rows]});return
            if parts==['api','tasks']:
                self.send({'tasks':[dict(x) for x in c.execute('SELECT * FROM tasks ORDER BY id DESC')]});return
            if parts==['health']:
                self.send({'ok':True});return
        self.send_error(404)
      except Exception as e:self.send({'error':str(e)},400)
    def do_POST(self):
      try:
        p=[x for x in urlparse(self.path).path.split('/') if x];d=self.body();actor=self.actor()
        if actor not in ['human']+NAMES:raise ValueError('未知成员')
        with connect() as c:
            if len(p)==4 and p[:2]==['api','agents'] and p[3]=='activity':
                name=p[2]
                if actor!=name or name not in NAMES:raise ValueError('身份不匹配')
                events=d.get('events',[])
                if not isinstance(events,list) or len(events)>300:raise ValueError('工作流事件批次无效')
                for event in events:
                    if not isinstance(event,dict):raise ValueError('工作流事件无效')
                    fields={key:str(event.get(key,'') or '') for key in ('turn_id','item_id','kind','phase','title','text')}
                    c.execute('INSERT INTO agent_activity(agent,turn_id,item_id,kind,phase,title,text,created_at) VALUES(?,?,?,?,?,?,?,?)',(name,fields['turn_id'][:100],fields['item_id'][:100],fields['kind'][:40],fields['phase'][:40],fields['title'][:1000],fields['text'][:32000],now()))
                out={'ok':True}
            elif len(p)==4 and p[:2]==['api','agents'] and p[3]=='wake':
                name=p[2]
                if actor!=name or name not in NAMES:raise ValueError('身份不匹配')
                notify(c,name,'startup',None,None);out={'ok':True}
            elif len(p)==4 and p[:2]==['api','agents'] and p[3]=='memories':
                name=p[2]
                if actor!=name or name not in NAMES:raise ValueError('只能管理自己的启动记忆')
                body=str(d.get('body','')).strip()
                if not body or len(body)>10000:raise ValueError('记忆内容不能为空且不得超过 10000 字')
                cur=c.execute('INSERT INTO startup_memories(agent,body,created_at) VALUES(?,?,?)',(name,body,now()))
                out={'id':cur.lastrowid}
            elif len(p)==5 and p[:2]==['api','agents'] and p[3]=='memories':
                name=p[2]
                if actor!=name or name not in NAMES:raise ValueError('只能管理自己的启动记忆')
                cur=c.execute('DELETE FROM startup_memories WHERE id=? AND agent=?',(int(p[4]),name))
                if cur.rowcount!=1:raise ValueError('记忆不存在或不可删除')
                out={'ok':True}
            elif len(p)==3 and p[:2]==['api','members']:
                if actor!='human' or p[2] not in NAMES:raise ValueError('只有老板可编辑 Agent 设定')
                fields={k:str(d[k]) for k in ('public_md','role_md','patrol_md') if k in d}
                if not fields:raise ValueError('未提供设定')
                for key,value in fields.items():c.execute(f'UPDATE members SET {key}=? WHERE name=?',(value,p[2]))
                out={'ok':True}
            elif p==['api','threads']:
                title=str(d.get('title','')).strip();members=d.get('members',[])
                if not title:raise ValueError('需要群名')
                tid=create_thread(c,title,members,d.get('kind','group'),actor);out={'id':tid}
            elif len(p)==4 and p[:2]==['api','threads'] and p[3]=='rename':
                if actor!='human':raise ValueError('只有人类用户可重命名群聊')
                tid=int(p[2]);title=str(d.get('title','')).strip()
                if not title or len(title)>120:raise ValueError('群名不能为空且不得超过 120 字')
                thread=c.execute('SELECT kind FROM threads WHERE id=?',(tid,)).fetchone()
                if not thread:raise ValueError('群聊不存在')
                if thread['kind']=='dm':raise ValueError('私聊不可重命名')
                c.execute('UPDATE threads SET title=? WHERE id=?',(title,tid));out={'ok':True}
            elif len(p)==4 and p[:2]==['api','threads'] and p[3]=='delete':
                if actor!='human':raise ValueError('只有人类用户可删除群聊')
                tid=int(p[2]);thread=c.execute('SELECT kind FROM threads WHERE id=?',(tid,)).fetchone()
                if not thread:raise ValueError('群聊不存在')
                if thread['kind']=='dm':raise ValueError('私聊不可删除')
                c.execute("DELETE FROM notifications WHERE resource_id=? AND kind IN ('message','joined')",(tid,))
                c.execute('DELETE FROM messages WHERE thread_id=?',(tid,))
                c.execute('DELETE FROM thread_members WHERE thread_id=?',(tid,))
                c.execute('DELETE FROM threads WHERE id=?',(tid,));out={'ok':True}
            elif len(p)==4 and p[:2]==['api','threads'] and p[3]=='messages':
                body=str(d.get('body','')).strip();tid=int(p[2])
                if not body:raise ValueError('消息不能为空')
                out={'id':post_message(c,tid,actor,body)}
            elif len(p)==4 and p[:2]==['api','threads'] and p[3]=='members':
                tid=int(p[2]);name=d.get('name');
                if name not in NAMES:raise ValueError('未知 Agent')
                if actor!='human' and not c.execute('SELECT 1 FROM thread_members WHERE thread_id=? AND name=?',(tid,actor)).fetchone():raise ValueError('邀请者不是讨论串成员')
                c.execute('INSERT OR IGNORE INTO thread_members(thread_id,name,mode) VALUES(?,?,?)',(tid,name,'participant'));notify(c,name,'joined',tid,None);out={'ok':True}
            elif len(p)==5 and p[:2]==['api','threads'] and p[3]=='mode':
                tid=int(p[2]);name=p[4];mode=d.get('mode')
                if actor!='human' and actor!=name:raise ValueError('只能修改自己的模式')
                if mode not in ('participant','observer'):raise ValueError('模式无效')
                if mode=='observer':
                    count=c.execute("SELECT COUNT(*) FROM thread_members WHERE thread_id=? AND mode='participant' AND name!='human' AND name!=?",(tid,name)).fetchone()[0]
                    if count<1:raise ValueError('至少保留一位参与者')
                c.execute('UPDATE thread_members SET mode=? WHERE thread_id=? AND name=?',(mode,tid,name));out={'ok':True}
            elif p==['api','boards']:
                title=str(d.get('title','')).strip()
                if not title:raise ValueError('需要看板名')
                cur=c.execute('INSERT INTO boards(title,created_at) VALUES(?,?)',(title,now()));out={'id':cur.lastrowid}
            elif p==['api','tasks']:
                title=str(d.get('title','')).strip();assignee=d.get('assignee');board=int(d.get('board_id',1))
                if not title or (assignee and assignee not in NAMES):raise ValueError('任务标题或负责人无效')
                remind_at=str(d.get('remind_at') or in_minutes(30))
                datetime.fromisoformat(remind_at.replace('Z','+00:00'))
                cur=c.execute('INSERT INTO tasks(board_id,title,description,assignee,remind_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(board,title,str(d.get('description','')),assignee,remind_at,now(),now()))
                if assignee:notify(c,assignee,'task',board,cur.lastrowid)
                out={'id':cur.lastrowid}
            elif len(p)==3 and p[:2]==['api','tasks']:
                tid=int(p[2]);old=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
                if not old:raise ValueError('任务不存在')
                fields={k:d[k] for k in ('status','assignee','branch_url','reviewer','review_result','description','remind_at') if k in d}
                if 'remind_at' in fields and fields['remind_at']:
                    datetime.fromisoformat(str(fields['remind_at']).replace('Z','+00:00'))
                if 'assignee' in fields and fields['assignee'] not in NAMES:raise ValueError('负责人无效')
                if fields.get('status')=='review' and not (fields.get('branch_url') or old['branch_url']):raise ValueError('提交审核前需要分支链接')
                if fields.get('status')=='done' and not (fields.get('review_result') or old['review_result']):raise ValueError('完成前需要审核结论')
                for k,v in fields.items():c.execute(f'UPDATE tasks SET {k}=?,updated_at=? WHERE id=?',(v,now(),tid))
                if 'remind_at' in fields:c.execute('UPDATE tasks SET reminder_sent_at=NULL WHERE id=?',(tid,))
                new_assignee=fields.get('assignee')
                if new_assignee and new_assignee!=old['assignee']:notify(c,new_assignee,'task',old['board_id'],tid)
                out={'ok':True}
            elif len(p)==4 and p[:2]==['api','agents'] and p[3]=='status':
                name=p[2]
                if actor!=name:raise ValueError('身份不匹配')
                status=d.get('status')
                if status not in ('busy','idle'):raise ValueError('状态无效')
                c.execute('UPDATE members SET status=?,idle_since=? WHERE name=?',(status,now(),name));out={'ok':True}
            else:self.send_error(404);return
        if not (len(p)==4 and p[:2]==['api','agents'] and p[3]=='activity'):sync_all()
        self.send(out)
      except Exception as e:self.send({'error':str(e)},400)

def patrol_loop():
    while True:
        time.sleep(5)
        with connect() as c:
            for task in c.execute("SELECT id,board_id,assignee FROM tasks WHERE assignee IS NOT NULL AND remind_at IS NOT NULL AND reminder_sent_at IS NULL AND status!='done' AND julianday(remind_at)<=julianday('now')").fetchall():
                notify(c,task['assignee'],'reminder',task['board_id'],task['id'])
                c.execute('UPDATE tasks SET reminder_sent_at=? WHERE id=?',(now(),task['id']))
            for x in c.execute("SELECT name,idle_since FROM members WHERE status='idle' AND name!='human'"):
                if x['idle_since'] and datetime.fromisoformat(x['idle_since']).timestamp() < time.time()-8*3600:
                    pending=c.execute("SELECT 1 FROM notifications WHERE agent=? AND kind='patrol' AND created_at>?",(x['name'],x['idle_since'])).fetchone()
                    if not pending:
                        notify(c,x['name'],'patrol',None,None)
                        c.execute('UPDATE members SET idle_since=? WHERE name=?',(now(),x['name']))

init()
threading.Thread(target=patrol_loop,daemon=True).start()
ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever()
