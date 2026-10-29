"""Portable connection bundles and reversible remote writes. Never return secrets."""
import copy
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
from urllib.parse import urlsplit, urlunsplit
import sync_core as core

KEYS=('model','model_provider','model_reasoning_effort','model_verbosity',
      'model_context_window','model_auto_compact_token_limit','chatgpt_base_url',
      'openai_base_url','forced_login_method','forced_chatgpt_workspace_id',
      'cli_auth_credentials_store','model_catalog_json')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,default=str).encode()).hexdigest()


def endpoint_label(value):
    if not value: return 'OpenAI built-in endpoint'
    url=urlsplit(value)
    return urlunsplit((url.scheme,url.hostname or '',url.path,'',''))


def toml_value(value):
    if isinstance(value,str): return json.dumps(value,ensure_ascii=False)
    if isinstance(value,bool): return 'true' if value else 'false'
    if isinstance(value,int): return str(value)
    if isinstance(value,float): return repr(value)
    if isinstance(value,(datetime.datetime,datetime.date,datetime.time)): return value.isoformat()
    if isinstance(value,list): return '['+', '.join(toml_value(v) for v in value)+']'
    if isinstance(value,dict): return '{ '+', '.join(toml_value(str(k))+' = '+toml_value(v) for k,v in value.items())+' }'
    raise ValueError('Unsupported TOML value type: '+type(value).__name__)


def dump_toml(data):
    text='\n'.join(toml_value(k)+' = '+toml_value(v) for k,v in data.items())+'\n'
    if tomllib.loads(text)!=data: raise ValueError('TOML serialization did not round-trip')
    return text


