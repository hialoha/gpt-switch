"""Linux process diagnostics; intentionally compatible with Python 3.6+."""
import datetime
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from urllib.parse import quote


def identity(pid):
    directory=Path('/proc')/str(pid)
    stat=(directory/'stat').read_text()
    fields=stat[stat.rfind(')')+2:].split()
    return {'pid':int(pid),'start_ticks':fields[19],
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'uid':directory.stat().st_uid,'name':(directory/'comm').read_text().strip(),
        'path':os.readlink(str(directory/'exe'))}


def holders(db,warnings=None):
    paths={str(Path(str(db)+s).resolve()) for s in ('','-wal','-shm')}
    found=[]
    for directory in Path('/proc').iterdir():
        if not directory.name.isdigit() or int(directory.name)==os.getpid(): continue
        try:
            if directory.stat().st_uid!=os.getuid(): continue
            stat=(directory/'stat').read_text()
            if stat[stat.rfind(')')+2:].split()[0] in ('Z','X'): continue
            for fd in (directory/'fd').iterdir():
                try: target=os.readlink(str(fd))
                except FileNotFoundError: continue
                if target in paths:
                    item=identity(directory.name)
                    item['terminable']=int(directory.name)>1 and Path(item['path']).name.lower() in ('codex','codex.exe')
                    found.append(item); break
        except FileNotFoundError: continue
        except PermissionError:
            try:
                stat=(directory/'stat').read_text()
                if stat[stat.rfind(')')+2:].split()[0] in ('Z','X'): continue
            except FileNotFoundError: continue
            # exec() briefly makes even a same-user process non-dumpable.
            if not getattr(holders,'retrying',False):
                for attempt in range(4):
                    time.sleep(.05)
                    holders.retrying=True
                    try: return holders(db,warnings)
                    except RuntimeError:
                        if attempt==3: raise
                    finally: holders.retrying=False
            try: name=(directory/'comm').read_text().strip()
            except OSError: name='unknown'
            if name not in ('systemd','(sd-pam)','ssh-agent','sshd','sshd-session','sshd-auth','gpg-agent','dbus-daemon','gnome-keyring-d'):
                raise RuntimeError('无法检查同用户进程 '+directory.name+'（'+name+'）的文件占用，拒绝写入。')
            warning='系统登录服务 '+name+'（PID '+directory.name+'）不可读取 fd，已排除。'
            if warnings is not None and warning not in warnings: warnings.append(warning)
    return found


def same_process(a,b):
    return all(a.get(k)==b.get(k) for k in ('pid','start_ticks','boot_id','uid','path'))


def stop_processes(db,targets,force=False):
    if not targets: raise ValueError('请先诊断并选择具体进程')
    current={r['pid']:r for r in holders(db)}
    for target in targets:
        item=current.get(target.get('pid'))
        if not item or not same_process(item,target): raise RuntimeError('进程已变化或不再占用，请重新诊断。')
        if not item['terminable'] or item['pid'] in (os.getpid(),os.getppid()) or item['uid']!=os.getuid():
            raise RuntimeError('仅允许结束同用户、实际占用此数据库的 Codex 进程。')
    folder=Path(db).parent/'gpt-switch-backups'/datetime.datetime.now().strftime('stop-%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True)
    source=sqlite3.connect('file:'+quote(str(Path(db).resolve()),safe='/')+'?mode=ro',uri=True,timeout=3)
    dest=sqlite3.connect(str(folder/Path(db).name))
    try:
        deadline=time.monotonic()+10
        def progress(*args):
            if time.monotonic()>deadline: raise RuntimeError('结束进程前的备份超时，未发送信号。')
        if not hasattr(source,'backup'): raise RuntimeError('结束进程前备份需要 Python 3.7+，未发送信号。')
        source.backup(dest,pages=256,progress=progress)
        if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('备份校验失败，未发送信号。')
    finally: dest.close(); source.close()
    (folder/'stop-processes.json').write_text(json.dumps({'targets':targets,'force':force},ensure_ascii=False,indent=2),encoding='utf-8')
    sent=[]
    for target in targets:
        # Revalidate identity and selected-file ownership immediately before signaling.
        item=next((r for r in holders(db) if r['pid']==target['pid']),None)
        if not item or not same_process(item,target):
            sent.append({'pid':target['pid'],'signal_sent':False,'reason':'进程变化或已释放'}); continue
        sig=signal.SIGKILL if force else signal.SIGTERM
        # pidfd binds the signal to this specific process on supported Python/kernel versions.
        if hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'):
            fd=os.pidfd_open(item['pid'])
            try:
                if same_process(identity(item['pid']),target): signal.pidfd_send_signal(fd,sig)
                else: raise RuntimeError('进程身份发生变化，未发送信号。')
            finally: os.close(fd)
        else:
            if not same_process(identity(item['pid']),target): raise RuntimeError('进程身份发生变化，未发送信号。')
            os.kill(item['pid'],sig)
        sent.append({'pid':item['pid'],'signal_sent':True,'signal':'KILL' if force else 'TERM'})
    deadline=time.monotonic()+3
    remaining=holders(db)
    while remaining and time.monotonic()<deadline:
        time.sleep(.15); remaining=holders(db)
    return {'sent':sent,'holders':remaining,'backup':str(folder)}


def execute(request):
    home=Path(request.get('home') or os.environ.get('CODEX_HOME') or str(Path.home()/'.codex')).expanduser().resolve()
    db=Path(request['db']).expanduser().resolve() if request.get('db') else None
    if db is None:
        files=list(home.glob('state_*.sqlite'))
        if sys.version_info>=(3,10):
            import sync_core
            settings,_=sync_core.global_settings(home)
            if settings.get('sqlite_home'):
                extra=Path(settings['sqlite_home']).expanduser()
                if not extra.is_absolute(): extra=home/extra
                files.extend(extra.glob('state_*.sqlite'))
        files=list({p.resolve() for p in files})
        if len(files)>1: raise RuntimeError('多个远端数据库，请明确指定路径。')
        if files: db=files[0]
    result={'home':str(home),'db':str(db) if db else '', 'host':os.uname().nodename,
        'runtime':sys.version.split()[0],'holders':[],'warnings':[],'db_missing':db is None}
    if db is None:
        if request['action']=='terminate': raise RuntimeError('未找到数据库，未结束进程。')
        result['warnings'].append('此用户目录未找到 state_*.sqlite；请确认主机和路径，不能据此判断其他主机无占用。')
        return result
    if not db.is_file(): raise RuntimeError('远端数据库不存在：'+str(db))
    if request['action']=='terminate': result.update(stop_processes(db,request.get('targets',[]),request.get('force',False)))
    else: result['holders']=holders(db,result['warnings'])
    return result
