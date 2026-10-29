import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
import sys
from unittest.mock import patch,MagicMock
import connection_sync as sync
import connection_remote as remote


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root=Path(self.temp.name).resolve(); self.local=self.root/'local'; self.server=self.root/'remote'
        self.local.mkdir(); self.server.mkdir()
        (self.local/'config.toml').write_text('model="fixture-model"\nmodel_provider="openai"\nnotify=["C:/local-only/tool.exe"]\n',encoding='utf-8')
        (self.local/'auth.json').write_text(json.dumps({'OPENAI_API_KEY':'fixture-api-secret'}),encoding='utf-8')

    def tearDown(self): self.temp.cleanup()

    def bundle(self):
        with patch.dict(os.environ,{},clear=True): return sync.build_bundle(self.local)

    def test_bundle_exports_only_connection_fields(self):
        bundle=self.bundle()
        self.assertNotIn('notify',bundle['settings'])
        self.assertEqual(bundle['settings']['forced_login_method'],'api')
        self.assertNotIn('fixture-api-secret',json.dumps(bundle['summary']))
        self.assertEqual(bundle['auth']['OPENAI_API_KEY'],'fixture-api-secret')

    def test_missing_provider_does_not_reuse_stale_ui_provider(self):
        (self.local/'config.toml').write_text('model="fixture-model"',encoding='utf-8')
        self.assertEqual(sync.build_bundle(self.local,fallback_provider='stale-third-party')['settings']['model_provider'],'openai')

    def test_selected_provider_env_key_is_resolved(self):
        (self.local/'config.toml').write_text('model="fixture-model"\nmodel_provider="custom"\n[model_providers.custom]\nname="Custom"\nbase_url="https://example.invalid/v1"\nenv_key="ONLY_THIS_KEY"\n',encoding='utf-8')
        with patch.dict(os.environ,{'ONLY_THIS_KEY':'provider-fixture-secret','UNRELATED_SECRET':'never-copy'},clear=True):
            bundle=sync.build_bundle(self.local)
        self.assertTrue(bundle['provider']['requires_openai_auth'])
        self.assertNotIn('env_key',bundle['provider'])
        self.assertNotIn('never-copy',json.dumps(bundle))

    def test_openai_api_endpoint_env_is_preserved(self):
        with patch.dict(os.environ,{'OPENAI_BASE_URL':'https://example.invalid/v1'},clear=True):
            bundle=sync.build_bundle(self.local)
        self.assertEqual(bundle['settings']['openai_base_url'],'https://example.invalid/v1')

    def test_chatgpt_cache_not_exported_to_custom_gateway(self):
        (self.local/'auth.json').write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'access_token':'fixture-access','account_id':'fixture-account'}}),encoding='utf-8')
        (self.local/'config.toml').write_text('model="fixture-model"\nmodel_provider="third"\n',encoding='utf-8')
        with self.assertRaises(ValueError): self.bundle()

    def test_keyring_cache_not_assumed_current(self):
        (self.local/'auth.json').write_text(json.dumps({'auth_mode':'chatgpt','tokens':{'access_token':'fixture-access','account_id':'fixture-account'}}),encoding='utf-8')
        (self.local/'config.toml').write_text('model="fixture-model"\ncli_auth_credentials_store="keyring"',encoding='utf-8')
        with self.assertRaises(ValueError): self.bundle()

    def test_unrelated_settings_preserved_and_profiles_patched(self):
        original={'profile':'work','model_provider':'third','chatgpt_base_url':'https://old.invalid',
            'projects':{'/srv/project':{'trust_level':'trusted'}},'mcp_servers':{'tool':{'command':'remote-tool'}},
            'profiles':{'work':{'model_provider':'third','sandbox_mode':'read-only','openai_base_url':'https://old.invalid'}}}
        (self.server/'config.toml').write_text(sync.dump_toml(original),encoding='utf-8')
        (self.server/'work.config.toml').write_text('model_provider="third"\nchatgpt_base_url="https://old.invalid"\nmodel_verbosity="low"\n',encoding='utf-8')
        (self.server/'auth.json').write_text('{"OPENAI_API_KEY":"old-fixture"}',encoding='utf-8')
        before=sync.state_hash(self.server)
        backup=sync.apply_files(self.server,self.bundle(),before)
        after=sync.tomllib.loads((self.server/'config.toml').read_text(encoding='utf-8'))
        self.assertEqual(after['projects'],original['projects']); self.assertEqual(after['mcp_servers'],original['mcp_servers'])
        self.assertEqual(after['profiles']['work']['sandbox_mode'],'read-only')
        self.assertNotIn('chatgpt_base_url',after)
        effective,_=sync.core.global_settings(self.server)
        self.assertEqual(effective['model_provider'],'openai'); self.assertNotIn('chatgpt_base_url',effective)
        sync.restore_files(self.server,backup)
        self.assertEqual(sync.state_hash(self.server),before)

    def test_stale_remote_hash_blocks_before_write(self):
        with self.assertRaises(ValueError): sync.apply_files(self.server,self.bundle(),'stale')
        self.assertFalse((self.server/'gpt-switch-config-backups').exists())

    def test_partial_write_rolls_back_original_bytes(self):
        (self.server/'config.toml').write_text('# retained in backup\nmodel="old"\n',encoding='utf-8')
        before=sync.state_hash(self.server); backup=sync.backup_files(self.server)
        original_write=sync.atomic_write; calls=[]
        def fail_once(path,data,mode=0o600):
            calls.append(str(path))
            if len(calls)==2: raise OSError('fixture failure')
            return original_write(path,data,mode)
        with patch('connection_sync.atomic_write',side_effect=fail_once):
            with self.assertRaises(RuntimeError): sync.apply_files(self.server,self.bundle(),before,backup=backup)
        self.assertEqual(sync.state_hash(self.server),before)

    def test_remote_apply_returns_no_credential(self):
        bundle=self.bundle()
        with patch('connection_remote.os.uname',create=True,return_value=types.SimpleNamespace(nodename='fixture')),patch('connection_remote.backends',return_value=[]),patch('remote_agent.select_codex',return_value=({'path':'fake','version':'fixture'},[])),patch('connection_remote.verify_connection',return_value={'status':'account-verified','reason':'fixture'}):
            plan=remote.execute({'action':'connection_plan','home':str(self.server),'source_fingerprint':bundle['fingerprint']})
            result=remote.execute({'action':'connection_apply','home':str(self.server),'connection':bundle,'plan_hash':plan['plan_hash'],'targets':[]})
        self.assertTrue(result['written'])
        self.assertNotIn('fixture-api-secret',json.dumps(result))
        self.assertEqual(json.loads((self.server/'auth.json').read_text())['OPENAI_API_KEY'],'fixture-api-secret')

    def test_verify_failure_is_distinct_from_write_success(self):
        (self.server/'config.toml').write_text('model_provider="openai"',encoding='utf-8')
        client=MagicMock(); client.call.side_effect=[{}, {'config':{'model_provider':'openai'}},RuntimeError('workspace-401')]
        with patch('connection_remote.discovery.Client',return_value=client):
            result=remote.verify_connection(self.server,None,{'path':'fixture','version':'fixture'})
        self.assertEqual(result['status'],'failed'); self.assertIn('workspace-401',result['reason'])
        client.close.assert_called_once()

    def test_device_login_refuses_custom_endpoint_before_launch(self):
        (self.server/'config.toml').write_text('model_provider="third"',encoding='utf-8')
        with patch('connection_remote.subprocess.Popen') as launch:
            with self.assertRaises(ValueError): remote.device_login(self.server,None,{'action':'device_start'})
            launch.assert_not_called()

    def test_device_code_lifecycle_does_not_return_raw_log(self):
        (self.server/'config.toml').write_text('model_provider="openai"\nforced_login_method="chatgpt"\n',encoding='utf-8')
        token={'pid':99999,'start_ticks':'1','boot_id':'fixture','uid':1,'path':'/fixture/codex'}
        def launch(*args,**kwargs):
            kwargs['stdout'].write(b'Open https://auth.openai.com/codex/device code ABCD-EFGH\nprivate-debug-fixture\n')
            kwargs['stdout'].flush()
            return types.SimpleNamespace(pid=99999)
        with patch('remote_agent.select_codex',return_value=({'path':'/fixture/codex','version':'fixture'},[])),patch('connection_remote.subprocess.Popen',side_effect=launch),patch('connection_remote.processes.identity',return_value=token):
            result=remote.device_login(self.server,None,{'action':'device_start'})
        self.assertEqual(result['user_code'],'ABCD-EFGH')
        self.assertNotIn('private-debug-fixture',json.dumps(result))
        changed=dict(token,start_ticks='other')
        with patch('connection_remote.processes.identity',return_value=changed),patch('connection_remote.os.kill') as kill:
            remote.device_login(self.server,None,{'action':'device_cancel','login_id':result['login_id']})
            kill.assert_not_called()

    def test_exit_cleanup_cancels_pending_login_once(self):
        import concurrent.futures,ssh_remote
        session={'host':'fixture-host','home':'/fixture','db':'','login_id':'a'*32}
        pending=concurrent.futures.Future()
        pending.set_result({'ssh_alias':'fixture-host','home':'/fixture','db':'','login_id':'a'*32,'status':'awaiting-user'})
        with patch('ssh_remote.request') as request:
            ssh_remote.cleanup_logins(pending,[session])
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.args[:2],('fixture-host','device_cancel'))

    def test_transport_error_redacts_credentials(self):
        import ssh_remote
        value=ssh_remote.redact_connection_error('failure fixture-api-secret / fixture-access',{'auth':{'OPENAI_API_KEY':'fixture-api-secret','tokens':{'access_token':'fixture-access'}}})
        self.assertNotIn('fixture-api-secret',value); self.assertNotIn('fixture-access',value)

    def test_connection_ui_preview_does_not_display_secret(self):
        import tkinter as tk
        from connection_ui import ConnectionWindow
        root=tk.Tk(); root.withdraw()
        app=MagicMock(); app.future=None; app.closing=False
        app.home.get.return_value=str(self.local); app.model.get.return_value='fixture-model'; app.provider.get.return_value='openai'
        parent=MagicMock(); parent.root=root; parent.parent=app
        parent.host.get.return_value='fixturehost'; parent.home.get.return_value=''; parent.db.get.return_value=''
        ui=ConnectionWindow(parent); ui.preview()
        finish=app.remote_job.call_args.args[1]
        finish((self.bundle(),{'home':'/remote/.codex','db':'','before':{'provider':'old'},'targets':[],'plan_hash':'fixture'}))
        self.assertNotIn('fixture-api-secret',ui.detail.get('1.0','end'))
        self.assertNotIn('fixture-api-secret',ui.log.get('1.0','end'))
        root.update_idletasks()
        root.destroy()


