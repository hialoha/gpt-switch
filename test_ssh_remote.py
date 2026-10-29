import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import ssh_remote

FIXTURE = '''
original_execute=execute
def execute(request):
 import tempfile,sqlite3
 with tempfile.TemporaryDirectory(prefix="gpt-switch-test-") as folder:
  home=Path(folder); db=home/"state_1.sqlite"
  c=sqlite3.connect(str(db)); c.execute("PRAGMA journal_mode=WAL")
  c.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, model TEXT, model_provider TEXT)")
  c.execute("INSERT INTO threads VALUES('test','old','third')"); c.commit(); c.close()
  args={"home":folder,"db":str(db),"model":"test-new","provider":"openai"}
  before=original_execute(dict(args,action="preview"))
  result=original_execute(dict(args,action="apply"))
  backup=sqlite3.connect(str(Path(result["backup"])/db.name))
  assert backup.execute("SELECT model FROM threads").fetchone()[0]=="old"
  backup.close()
  after=original_execute(dict(args,action="preview"))
  assert len(before["rows"])==1 and len(after["rows"])==0 and result["count"]==1
  return {"selftest":"PASS","backup_verified":True}
'''

STOP_FIXTURE = '''
def execute(request):
 import tempfile,shutil,subprocess
 with tempfile.TemporaryDirectory(prefix="gpt-switch-stop-test-") as folder:
  root=Path(folder); db=root/"test.sqlite"
  c=sqlite3.connect(str(db)); c.execute("CREATE TABLE test(x)"); c.commit(); c.close()
  binary=root/"codex"; shutil.copy2(sys.executable,str(binary))
  outcomes=[]
  for force in (False,True):
   child=subprocess.Popen([str(binary),"-c","import sys,signal; f=open(sys.argv[1], 'rb'); signal.pause()",str(db)],env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   try:
    deadline=time.monotonic()+4; selected=[]
    while time.monotonic()<deadline:
     selected=[r for r in holders(db) if r["pid"]==child.pid]
     if selected: break
     time.sleep(.05)
    assert len(selected)==1, "fixture holder not ready; returncode="+str(child.poll())+" stderr="+(child.stderr.read().decode(errors="replace") if child.poll() is not None else "running")
    stale=dict(selected[0],start_ticks="invalid")
    try: stop_processes(db,[stale],force)
    except RuntimeError: pass
    else: raise AssertionError("stale identity was accepted")
    assert child.poll() is None
    result=stop_processes(db,selected,force)
    child.wait(timeout=4)
    assert child.pid not in [r["pid"] for r in result["holders"]]
    backup=sqlite3.connect(str(Path(result["backup"])/db.name))
    assert backup.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    backup.close(); outcomes.append(result["sent"][0]["signal"])
   finally:
    if child.poll() is None: child.kill(); child.wait()
    child.stderr.close()
  return {"selftest":"PASS","signals":outcomes,"backup_verified":True,"stale_pid_rejected":True}
'''

REPAIR_FIXTURE = '''
original_execute=execute
def execute(request):
 import tempfile,sqlite3,time
 with tempfile.TemporaryDirectory(prefix="gpt-switch-repair-test-") as folder:
  home=Path(folder); db=home/"state_1.sqlite"
  c=sqlite3.connect(str(db)); c.execute("PRAGMA journal_mode=WAL")
  c.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,model TEXT,model_provider TEXT)")
  c.execute("INSERT INTO threads VALUES('test','old','third')"); c.commit(); c.close()
  binary=home/"codex"; shutil.copy2(sys.executable,str(binary))
  child=subprocess.Popen([str(binary),"-c","import sys,signal; f=open(sys.argv[1], 'rb'); signal.pause()",str(db)],env=dict(os.environ,PYTHONHOME=sys.base_prefix))
  try:
   deadline=time.monotonic()+4
   while time.monotonic()<deadline:
    if any(r["pid"]==child.pid for r in holders(db)): break
    time.sleep(.05)
   args={"home":folder,"db":str(db),"model":"new","provider":"openai"}
   plan=original_execute(dict(args,action="plan"))
   assert len(plan["holders"])==1 and plan["holders"][0]["pid"]==child.pid
   result=original_execute(dict(args,action="repair",targets=plan["holders"],plan_hash=plan["plan_hash"],force=False))
   child.wait(timeout=4)
   assert result["count"]==1 and result["verified_remaining"]==0
   assert result["stop_backups"] and result["backup"]
   backup=sqlite3.connect(str(Path(result["backup"])/db.name))
   assert backup.execute("SELECT model FROM threads").fetchone()[0]=="old"
   backup.close()
   return {"selftest":"PASS","one_click_count":result["count"],"remaining":result["verified_remaining"],"backup_verified":True}
  finally:
   if child.poll() is None: child.kill(); child.wait()
'''