def build_bundle(home, fallback_model='', fallback_provider='openai'):
    """Called locally. Returned object is secret-bearing and must stay off disk/logs."""
    home=Path(home)
    settings,_=core.global_settings(home)
    # Missing model_provider means Codex's built-in OpenAI default, not a stale UI value.
    provider=settings.get('model_provider') or 'openai'
    model=settings.get('model') or fallback_model
    if not model: raise ValueError('请先在主窗口识别或选择本机可用模型。')
    definition=copy.deepcopy(settings.get('model_providers',{}).get(provider,{})) if provider!='openai' else {}
    if provider in ('ollama','lmstudio','amazon-bedrock'):
        raise ValueError('此入口支持 OpenAI / OpenAI-compatible providers，暂不迁移本地模型或 AWS 凭证链。')
    if definition.get('auth') or definition.get('gateway_oauth'):
        raise ValueError('本机使用外部认证助手，不能把 Windows 助手直接复制到 Linux；请使用远端独立授权。')
    cache={}
    auth_path=home/'auth.json'
    if auth_path.exists():
        try: cache=json.loads(auth_path.read_text(encoding='utf-8-sig'))
        except (ValueError,OSError): raise ValueError('本机 auth.json 无法解析；未读取或显示凭证内容。') from None
    if not isinstance(cache,dict) or not isinstance(cache.get('tokens') or {},dict):
        raise ValueError('本机登录缓存格式不兼容；未显示凭证内容。')
    env_name=definition.get('env_key')
    key=os.environ.get(env_name,'') if env_name and not definition.get('requires_openai_auth') else ''
    key=key or definition.get('experimental_bearer_token','')
    if settings.get('cli_auth_credentials_store') in ('keyring','ephemeral') and not key:
        raise ValueError('本机使用系统凭证库/内存；不导出可能过期的文件缓存。请使用文件缓存或远端独立授权。')
    if not key and (provider=='openai' or definition.get('requires_openai_auth')):
        if cache.get('auth_mode')!='chatgpt': key=cache.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY','')
    if key:
        kind='api'; auth={'OPENAI_API_KEY':key}
        if provider!='openai':
            definition.pop('env_key',None); definition.pop('env_key_instructions',None)
            definition.pop('experimental_bearer_token',None)
            definition['requires_openai_auth']=True
    elif cache.get('tokens',{}).get('access_token') and cache.get('tokens',{}).get('account_id'):
        kind='chatgpt'
        if settings.get('cli_auth_credentials_store') in ('keyring','ephemeral'):
            raise ValueError('当前登录使用系统凭证库/内存；不能假定 auth.json 是当前有效登录。请先使用文件登录缓存或远端设备码授权。')
        if provider!='openai' or settings.get('chatgpt_base_url') or settings.get('openai_base_url'):
            raise ValueError('ChatGPT 登录态仅允许同步到内置 OpenAI 连接；不会把账号令牌发送给自定义代理。')
        auth={k:cache[k] for k in ('auth_mode','tokens','last_refresh') if k in cache}
        auth['auth_mode']='chatgpt'; auth['OPENAI_API_KEY']=None
    else:
        raise ValueError('没有找到可迁移凭证。请确认 cc-switch 已写入 auth.json，或选定供应商的环境变量已对本工具生效。')
    if env_name and not definition.get('requires_openai_auth'):
        raise ValueError('所选供应商环境变量未对本工具生效：'+env_name)
    for header,variable in definition.pop('env_http_headers',{}).items():
        if variable not in os.environ: raise ValueError('缺少供应商请求头环境变量：'+variable)
        definition.setdefault('http_headers',{})[header]=os.environ[variable]
    target={k:copy.deepcopy(settings[k]) for k in KEYS if k in settings and k!='model_catalog_json'}
    target.update(model=model,model_provider=provider,cli_auth_credentials_store='file',forced_login_method=kind)
    if kind=='api' and provider=='openai' and not target.get('openai_base_url'):
        env_base=os.environ.get('OPENAI_BASE_URL') or os.environ.get('OPENAI_API_BASE')
        if env_base: target['openai_base_url']=env_base
    if kind=='api': target.pop('forced_chatgpt_workspace_id',None)
    endpoint=definition.get('base_url') or target.get('openai_base_url')
    if endpoint:
        url=urlsplit(endpoint)
        if url.scheme!='https' or url.hostname in ('localhost','127.0.0.1','::1'):
            raise ValueError('不能直接迁移本机 HTTP/回环代理地址；远端需要可达的 HTTPS 服务地址。')
    catalog=None
    if settings.get('model_catalog_json'):
        path=Path(settings['model_catalog_json']).expanduser()
        if not path.is_absolute(): path=home/path
        catalog=path.read_text(encoding='utf-8-sig')
        if not isinstance(json.loads(catalog).get('models'),list): raise ValueError('本机模型目录格式不兼容。')
    bundle={'settings':target,'provider':definition,'auth':auth,'catalog':catalog,'kind':kind}
    bundle['summary']={'model':model,'provider':provider,'auth_kind':kind,
        'endpoint':endpoint_label(endpoint),'catalog':bool(catalog),'credential_source':'selected env / auth.json' if kind=='api' else 'auth.json'}
    bundle['fingerprint']=fingerprint(bundle)
    return bundle


def paths_for(home):
    home=Path(home).resolve()
    config=home/'config.toml'
    data=tomllib.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {}
    paths=[config,home/'auth.json',home/'gpt-switch-models.json']
    profile=data.get('profile')
    if profile:
        if not re.fullmatch(r'[\w.-]+',profile): raise ValueError('远端 profile 名称不兼容，未修改。')
        extra=home/(profile+'.config.toml')
        if extra.exists(): paths.append(extra)
    for p in paths:
        if p.is_symlink(): raise ValueError('目标配置为符号链接，请先明确真实配置位置：'+str(p))
    return paths,data


def state_hash(home):
    paths,_=paths_for(home)
    return fingerprint([(p.name,p.read_bytes().hex() if p.exists() else None) for p in paths])


