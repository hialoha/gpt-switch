"""Remote connection lifecycle, previews contain metadata only."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
import uuid
import connection_sync as sync
import sync_core as core
import discovery
import remote_processes as processes


def optional_db(home,explicit=''):
    if explicit:
        path=Path(explicit).expanduser().resolve()
        if not path.is_file(): raise ValueError('指定数据库不存在：'+str(path))
        return path
    try: return core.database(home)
    except ValueError:
        settings,_=core.global_settings(home)
        roots=[Path(home)]
        if settings.get('sqlite_home'):
            extra=Path(settings['sqlite_home']).expanduser()
            roots.append(extra if extra.is_absolute() else Path(home)/extra)
        if any(list(root.glob('state_*.sqlite')) for root in roots): raise
        return None


def backends(home,db):
    home=Path(home).resolve()
    result={r['pid']:r for r in processes.holders(db) if r.get('terminable')} if db else {}
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit() or int(directory.name)==os.getpid(): continue
        try:
            if directory.stat().st_uid!=os.getuid(): continue
            if (directory/'comm').read_text().strip() not in ('codex','codex.exe'): continue
            args=(directory/'cmdline').read_bytes().split(b'\0')
            if b'app-server' not in args: continue
            env=dict(part.split(b'=',1) for part in (directory/'environ').read_bytes().split(b'\0') if b'=' in part)
            other=Path(env.get(b'CODEX_HOME',str(Path.home()/'.codex').encode()).decode()).expanduser().resolve()
            if other!=home: continue
            item=processes.identity(directory.name); item['terminable']=True
            item['role']='app-server'
            item['environment_overrides']=[key for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_API_BASE','CODEX_API_KEY') if key.encode() in env]
            item['launch_overrides']=any(b'model_provider=' in arg or b'chatgpt_base_url=' in arg or b'openai_base_url=' in arg for arg in args[1:])
            result[item['pid']]=item
        except (FileNotFoundError,ProcessLookupError): continue
        except PermissionError:
            raise RuntimeError('无法核对 Codex 后端 '+directory.name+' 的所属目录；拒绝自动重启。') from None
    return list(result.values())


def validate_targets(home,db,targets):
    current=backends(home,db); approved={p['pid']:p for p in targets}
    for item in current:
        if item['pid'] not in approved or not processes.same_process(item,approved[item['pid']]):
            raise ValueError('后端进程已变化，请重新预览连接同步；未结束进程。')
    return current


def stop_backends(home,db,targets,force=False):
    current=validate_targets(home,db,targets)
    def send(item,sig):
        if not processes.same_process(processes.identity(item['pid']),item): raise ValueError('进程身份变化，未发送信号。')
        if hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'):
            fd=os.pidfd_open(item['pid'])
            try:
                if not processes.same_process(processes.identity(item['pid']),item): raise ValueError('进程身份变化。')
                signal.pidfd_send_signal(fd,sig)
            finally: os.close(fd)
        else: os.kill(item['pid'],sig)
    for item in current:
        try: send(item,signal.SIGTERM)
        except ProcessLookupError: pass
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        remaining=[p for p in backends(home,db) if any(processes.same_process(p,t) for t in current)]
        if not remaining: return
        time.sleep(.1)
    if force:
        for item in remaining:
            try: send(item,signal.SIGKILL)
            except ProcessLookupError: pass
        time.sleep(.15)
    elif remaining: raise RuntimeError('后端未退出；可重新预览并允许强制停止这些已核对的后端。')


def database_backup(db,folder):
    if db is None: return
    c=core.connection(db); dest=sqlite3.connect(str(Path(folder)/db.name))
    try:
        deadline=time.monotonic()+10
        def progress(*_):
            if time.monotonic()>deadline: raise RuntimeError('数据库备份超时，未重启后端。')
        c.backup(dest,pages=256,progress=progress)
        if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('数据库备份未通过校验。')
    finally: dest.close(); c.close()


def verify_connection(home,db,selected=None):
    from remote_agent import select_codex
    selected=selected or select_codex(home,db)[0]
    result={'client':{'path':selected['path'],'version':selected['version']},'inference_tested':False}
    client=discovery.Client(home,selected['path'],timeout=20,clean_connection_env=True)
    try:
        client.call('initialize',{'clientInfo':{'name':'gpt_switch','title':'GPT Switch','version':'2.6.0'}})
        client.send({'method':'initialized','params':{}})
        effective=client.call('config/read',{'includeLayers':False}).get('config',{})
        expected,_=core.global_settings(home)
        if effective.get('model_provider','openai')!=expected.get('model_provider','openai'):
            result.update(status='failed',reason='托管配置/系统策略覆盖了供应商；未绕过策略。'); return result
        account=client.call('account/read',{'refreshToken':False}).get('account')
        if not account:
            result.update(status='failed',reason='未读取到远端有效登录态。'); return result
        result['account_type']=account.get('type','unknown')
        result['models_count']=len(client.call('model/list',{'limit':100}).get('data',[]))
        if account.get('type') in ('apiKey','apikey'):
            result.update(status='credential-loaded',reason='API Key 已加载，尚未验证推理授权；请用新对话测试。')
        else: result.update(status='account-verified',reason='account/read 与 model/list 通过；未发送聊天推理请求。')
    except RuntimeError as e:
        result.update(status='failed',reason=str(e))
    finally: client.close()
    return result


def device_login(home,db,request):
    """Only user code + official verification URL are returned, never raw login logs."""
    from remote_agent import select_codex
    action=request['action']
    if action=='device_start':
        settings,_=core.global_settings(home)
        if settings.get('model_provider','openai')!='openai' or settings.get('chatgpt_base_url') or settings.get('openai_base_url') or settings.get('forced_login_method')=='api':
            raise ValueError('设备码授权仅用于内置 OpenAI / ChatGPT 登录；请先同步对应官方连接配置。')
    base=home/'gpt-switch-login'; base.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(str(base),0o700)
    if action=='device_start':
        backup=sync.backup_files(home)
        selected=select_codex(home,db)[0]; login_id=uuid.uuid4().hex
        folder=base/login_id; folder.mkdir(mode=0o700)
        fd=os.open(str(folder/'login.log'),os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600)
        env=dict(os.environ,CODEX_HOME=str(home))
        for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_API_BASE','CODEX_API_KEY'): env.pop(key,None)
        with os.fdopen(fd,'wb') as output:
            child=subprocess.Popen([selected['path'],'login','--device-auth'],stdin=subprocess.DEVNULL,
                stdout=output,stderr=subprocess.STDOUT,env=env,cwd=str(home),start_new_session=True)
        time.sleep(.1)
        try: token=processes.identity(child.pid)
        except OSError: token=None
        meta={'token':token,'backup':str(backup)}
        sync.atomic_write(folder/'meta.json',json.dumps(meta).encode())
    else:
        login_id=request.get('login_id','')
        if not __import__('re').fullmatch(r'[a-f0-9]{32}',login_id): raise ValueError('无效授权会话。')
        folder=base/login_id
        if folder.is_symlink(): raise ValueError('授权目录不可为链接。')
        meta=json.loads((folder/'meta.json').read_text(encoding='utf-8'))
        if action=='device_cancel' and meta.get('token'):
            token=meta['token']
            try:
                if processes.same_process(processes.identity(token['pid']),token): os.kill(token['pid'],signal.SIGTERM)
            except (ProcessLookupError,FileNotFoundError): pass
            return {'home':str(home),'db':str(db) if db else '', 'login_id':login_id,'status':'cancelled'}
    import re
    content=(folder/'login.log').read_text(encoding='utf-8',errors='replace')[-32768:]
    content=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',content)
    url=re.search(r'https://auth\.openai\.com/[A-Za-z0-9/_-]+',content)
    code=re.search(r'\b[A-Z0-9]{4}-[A-Z0-9]{4}\b',content)
    status='awaiting-user' if code else 'starting'
    if 'successfully logged in' in content.lower(): status='completed'
    elif meta.get('token'):
        try:
            current=processes.identity(meta['token']['pid'])
            if not processes.same_process(current,meta['token']): status='ended'
        except OSError: status='ended'
    else: status='ended'
    return {'home':str(home),'db':str(db) if db else '', 'login_id':login_id,'status':status,
        'verification_url':url.group(0) if url else '', 'user_code':code.group(0) if code else '', 'backup':meta['backup']}


def execute(request):
    home=Path(request.get('home') or os.environ.get('CODEX_HOME') or str(Path.home()/'.codex')).expanduser().resolve()
    action=request['action']; db=optional_db(home,request.get('db',''))
    result={'home':str(home),'db':str(db) if db else '','host':os.uname().nodename,'warnings':[]}
    if action.startswith('device_'): return device_login(home,db,request)
    if action=='connection_plan':
        before=sync.state_hash(home)
        result.update(before=sync.current_summary(home),targets=backends(home,db),state_hash=before)
        result['plan_hash']=sync.fingerprint([before,request['source_fingerprint']])
        if before!=sync.state_hash(home): raise ValueError('远端登录态在预览期间变化，请重试。')
    elif action=='connection_apply':
        from remote_agent import select_codex
        bundle=request['connection']
        copy_bundle=dict(bundle); supplied=copy_bundle.pop('fingerprint',None)
        if supplied!=sync.fingerprint(copy_bundle): raise ValueError('本机同步包已变化，请重新预览。')
        expected=sync.fingerprint([sync.state_hash(home),supplied])
        if expected!=request.get('plan_hash'): raise ValueError('远端配置或本机选择已变化，请重新预览。')
        current=validate_targets(home,db,request.get('targets',[]))
        selected=select_codex(home,db)[0]
        backup=sync.backup_files(home); database_backup(db,backup)
        try:
            if request.get('stop_backends',True): stop_backends(home,db,current,request.get('force',False))
            elif current: result['warnings'].append('未停止原后端，请重新连接 SSH 项目后再测试。')
            # An exiting backend may refresh only its old auth cache. Preserve that too.
            manifest=json.loads((backup/'manifest.json').read_text())
            for item in manifest:
                if item['name']=='auth.json': continue
                path=home/item['name']
                original=(backup/item['name']).read_bytes() if item['existed'] else None
                if (path.read_bytes() if path.exists() else None)!=original:
                    raise ValueError('退出过程中配置发生变化，请重新预览。')
            sync.apply_files(home,bundle,sync.state_hash(home),backup=backup)
        except Exception as e:
            raise RuntimeError('连接同步未完成；备份：'+str(backup)+'；'+str(e)) from None
        result.update(written=True,backup=str(backup),after=bundle['summary'],verification=verify_connection(home,db,selected))
        result['restart_required']=bool(backends(home,db))
        result['warnings'].append('请重新连接对应 SSH 项目，再用新对话测试。')
    elif action=='connection_verify': result['verification']=verify_connection(home,db)
    elif action=='connection_restore':
        if backends(home,db): raise ValueError('恢复前请先停止对应后端，避免恢复后被旧进程覆盖。')
        sync.restore_files(home,request['backup']); result['restored']=True
    else: raise ValueError('未知连接操作')
    return result
