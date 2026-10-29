"""Executed in a temporary folder over SSH. No persistent installation."""
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import re
import subprocess
import hashlib
import sync_core as core
import discovery
import coverage


def select_codex(home, db):
    candidates=set()
    active=set()
    try: active={r['path'] for r in holders(db) if Path(r['path']).name.lower() in ('codex','codex.exe')} if db else set()
    except RuntimeError: pass
    candidates.update(active)
    path=shutil.which('codex')
    if path: candidates.add(path)
    for directory in (Path.home()/'.vscode-server/extensions',Path.home()/'.vscode-server-insiders/extensions'):
        candidates.update(str(p) for p in directory.glob('openai.chatgpt-*/bin/linux-*/codex'))
    for directory in (Path(home)/'bin',Path.home()/'.local/bin'):
        p=directory/'codex'
        if p.is_file(): candidates.add(str(p))
    choices=[]
    for path in sorted(candidates):
        try:
            output=subprocess.run([path,'--version'],capture_output=True,text=True,timeout=5,check=True).stdout.strip()
            match=re.search(r'(\d+)\.(\d+)\.(\d+)',output)
            if match:
                choices.append({'path':path,'version':output,'version_tuple':tuple(map(int,match.groups())),
                    'active_database_client':path in active})
        except (OSError,subprocess.SubprocessError): continue
    if not choices: raise RuntimeError('远端未找到可执行的 Codex CLI。')
    selected=max(choices,key=lambda r:(r['active_database_client'],r['version_tuple']))
    return selected,choices


from remote_processes import holders


def plan_hash(db,model,provider):
    c=core.connection(db)
    try: rows=core.rows(c)
    finally: c.close()
    values=sorted((r['id'],r['model'],r['model_provider']) for r in rows if r['model']!='codex-auto-review')
    return hashlib.sha256(json.dumps([str(Path(db).resolve()),model,provider,values],ensure_ascii=True).encode()).hexdigest()


def repair(db,model,provider,request):
    from remote_processes import same_process,stop_processes
    if not request.get('plan_hash') or request['plan_hash']!=plan_hash(db,model,provider):
        raise RuntimeError('远端记录自预览后已变化，请重新点击一键修复获取新预览。未结束进程。')
    changes=core.preview(db,model,provider)
    if not changes: return {'count':0,'backup':None,'stop_backups':[],'verified_remaining':0,'sent':[]}
    approved={r['pid']:r for r in request.get('targets',[])}
    active=holders(db)
    for item in active:
        if not item.get('terminable') or item['pid'] not in approved or not same_process(item,approved[item['pid']]):
            raise RuntimeError('出现未在预览中列出的占用进程，请重新预览；未结束进程。')
    backups=[]; sent=[]
    try:
        if active:
            stopped=stop_processes(db,active,force=False)
            backups.append(stopped['backup']); sent.extend(stopped['sent'])
            remaining=stopped['holders']
            if remaining and request.get('force'):
                if any(r['pid'] not in approved or not same_process(r,approved[r['pid']]) for r in remaining):
                    raise RuntimeError('后端被自动重启，出现新的 PID。请先断开此主机的 Codex/VS Code 远程连接，再重新操作。')
                stopped=stop_processes(db,remaining,force=True)
                backups.append(stopped['backup']); sent.extend(stopped['sent'])
                remaining=stopped['holders']
            if remaining: raise RuntimeError('退出信号后仍有占用：'+json.dumps(remaining,ensure_ascii=False))
        # Stop and synchronize within the same SSH helper; no second preview/network round trip.
        if request['plan_hash']!=plan_hash(db,model,provider): raise RuntimeError('退出过程中记录发生变化，请重新预览。')
        def guard():
            remaining=holders(db)
            if remaining: raise RuntimeError('新进程重新占用数据库，请断开此主机的 Codex/VS Code 连接：'+json.dumps(remaining,ensure_ascii=False))
            return False
        count,backup=core.sync(db,model,provider,process_check=guard)
        verified=len(core.preview(db,model,provider))
        return {'count':count,'backup':str(backup) if backup else None,'stop_backups':backups,'sent':sent,'verified_remaining':verified}
    except Exception as e:
        raise RuntimeError('一键修复未完成：'+str(e)+'；已生成的远端备份：'+json.dumps(backups,ensure_ascii=False)) from e


def execute(request):
    if request['action'].startswith(('connection_','device_')):
        import connection_remote
        return connection_remote.execute(request)
    home=Path(request.get('home') or os.environ.get('CODEX_HOME') or str(Path.home()/'.codex')).expanduser().resolve()
    db=Path(request['db']).expanduser().resolve() if request.get('db') else core.database(home)
    action=request['action']
    model=request.get('model',''); provider=request.get('provider','')
    result={'home':str(home),'db':str(db),'host':os.uname().nodename,'warnings':[]}
    if action=='discover':
        selected,choices=select_codex(home,db)
        result.update(discovery.discover(home,executable=selected['path']))
        result['client']=selected
        result['clients']=choices
    elif action=='preview':
        result.update(rows=core.preview(db,model,provider),audit=coverage.audit(home,db,model,provider))
    elif action=='plan':
        before=plan_hash(db,model,provider)
        result.update(rows=core.preview(db,model,provider),audit=coverage.audit(home,db,model,provider),
            holders=holders(db,result['warnings']),plan_hash=plan_hash(db,model,provider))
        if before!=result['plan_hash']: raise RuntimeError('准备预览期间记录发生变化，请重试。未修改。')
    elif action=='repair': result.update(repair(db,model,provider,request))
    elif action=='occupancy': result['holders']=holders(db,result['warnings'])
    elif action=='apply':
        event=threading.Event()
        for sig in (signal.SIGHUP,signal.SIGTERM): signal.signal(sig,lambda *_:event.set())
        def guard():
            items=holders(db,result['warnings'])
            if items: raise RuntimeError('远端数据库仍被占用：'+json.dumps(items,ensure_ascii=False))
            return False
        count,backup=core.sync(db,model,provider,process_check=guard,cancel_event=event)
        result.update(count=count,backup=str(backup) if backup else None)
    else: raise ValueError('未知远端操作')
    return result
