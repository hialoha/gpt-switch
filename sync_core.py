"""Database-only synchronization; no credentials or network access."""
import csv
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tomllib
import occupancy
from urllib.parse import quote


def global_settings(home):
    path = Path(home) / 'config.toml'
    config = tomllib.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    active = config.get('profile')
    settings = dict(config)
    if active:
        extra = Path(home) / (active + '.config.toml')
        if active not in config.get('profiles', {}) and not extra.exists():
            raise ValueError('全局配置指定的 profile 不存在：' + str(active))
        settings.update(config.get('profiles', {}).get(active, {}))
        if extra.exists():
            settings.update(tomllib.loads(extra.read_text(encoding='utf-8-sig')))
    return settings, active


def global_target(home):
    settings, active = global_settings(home)
    model = settings.get('model', '')
    provider = settings.get('model_provider', 'openai')
    if not isinstance(model, str) or not isinstance(provider, str):
        raise ValueError('model / model_provider 必须是字符串')
    return model, provider, active


def catalog(home):
    """Read Codex ModelsResponse; never treat the catalog as a default selection."""
    settings, _ = global_settings(home)
    value = settings.get('model_catalog_json')
    if value is None:
        return [], None
    if not isinstance(value, str) or not value.strip():
        raise ValueError('model_catalog_json 必须是有效的本地文件路径')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(home) / path
    path = path.resolve()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise ValueError('模型目录必须是包含 models 数组的 JSON 对象')
    result = []
    seen = set()
    for item in data['models']:
        slug = item.get('slug') if isinstance(item, dict) else None
        if not isinstance(slug, str) or not slug.strip() or slug in seen:
            raise ValueError('模型目录的每条记录必须有唯一的非空 slug')
        seen.add(slug)
        result.append(slug)
    return result, path


def database(home):
    roots={Path(home)}
    settings,_=global_settings(home)
    if settings.get('sqlite_home'):
        extra=Path(settings['sqlite_home']).expanduser()
        roots.add(extra if extra.is_absolute() else Path(home)/extra)
    candidates = list({p.resolve() for root in roots for p in root.glob('state_*.sqlite')})
    if len(candidates) != 1:
        raise ValueError('需要恰好一个 state_*.sqlite；请用数据库选择按钮明确指定。')
    return candidates[0]


def running(db):
    return bool(occupancy.holders(db))


def check_cancel(event):
    if event is not None and event.is_set(): raise RuntimeError('操作已取消，未提交的修改已回滚。')


def connection(db, write=False):
    # SQLite Windows paths work with slash-separated URI paths, not escaped drive colons.
    uri = 'file:' + quote(Path(db).resolve().as_posix(), safe='/:') + ('?mode=rw' if write else '?mode=ro')
    return sqlite3.connect(uri, uri=True, timeout=3)


def rows(c):
    fields = {r[1] for r in c.execute('PRAGMA table_info(threads)')}
    if not {'id', 'model', 'model_provider'} <= fields:
        raise ValueError('数据库结构不兼容：threads 缺少必要字段，未修改。')
    c.row_factory = sqlite3.Row
    title = 'title' if 'title' in fields else "'' AS title"
    return [dict(r) for r in c.execute('SELECT id, ' + title + ', model, model_provider FROM threads')]


def preview(db, model, provider):
    if not model.strip() or not provider.strip():
        raise ValueError('配置未指定模型，请先填写目标模型，或在 config.toml 中设置 model。')
    c = connection(db)
    try:
        return [r for r in rows(c) if r['model'] != 'codex-auto-review'
                and (r['model'], r['model_provider']) != (model, provider)]
    finally:
        c.close()


def sync(db, model, provider, process_check=None, cancel_event=None):
    injected_check = process_check
    def check():
        check_cancel(cancel_event)
        if injected_check is not None: return injected_check()
        items=occupancy.holders(db)
        if items: raise RuntimeError('所选数据库仍被占用：'+occupancy.describe(items)+'。请退出对应程序（也可能是 VS Code 插件）；未修改。')
        return False
    process_check=check
    check_cancel(cancel_event)
    if process_check():
        raise RuntimeError('Codex 正在运行；请完全退出桌面端及 CLI，或启用后台监控等待退出。')
    changes = preview(db, model, provider)
    if not changes:
        return 0, None
    db = Path(db)
    folder = db.parent / 'gpt-switch-backups' / dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    folder.mkdir(parents=True, exist_ok=False)
    c = connection(db, True)
    try:
        dest = sqlite3.connect(str(folder / db.name))
        try:
            c.backup(dest, pages=256, progress=lambda *_: check_cancel(cancel_event))
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份校验失败，未修改数据库')
        finally:
            dest.close()
        (folder / 'changes.json').write_text(json.dumps({'database': str(db.resolve()),
            'target': {'model': model, 'model_provider': provider}, 'before': changes},
            ensure_ascii=False, indent=2), encoding='utf-8')
        if process_check():
            raise RuntimeError('备份完成，但 Codex 已启动；取消修改。')
        c.execute('BEGIN IMMEDIATE')
        # Abort if anything selected changed since preview; other new rows wait for the next run.
        for r in changes:
            check_cancel(cancel_event)
            n = c.execute('UPDATE threads SET model=?, model_provider=? WHERE id=? AND model IS ? AND model_provider IS ?',
                (model, provider, r['id'], r['model'], r['model_provider'])).rowcount
            if n != 1:
                raise RuntimeError('对话已被其他程序修改，本次同步已回滚')
        if process_check():
            raise RuntimeError('Codex 已启动，本次同步已回滚')
        check_cancel(cancel_event)
        c.commit()
        return len(changes), folder
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()
