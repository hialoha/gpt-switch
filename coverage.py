"""Read-only coverage inventory. Never silently import missing or remote threads."""
import json
from pathlib import Path
import sync_core as core


def audit(home, db, model, provider, cancel_event=None):
    home=Path(home); db=Path(db).resolve()
    c=core.connection(db)
    try: records=core.rows(c)
    finally: c.close()
    ids={r['id'] for r in records}
    internal=[r for r in records if r['model']=='codex-auto-review']
    regular=[r for r in records if r['model']!='codex-auto-review']
    changed=[r for r in regular if (r['model'],r['model_provider'])!=(model,provider)]
    result={'database':str(db),'database_total':len(records),'internal_skipped':len(internal),
        'regular_total':len(regular),'needs_change':len(changed),'already_matches':len(regular)-len(changed),
        'missing_model':sum(not r['model'] for r in regular),'session_files':0,'unindexed_sessions':[],
        'unreadable_files':[],'other_databases':[],'catalog_host_counts':[], 'catalog_local_missing':[]}
    for directory in ('sessions','archived_sessions'):
        for path in (home/directory).rglob('*.jsonl'):
            core.check_cancel(cancel_event)
            result['session_files']+=1
            try:
                with path.open(encoding='utf-8-sig') as f:
                    line=f.readline(4*1024*1024)
                entry=json.loads(line)
                payload=entry.get('payload',{})
                sid=payload.get('id') or payload.get('session_id')
                if entry.get('type')!='session_meta' or not sid:
                    raise ValueError('无法识别 session_meta / ID')
                if sid not in ids: result['unindexed_sessions'].append({'id':sid,'path':str(path)})
            except (OSError,ValueError) as e:
                result['unreadable_files'].append({'path':str(path),'error':str(e)})
    roots={home,db.parent}
    settings,_=core.global_settings(home)
    if settings.get('sqlite_home'):
        extra=Path(settings['sqlite_home']).expanduser()
        roots.add(extra if extra.is_absolute() else home/extra)
    for path in sorted({p.resolve() for root in roots for p in root.glob('state_*.sqlite')}):
        if path==db: continue
        core.check_cancel(cancel_event)
        try:
            c=core.connection(path)
            try: other=core.rows(c)
            finally: c.close()
            result['other_databases'].append({'path':str(path),'threads':len(other),'not_modified':True})
        except Exception as e: result['unreadable_files'].append({'path':str(path),'error':str(e)})
    catalog=home/'sqlite/codex-dev.db'
    if catalog.exists():
        c=core.connection(catalog)
        try:
            counts=c.execute('SELECT h.host_kind,count(*) FROM local_thread_catalog t LEFT JOIN local_thread_catalog_hosts h ON h.host_id=t.host_id GROUP BY h.host_kind').fetchall()
            result['catalog_host_counts']=[{'kind':kind or 'unknown','count':count} for kind,count in counts]
            local=c.execute("SELECT t.thread_id FROM local_thread_catalog t JOIN local_thread_catalog_hosts h ON h.host_id=t.host_id WHERE h.host_kind='local'").fetchall()
            result['catalog_local_missing']=[r[0] for r in local if r[0] not in ids]
        except Exception as e: result['unreadable_files'].append({'path':str(catalog),'error':str(e)})
        finally: c.close()
    return result


def summary(report):
    remote=sum(x['count'] for x in report['catalog_host_counts'] if x['kind']!='local')
    return (f"数据库共 {report['database_total']} 条：普通对话 {report['regular_total']}，"
            f"需同步 {report['needs_change']}，已一致 {report['already_matches']}，"
            f"内部审核跳过 {report['internal_skipped']}；会话文件 {report['session_files']}，"
            f"未入当前库 {len(report['unindexed_sessions'])}；另有远程/ChatGPT 索引 {remote} 条（不在此库）。")
