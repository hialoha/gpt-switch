"""Transport a standard-library helper over authenticated system OpenSSH."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time

BOOTSTRAP = '''import sys,json,tempfile,pathlib,runpy
package=json.loads(sys.stdin.readline())
with tempfile.TemporaryDirectory(prefix="gpt-switch-") as folder:
 root=pathlib.Path(folder)
 for name,source in package["sources"].items():
  p=root/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(source,encoding="utf-8")
 sys.path.insert(0,str(root))
 try:
  action=package["request"]["action"]
  if action not in ("occupancy","terminate") and sys.version_info<(3,10):
   raise RuntimeError("Remote Python "+sys.version.split()[0]+" is too old for model discovery/sync; requires Python 3.10+. Process diagnostics still work.")
  module="remote_processes.py" if action in ("occupancy","terminate") else "remote_agent.py"
  result=runpy.run_path(str(root/module))["execute"](package["request"])
  print(json.dumps({"ok":True,"result":result},ensure_ascii=True),flush=True)
 except Exception as e:
  print(json.dumps({"ok":False,"error":str(e)},ensure_ascii=True),flush=True)
'''


def aliases(path=None):
    result=[]; visited=set()
    def read(file):
        file=Path(file).expanduser().resolve()
        if file in visited or not file.exists(): return
        visited.add(file)
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            parts=line.split('#',1)[0].strip().split()
            if not parts or parts[0].startswith('#'): continue
            if parts[0].lower()=='host':
                result.extend(p for p in parts[1:] if re.fullmatch(r'[\w.@-]+',p) and not p.startswith('-'))
            if parts[0].lower()=='include':
                import glob
                for pattern in parts[1:]:
                    for child in glob.glob(str(Path(os.path.expanduser(pattern)) if Path(os.path.expanduser(pattern)).is_absolute() else file.parent/pattern)):
                        read(child)
    read(path or Path.home()/'.ssh/config')
    return list(dict.fromkeys(result))


def sources():
    root=Path(__file__).parent
    result={name:(root/name).read_text(encoding='utf-8') for name in
            ('sync_core.py','occupancy.py','coverage.py','discovery.py','remote_agent.py','remote_processes.py','connection_sync.py','connection_remote.py')}
    # Python 3.10 support without pip or changing the remote Python environment.
    import tomllib
    for path in Path(tomllib.__file__).parent.glob('*.py'):
        result['tomllib/'+path.name]=path.read_text(encoding='utf-8')
    return result


def redact_connection_error(text,bundle):
    if not bundle: return text
    values=[]
    def collect(value):
        if isinstance(value,str) and len(value)>=6: values.append(value)
        elif isinstance(value,dict):
            for child in value.values(): collect(child)
        elif isinstance(value,list):
            for child in value: collect(child)
    collect(bundle.get('auth',{}))
    collect(bundle.get('provider',{}).get('http_headers',{}))
    collect(bundle.get('provider',{}).get('query_params',{}))
    for value in sorted(values,key=len,reverse=True): text=text.replace(value,'[REDACTED]')
    return text


def request(host, action, model='', provider='', home='', db='', cancel_event=None, timeout=60, targets=None, force=False, plan_hash=None, connection=None, source_fingerprint='', backup='', stop_backends=True, login_id=''):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',host):
        raise ValueError('请选择有效的 SSH 主机别名，不支持命令或 SSH 参数。')
    package={'sources':sources(),'request':{'action':action,'model':model,'provider':provider,'home':home,'db':db,'targets':targets or [],'force':bool(force),'plan_hash':plan_hash}}
    package['request'].update(connection=connection,source_fingerprint=source_fingerprint,backup=backup,stop_backends=bool(stop_backends),login_id=login_id)
    mutating=action in ('apply','terminate','repair','connection_apply','connection_restore')
    remote_command='for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do if command -v "$p" >/dev/null 2>&1; then exec "$p" -c '+shlex.quote(BOOTSTRAP)+'; fi; done; echo "No Python 3 found" >&2; exit 127'
    command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=5',
        '-o','ServerAliveCountMax=2','--',host,remote_command]
    if cancel_event is not None and cancel_event.is_set(): raise RuntimeError('SSH 操作已取消')
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    outcome={}
    def exchange():
        try: outcome['output']=process.communicate(json.dumps(package,ensure_ascii=True)+'\n')
        except Exception as e: outcome['error']=e
    worker=threading.Thread(target=exchange,daemon=True); worker.start()
    deadline=time.monotonic()+timeout
    while worker.is_alive():
        # During a remote write, wait for the acknowledged transaction outcome on close.
        if (cancel_event is not None and cancel_event.is_set() and not mutating) or time.monotonic()>deadline:
            process.terminate(); worker.join(3)
            if worker.is_alive(): process.kill(); worker.join(2)
            if mutating: raise RuntimeError('SSH 操作结果未知：连接中断/超时。请重新验证，不要盲目重试；配置与数据库备份保存在对应远端目录。')
            raise RuntimeError('SSH 操作已取消或连接超时')
        worker.join(.1)
    if 'error' in outcome: raise outcome['error']
    stdout,stderr=outcome['output']
    if process.returncode:
        if connection is not None: raise RuntimeError('SSH 凭证同步连接失败（退出码 '+str(process.returncode)+'）；未显示原始输出。请重新验证远端状态。')
        raise RuntimeError('SSH 连接失败（请先在终端确认 ssh '+host+' 能免交互登录）：'+stderr[-1200:])
    try: envelope=json.loads(stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        raise RuntimeError('远端未返回有效结果。' if connection is not None else '远端未返回有效结果：'+stderr[-800:]) from None
    if not envelope.get('ok'): raise RuntimeError(redact_connection_error(envelope.get('error','远端操作失败'),connection))
    if mutating:
        try:
            import datetime
            folder=Path(__file__).parent/'ssh-results'; folder.mkdir(exist_ok=True)
            receipt={'ssh_host':host,**envelope['result']}
            (folder/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError: pass
    result=envelope['result']
    if action.startswith('device_'): result['ssh_alias']=host
    return result


def cleanup_logins(pending,sessions):
    """Best-effort cancellation on desktop exit; receives metadata, never credentials."""
    sessions=list(sessions)
    if pending is not None:
        try:
            result=pending.result()
            if isinstance(result,dict) and result.get('login_id') and result.get('ssh_alias') and result.get('status') in ('starting','awaiting-user'):
                sessions.append({'host':result['ssh_alias'],'home':result['home'],'db':result.get('db',''),'login_id':result['login_id']})
        except Exception: pass
    seen=set()
    for item in sessions:
        key=(item['host'],item['login_id'])
        if key in seen: continue
        seen.add(key)
        try: request(item['host'],'device_cancel',home=item['home'],db=item['db'],login_id=item['login_id'],timeout=8)
        except Exception: pass