def current_summary(home):
    settings,_=core.global_settings(home)
    provider=settings.get('model_provider','openai')
    definition=settings.get('model_providers',{}).get(provider,{})
    auth={}; p=Path(home)/'auth.json'
    if p.exists():
        try: auth=json.loads(p.read_text(encoding='utf-8-sig'))
        except ValueError: pass
    return {'provider':provider,'model':settings.get('model','client default'),
        'endpoint':endpoint_label(definition.get('base_url') or settings.get('openai_base_url') or settings.get('chatgpt_base_url')),
        'auth_kind':auth.get('auth_mode') or ('api' if auth.get('OPENAI_API_KEY') else 'unknown'),
        'credential_store':settings.get('cli_auth_credentials_store','auto')}


def atomic_write(path,data,mode=0o600):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.gpt-switch-',dir=str(path.parent))
    try:
        os.chmod(name,mode)
        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name,str(path)); os.chmod(str(path),mode)
    finally:
        if os.path.exists(name): os.unlink(name)


def backup_files(home):
    home=Path(home).resolve(); paths,_=paths_for(home)
    folder=home/'gpt-switch-config-backups'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True,mode=0o700); os.chmod(str(folder),0o700)
    manifest=[]
    for path in paths:
        item={'name':path.name,'existed':path.exists()}
        if path.exists():
            item['mode']=stat.S_IMODE(path.stat().st_mode)
            atomic_write(folder/path.name,path.read_bytes())
        manifest.append(item)
    atomic_write(folder/'manifest.json',json.dumps(manifest).encode())
    return folder


def restore_files(home,folder):
    home=Path(home).resolve(); folder=Path(folder).resolve()
    if folder.parent!=home/'gpt-switch-config-backups': raise ValueError('备份路径不属于该 Codex 目录。')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    for item in manifest:
        name=item['name']
        if not re.fullmatch(r'(?:config\.toml|auth\.json|gpt-switch-models\.json|[\w.-]+\.config\.toml)',name): raise ValueError('非法备份清单。')
        path=home/name
        if path.is_symlink(): raise ValueError('恢复目标为符号链接，已停止。')
        if item['existed']: atomic_write(path,(folder/name).read_bytes(),0o600 if name=='auth.json' else item.get('mode',0o600))
        elif path.exists(): path.unlink()


def apply_files(home,bundle,expected_hash,backup=None):
    home=Path(home).resolve()
    if state_hash(home)!=expected_hash: raise ValueError('远端配置或登录缓存已变化，请重新预览。')
    paths,data=paths_for(home)
    backup=Path(backup) if backup else backup_files(home)
    target=copy.deepcopy(bundle['settings'])
    if bundle.get('catalog') is not None: target['model_catalog_json']=str(home/'gpt-switch-models.json')
    def update(scope):
        for key in KEYS: scope.pop(key,None)
        scope.update(copy.deepcopy(target))
        provider=target['model_provider']
        providers=scope.get('model_providers',{})
        if provider=='openai': providers.pop('openai',None)
        else:
            scope.setdefault('model_providers',{})[provider]=copy.deepcopy(bundle['provider'])
    update(data)
    if data.get('profile'):
        inline=data.get('profiles',{}).get(data['profile'])
        if inline is not None: update(inline)
    writes={home/'config.toml':dump_toml(data).encode(),home/'auth.json':json.dumps(bundle['auth'],ensure_ascii=True).encode()}
    for path in paths[3:]:
        profile=tomllib.loads(path.read_text(encoding='utf-8-sig')); update(profile)
        writes[path]=dump_toml(profile).encode()
    if bundle.get('catalog') is not None: writes[home/'gpt-switch-models.json']=bundle['catalog'].encode()
    try:
        for path,content in writes.items(): atomic_write(path,content)
    except Exception:
        restore_files(home,backup)
        raise RuntimeError('配置写入失败，已恢复备份；凭证内容未显示。') from None
    return str(backup)
