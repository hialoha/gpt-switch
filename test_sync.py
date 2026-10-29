import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import sync_core as core


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent)
        self.home = pathlib.Path(self.tmp.name).resolve()
        self.db = self.home/'state_1.sqlite'
        self.c = sqlite3.connect(str(self.db))
        self.c.execute('PRAGMA journal_mode=WAL')
        self.c.execute('CREATE TABLE threads(id TEXT PRIMARY KEY,title TEXT,model TEXT,model_provider TEXT)')
        self.c.executemany('INSERT INTO threads VALUES(?,?,?,?)', [('1','旧对话','old','third'),('2','新对话','new','openai')])
        self.c.commit()

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def config(self, text):
        (self.home/'config.toml').write_text(text, encoding='utf-8')

    def test_preview_is_readonly(self):
        self.assertEqual(len(core.preview(self.db,'new','openai')),1)
        self.assertEqual(self.c.execute('SELECT model FROM threads WHERE id=?',('1',)).fetchone()[0],'old')
        self.assertFalse((self.home/'gpt-switch-backups').exists())

    def test_sync_with_wal_backup(self):
        count, folder = core.sync(self.db,'new','openai',lambda:False)
        self.assertEqual(count,1)
        backup = sqlite3.connect(str(folder/self.db.name))
        try:
            self.assertEqual(backup.execute('SELECT model FROM threads WHERE id=?',('1',)).fetchone()[0],'old')
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally: backup.close()
        self.assertEqual(core.preview(self.db,'new','openai'),[])
        self.assertEqual(core.sync(self.db,'new','openai',lambda:False),(0,None))

    def test_running_refuses_before_backup(self):
        with self.assertRaises(RuntimeError): core.sync(self.db,'new','openai',lambda:True)
        self.assertFalse((self.home/'gpt-switch-backups').exists())

    def test_internal_review_is_preserved(self):
        self.c.execute('INSERT INTO threads VALUES(?,?,?,?)',('internal','review','codex-auto-review','openai'))
        self.c.commit()
        count, _ = core.sync(self.db,'new','openai',lambda:False)
        self.assertEqual(count,1)
        self.assertEqual(self.c.execute('SELECT model FROM threads WHERE id=?',('internal',)).fetchone()[0],'codex-auto-review')

    def test_process_starts_during_update_rolls_back(self):
        checks = iter([False,False,True])
        with self.assertRaises(RuntimeError): core.sync(self.db,'new','openai',lambda:next(checks))
        self.assertEqual(self.c.execute('SELECT model FROM threads WHERE id=?',('1',)).fetchone()[0],'old')
        self.assertEqual(len(list((self.home/'gpt-switch-backups').glob('*/state_1.sqlite'))),1)

    def test_config_profile_file(self):
        self.config('model="base"\nprofile="work"\n')
        (self.home/'work.config.toml').write_text('model="profile-model"\nmodel_provider="custom"\n', encoding='utf-8')
        self.assertEqual(core.global_target(self.home),('profile-model','custom','work'))

    def test_missing_model_no_guess(self):
        self.assertEqual(core.global_target(self.home),('', 'openai',None))
        with self.assertRaises(ValueError): core.preview(self.db,'','openai')

    def test_multiple_databases_requires_selection(self):
        (self.home/'state_2.sqlite').touch()
        with self.assertRaises(ValueError): core.database(self.home)

    def test_catalog_relative_path_and_reload(self):
        self.config('model_catalog_json="模型.json"\n')
        path=self.home/'模型.json'
        path.write_text('{"models":[{"slug":"first"},{"slug":"second"}]}',encoding='utf-8-sig')
        self.assertEqual(core.catalog(self.home),(['first','second'],path))
        self.assertEqual(core.global_target(self.home)[0],'')
        path.write_text('{"models":[{"slug":"changed"}]}',encoding='utf-8')
        self.assertEqual(core.catalog(self.home)[0],['changed'])

    def test_invalid_catalog(self):
        self.config('model_catalog_json="models.json"')
        path=self.home/'models.json'
        for text in ('[]','{"models":[{"id":"bad"}]}','{"models":[{"slug":"x"},{"slug":"x"}]}'):
            path.write_text(text,encoding='utf-8')
            with self.assertRaises(ValueError): core.catalog(self.home)

    def test_gui_smoke_and_monitor_waits(self):
        import tkinter as tk
        from app import App
        with patch.dict('os.environ', {'CODEX_HOME':str(self.home)}):
            root=tk.Tk(); root.withdraw()
            gui=App(root, auto_discover=False); root.update()
            self.assertEqual(gui.db.get(),str(self.db))
            gui.manual.set(True); gui.model.set('new'); gui.provider.set('openai')
            self.assertEqual(gui.target(),(self.db,'new','openai'))
            gui.auto.set(True)
            with patch('sync_core.running',return_value=True): gui.tick()
            self.assertIsNone(gui.future)
            self.assertIn('占用',gui.status.get())
            gui.quit()

    def test_coverage_counts_orphans_and_archives(self):
        import coverage,json
        folder=self.home/'archived_sessions'; folder.mkdir()
        for sid in ('1','orphan'):
            (folder/(sid+'.jsonl')).write_text(json.dumps({'type':'session_meta','payload':{'id':sid}}),encoding='utf-8')
        (folder/'bad.jsonl').write_text('broken',encoding='utf-8')
        report=coverage.audit(self.home,self.db,'new','openai')
        self.assertEqual(report['database_total'],2)
        self.assertEqual(report['needs_change'],1)
        self.assertEqual(report['already_matches'],1)
        self.assertEqual(report['session_files'],3)
        self.assertEqual([x['id'] for x in report['unindexed_sessions']],['orphan'])
        self.assertEqual(len(report['unreadable_files']),1)

    def test_cancel_before_write_and_after_backup(self):
        import threading
        event=threading.Event(); event.set()
        with self.assertRaises(RuntimeError): core.sync(self.db,'new','openai',lambda:False,cancel_event=event)
        self.assertFalse((self.home/'gpt-switch-backups').exists())
        event.clear(); calls=[]
        def check():
            calls.append(1)
            if len(calls)==2: event.set()
            return False
        with self.assertRaises(RuntimeError): core.sync(self.db,'new','openai',check,cancel_event=event)
        self.assertEqual(self.c.execute('SELECT model FROM threads WHERE id=?',('1',)).fetchone()[0],'old')

    def test_x_cancels_busy_worker_and_exits(self):
        import tkinter as tk,time
        from app import App
        with patch.dict('os.environ', {'CODEX_HOME':str(self.home)}):
            root=tk.Tk(); root.withdraw(); gui=App(root,auto_discover=False)
            gui.future=gui.pool.submit(lambda:gui.cancel_event.wait(2))
            destroyed=[]
            root.bind('<Destroy>',lambda e:destroyed.append(True) if e.widget==root else None)
            gui.close()
            deadline=time.monotonic()+3
            while not destroyed and time.monotonic()<deadline:
                root.update(); time.sleep(.01)
            self.assertTrue(destroyed)
            self.assertTrue(gui.cancel_event.is_set())

    def test_gui_no_model_resolves_via_official_default(self):
        import tkinter as tk
        import concurrent.futures
        import discovery
        from app import App
        result={'model':'api-default','provider':'openai','values':['api-default','other'], 'source':'official isDefault'}
        with patch.dict('os.environ', {'CODEX_HOME':str(self.home)}):
            root=tk.Tk(); root.withdraw(); gui=App(root, auto_discover=False)
            gui.job=('discover',False,(discovery.fingerprint(self.home),))
            gui.future=concurrent.futures.Future(); gui.future.set_result(result)
            gui.tick()
            self.assertEqual(gui.target(),(self.db,'api-default','openai'))
            self.assertIn('official',gui.config_note.cget('text'))
            gui.model.set('other'); gui.select_model()
            self.assertTrue(gui.manual.get())
            self.assertEqual(gui.target()[1],'other')
            gui.manual.set(False); gui.load_config()
            self.assertEqual(gui.target()[1],'api-default')
            gui.quit()


if __name__=='__main__': unittest.main()
