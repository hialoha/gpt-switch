"""Use the installed Codex app-server's public discovery RPCs, without inference."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time


def fingerprint(home):
    home = Path(home).resolve()
    files = [home/'config.toml', *home.glob('*.config.toml')]
    try:
        from sync_core import global_settings
        value = global_settings(home)[0].get('model_catalog_json')
        if isinstance(value,str) and value:
            path = Path(value).expanduser()
            files.append(path if path.is_absolute() else home/path)
    except (ValueError,OSError): pass
    return (str(home), tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files if p.exists()))


def cached_models(home):
    """Offline candidates only; cache ordering is not a default-model signal."""
    path = Path(home)/'models_cache.json'
    if not path.exists(): return []
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        return list(dict.fromkeys(r['slug'] for r in data.get('models', [])
            if isinstance(r, dict) and isinstance(r.get('slug'), str) and r['slug']
            and r.get('visibility') != 'hide' and r['slug'] != 'codex-auto-review'))
    except (ValueError, OSError, AttributeError): return []


def find_codex():
    # Prefer the desktop-bundled binary over an unrelated npm installation.
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local')))/'OpenAI/Codex/bin'
    candidates = list(base.glob('*/codex.exe')) if base.exists() else []
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    for name in ('codex.exe', 'codex'):
        found = shutil.which(name)
        if found and Path(found).suffix.lower() not in ('.cmd', '.bat', '.ps1'):
            return found
    raise RuntimeError('未找到 Codex 可执行程序。可从已有对话选目标，或手动填写。')


class Client:
    def __init__(self, home, executable=None, timeout=25, cancel_event=None, clean_connection_env=False):
        self.cancel_event=cancel_event
        env = dict(os.environ, CODEX_HOME=str(Path(home).resolve()))
        if clean_connection_env:
            for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_API_BASE','CODEX_API_KEY','CODEX_MODEL_PROVIDER'):
                env.pop(key,None)
        self.p = subprocess.Popen([executable or find_codex(), 'app-server'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', env=env, cwd=str(Path(home).resolve()),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.messages = queue.Queue()
        self.deadline = time.monotonic()+timeout
        self.serial = 0
        def read():
            try:
                for line in self.p.stdout:
                    try: self.messages.put(json.loads(line))
                    except json.JSONDecodeError: pass
            finally: self.messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def send(self, message):
        self.p.stdin.write(json.dumps(message)+'\n'); self.p.stdin.flush()

    def call(self, method, params):
        self.serial += 1
        serial = self.serial
        self.send({'id':serial, 'method':method, 'params':params})
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set(): raise RuntimeError('模型发现已取消')
            left = self.deadline-time.monotonic()
            if left <= 0: raise TimeoutError('Codex 模型发现超时，请重试或从已有对话选择目标。')
            try: message = self.messages.get(timeout=min(left,.2))
            except queue.Empty: continue
            if message is None: raise RuntimeError('Codex 模型发现进程提前退出。')
            if message.get('id') != serial: continue
            if 'error' in message:
                # Do not display raw server messages, which may contain credential URLs.
                raw=str(message['error'].get('message','')).lower()
                category=('workspace-401' if 'workspace' in raw and ('401' in raw or 'unauthorized' in raw)
                    else 'auth-401' if '401' in raw or 'unauthorized' in raw
                    else 'network' if any(s in raw for s in ('connection','timeout','tls','network')) else 'rpc-error')
                raise RuntimeError('Codex '+method+'：'+category+'（错误码 '+str(message['error'].get('code'))+'）')
            return message.get('result', {})

    def close(self):
        if self.p.poll() is None:
            self.p.terminate()
            try: self.p.wait(timeout=3)
            except subprocess.TimeoutExpired: self.p.kill(); self.p.wait(timeout=3)
        self.reader.join(timeout=1)
        for pipe in (self.p.stdin,self.p.stdout):
            try: pipe.close()
            except OSError: pass


def resolve(config, models):
    """An explicit model wins; otherwise use the API's unique recommended default."""
    provider = config.get('model_provider') or config.get('modelProvider') or 'openai'
    model = config.get('model')
    source = 'Codex 有效配置（config/read）'
    if not model:
        defaults = [r.get('model') or r.get('id') for r in models if r.get('isDefault') is True]
        if len(defaults) != 1 or not defaults[0]:
            return '', provider, 'Codex 没有返回唯一默认模型，请从下拉列表或已有对话选择。'
        model = defaults[0]
        source = '此 Codex 客户端推荐默认值（model/list · isDefault，随版本/账号变化）'
    return model, provider, source


def discover(home, executable=None, cancel_event=None):
    client = Client(home, executable, cancel_event=cancel_event)
    try:
        client.call('initialize', {'clientInfo':{'name':'gpt_switch','title':'GPT Switch','version':'2.0.0'}})
        client.send({'method':'initialized','params':{}})
        config = client.call('config/read', {'includeLayers':False}).get('config', {})
        models = []
        cursor = None
        for _ in range(20):
            params = {'limit':100, 'includeHidden':False}
            if cursor: params['cursor'] = cursor
            response = client.call('model/list',params)
            models.extend(response.get('data', []))
            cursor = response.get('nextCursor')
            if not cursor: break
        else: raise RuntimeError('Codex 模型列表分页超出限制。')
        model, provider, source = resolve(config, models)
        values = list(dict.fromkeys(r.get('model') or r.get('id') for r in models if r.get('model') or r.get('id')))
        return {'model':model,'provider':provider,'source':source,'values':values}
    finally:
        client.close()