LIVE_FIXTURE='''
_execute=execute
def execute(request):
 import tempfile
 global verify_connection
 verify_connection=lambda *args,**kwargs:{"status":"fixture-verified","reason":"No network/auth requests in fixture"}
 with tempfile.TemporaryDirectory(prefix="gpt-switch-connection-test-") as folder:
  home=Path(folder)
  (home/"config.toml").write_text('model_provider="old"\\n[projects."/srv/fixture"]\\ntrust_level="trusted"\\n',encoding="utf-8")
  (home/"auth.json").write_text('{"OPENAI_API_KEY":"old-fixture-key"}',encoding="utf-8")
  bundle={"settings":{"model":"fixture-model","model_provider":"openai","cli_auth_credentials_store":"file","forced_login_method":"api"},"provider":{},"auth":{"OPENAI_API_KEY":"new-fixture-key"},"catalog":None,"kind":"api","summary":{"model":"fixture-model","provider":"openai","auth_kind":"api"}}
  bundle["fingerprint"]=sync.fingerprint(bundle)
  original=sync.state_hash(home)
  plan=_execute({"action":"connection_plan","home":folder,"source_fingerprint":bundle["fingerprint"]})
  assert not plan["targets"],"fixture must not target production backends"
  result=_execute({"action":"connection_apply","home":folder,"connection":bundle,"plan_hash":plan["plan_hash"],"targets":[]})
  assert result["written"] and result["verification"]["status"]=="fixture-verified"
  assert 'new-fixture-key' not in json.dumps(result)
  assert (home/"auth.json").stat().st_mode & 0o777 == 0o600
  assert sync.core.global_settings(home)[0]["projects"]["/srv/fixture"]["trust_level"]=="trusted"
  _execute({"action":"connection_restore","home":folder,"backup":result["backup"]})
  assert sync.state_hash(home)==original
  return {"selftest":"PASS","credential_redaction":True,"permissions":"0600","rollback_verified":True}
'''


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--live-selftest':
        import ssh_remote
        original=ssh_remote.sources
        def fixture_sources():
            sources=original(); sources['connection_remote.py']+='\n'+LIVE_FIXTURE
            return sources
        with patch('ssh_remote.sources',side_effect=fixture_sources):
            print(ssh_remote.request(sys.argv[2],'connection_fixture'))
    else: unittest.main()