class RemoteTests(unittest.TestCase):
    def test_aliases_include_and_wildcards(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path=Path(folder)/'config'; include=Path(folder)/'extra'
            path.write_text('Host one two # ignore words\nHost *.invalid !negative\nInclude extra\n',encoding='utf-8')
            include.write_text('Host three\nInclude config\n',encoding='utf-8')
            self.assertEqual(ssh_remote.aliases(path),['one','two','three'])

    def test_host_cannot_be_a_command(self):
        for value in ('-oProxyCommand=bad','host;bad','host bad','$(bad)'):
            with self.assertRaises(ValueError): ssh_remote.request(value,'preview')

    def test_bootstrap_helper_backup_roundtrip(self):
        sources=ssh_remote.sources()
        sources['remote_agent.py']=sources['remote_agent.py'].replace('os.uname().nodename',repr('testhost')).replace('signal.SIGHUP','signal.SIGTERM')+'\nholders=lambda *args: []\n'+FIXTURE
        package={'sources':sources,'request':{'action':'fixture'}}
        result=subprocess.run([sys.executable,'-c',ssh_remote.BOOTSTRAP],input=json.dumps(package),text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        envelope=json.loads(result.stdout)
        self.assertTrue(envelope['ok'],envelope)
        self.assertEqual(envelope['result']['selftest'],'PASS')

    def test_remote_ui_requires_preview_and_invalidates_on_change(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        from remote_ui import RemoteWindow
        root=tk.Tk(); root.withdraw()
        parent=MagicMock(); parent.root=root; parent.future=None
        parent.model.get.return_value='new'; parent.provider.get.return_value='openai'
        window=RemoteWindow(parent); window.host.set('testhost')
        window.run('apply'); parent.remote_job.assert_not_called()
        window.run('preview')
        callback=parent.remote_job.call_args.args[1]
        callback({'home':'/home/test/.codex','db':'/home/test/.codex/state_5.sqlite',
          'rows':[],'audit':{'database_total':1,'already_matches':1,'internal_skipped':0}})
        self.assertEqual(window.preview_key,window.key())
        parent.remote_job.reset_mock(); window.model.set('changed')
        window.run('apply'); parent.remote_job.assert_not_called()
        root.destroy()

    def test_inspecting_old_remote_does_not_overwrite_local_target(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        from remote_ui import RemoteWindow
        root=tk.Tk(); root.withdraw()
        parent=MagicMock(); parent.root=root; parent.future=None
        parent.model.get.return_value='current-local'; parent.provider.get.return_value='openai'
        window=RemoteWindow(parent); window.host.set('testhost')
        window.run('discover')
        callback=parent.remote_job.call_args.args[1]
        callback({'home':'/home/test/.codex','db':'/home/test/.codex/state_5.sqlite',
          'model':'old-remote','provider':'thirdparty','values':['old-remote'],
          'client':{'path':'/snap/bin/codex','version':'0.114.0'}})
        self.assertEqual(window.model.get(),'current-local')
        self.assertEqual(window.provider.get(),'openai')
        window.adopt_remote()
        self.assertEqual(window.model.get(),'old-remote')
        self.assertEqual(window.provider.get(),'thirdparty')
        self.assertIsNone(window.preview_key)
        window.adopt_local()
        self.assertEqual(window.model.get(),'current-local')
        self.assertEqual(window.provider.get(),'openai')
        root.destroy()

    def test_selects_database_client_instead_of_old_path_cli(self):
        from unittest.mock import MagicMock
        import remote_agent
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            home=Path(folder).resolve()
            entries=[{'path':'/opt/new/codex'},{'path':'/usr/bin/python3'}]
            def version(args,**kwargs):
                self.assertNotEqual(args[0],'/usr/bin/python3')
                return MagicMock(stdout='codex-cli '+('0.162.1' if args[0]=='/opt/new/codex' else '0.114.0'))
            with patch('remote_agent.holders',return_value=entries),patch('remote_agent.shutil.which',return_value='/snap/bin/codex'),patch('remote_agent.Path.home',return_value=home),patch('remote_agent.subprocess.run',side_effect=version):
                selected,candidates=remote_agent.select_codex(home,home/'state.sqlite')
            self.assertEqual(selected['path'],'/opt/new/codex')
            self.assertTrue(selected['active_database_client'])
            self.assertEqual(len(candidates),2)

    def test_switch_host_clears_paths_and_discards_late_response(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        from remote_ui import RemoteWindow
        root=tk.Tk(); root.withdraw(); parent=MagicMock()
        parent.root=root; parent.future=None
        parent.model.get.return_value='current'; parent.provider.get.return_value='openai'
        window=RemoteWindow(parent); window.host.set('first')
        window.home.set('/first/.codex'); window.db.set('/first/state.sqlite')
        window.run('discover'); callback=parent.remote_job.call_args.args[1]
        window.preview_key=window.key(); window.auto.set(True)
        window.host.set('second')
        self.assertEqual(window.home.get(),''); self.assertEqual(window.db.get(),'')
        self.assertIsNone(window.preview_key); self.assertFalse(window.auto.get())
        # Even an A -> B -> A switch must invalidate an in-flight A response.
        window.host.set('first'); window.home.set('/first/.codex'); window.db.set('/first/state.sqlite')
        callback({'home':'/late/.codex','db':'/late/state.sqlite','model':'old','provider':'third'})
        self.assertEqual(window.home.get(),'/first/.codex')
        self.assertIsNone(window.remote_result)
        root.destroy()

    def test_stop_requires_fresh_diagnostic_context(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        from remote_ui import RemoteWindow
        root=tk.Tk(); root.withdraw(); parent=MagicMock(); parent.root=root; parent.future=None
        parent.model.get.return_value='current'; parent.provider.get.return_value='openai'
        window=RemoteWindow(parent); window.host.set('first')
        window.run('terminate',targets=[{'pid':9999}]); parent.remote_job.assert_not_called()
        self.assertIn('诊断',window.status.get())
        root.destroy()

    def test_diagnostic_helper_has_python36_compatible_syntax(self):
        import ast
        ast.parse((Path(__file__).parent/'remote_processes.py').read_text(encoding='utf-8'),feature_version=(3,6))
        ast.parse(ssh_remote.BOOTSTRAP,feature_version=(3,6))

    def test_stale_process_refused_before_backup(self):
        import remote_processes
        token={'pid':9000,'start_ticks':'100','boot_id':'boot','uid':1000,'path':'/bin/codex','terminable':True}
        with patch('remote_processes.holders',return_value=[token]),patch('remote_processes.sqlite3.connect') as connect:
            with self.assertRaises(RuntimeError): remote_processes.stop_processes('/unused',[dict(token,start_ticks='changed')])
            connect.assert_not_called()

    def test_repair_rejects_stale_plan_before_stopping(self):
        import remote_agent
        with patch('remote_agent.plan_hash',return_value='current'),patch('remote_processes.stop_processes') as stop:
            with self.assertRaises(RuntimeError): remote_agent.repair('/unused','new','openai',{'plan_hash':'old'})
            stop.assert_not_called()

    def test_repair_refuses_new_unapproved_holder(self):
        import remote_agent
        with patch('remote_agent.plan_hash',return_value='hash'),patch('remote_agent.core.preview',return_value=[{'id':'1'}]),patch('remote_agent.holders',return_value=[{'pid':999,'terminable':True}]),patch('remote_processes.stop_processes') as stop:
            with self.assertRaises(RuntimeError): remote_agent.repair('/unused','new','openai',{'plan_hash':'hash','targets':[]})
            stop.assert_not_called()

    def test_one_click_plan_then_confirm_workflow(self):
        import tkinter as tk
        from unittest.mock import MagicMock
        from remote_ui import RemoteWindow
        root=tk.Tk(); root.withdraw(); parent=MagicMock(); parent.root=root; parent.future=None
        parent.model.get.return_value='current'; parent.provider.get.return_value='openai'
        window=RemoteWindow(parent); window.host.set('testhost')
        window.run('repair'); parent.remote_job.assert_not_called()
        window.run('plan'); callback=parent.remote_job.call_args.args[1]
        callback({'home':'/home/test/.codex','db':'/home/test/.codex/state.sqlite','rows':[{'id':'1','title':'test','model':'old','model_provider':'third'}],
          'audit':{'database_total':1,'already_matches':0,'internal_skipped':0},'holders':[], 'plan_hash':'hash'})
        self.assertIsNotNone(window.repair_plan)
        parent.remote_job.reset_mock(); window.run('repair',targets=[],plan_hash='hash')
        parent.remote_job.assert_called_once()
        root.destroy()


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1] in ('--live-selftest','--live-stop-selftest','--live-repair-selftest'):
        stopping=sys.argv[1]=='--live-stop-selftest'
        repairing=sys.argv[1]=='--live-repair-selftest'
        original=ssh_remote.sources
        def fixture_sources():
            sources=original()
            name='remote_processes.py' if stopping else 'remote_agent.py'
            sources[name]+='\n'+(STOP_FIXTURE if stopping else (REPAIR_FIXTURE if repairing else FIXTURE))
            return sources
        with patch('ssh_remote.sources',side_effect=fixture_sources):
            print(ssh_remote.request(sys.argv[2],'occupancy' if stopping else 'fixture'))
    else: unittest.main()
