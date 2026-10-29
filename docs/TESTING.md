# Testing / 测试说明

## Automated tests

Use Windows Python 3.11/3.12 with Tcl/Tk, from the project directory:

```powershell
python -m unittest -v test_connection_sync.py test_sync.py test_discovery.py test_occupancy.py test_ssh_remote.py
```

The default suite creates temporary fixture files, uses no production credentials, and does not contact SSH hosts. It covers selected credential export, profile overrides, unrelated-setting preservation, stale previews, write rollback, secret redaction, login failures, SQLite/WAL snapshots, process identity and GUI state isolation.

## Explicit SSH fixtures

Run only on a Linux host you control with non-interactive OpenSSH access:

```powershell
python test_connection_sync.py --live-selftest dev-server
python test_ssh_remote.py --live-selftest dev-server
python test_ssh_remote.py --live-stop-selftest dev-server
python test_ssh_remote.py --live-repair-selftest dev-server
```

These use independent temporary directories/databases, synthetic credentials and controlled test processes. The connection fixture stubs account verification: it proves transfer, permissions and restoration, **not real ChatGPT/API authentication**. They do not migrate real chats, export the operator's actual local login or stop production backends.

## 2.6 user acceptance / 用户验收

This version remains local until the user confirms. No push or GitHub metadata update during testing.

1. **Local first / 本机先确认**：switch with CC Switch and verify a new local chat.
2. **Preview / 预览**：select SSH host and open 跟随本机连接与登录态… . Check provider/model, login type, endpoint and exact listed backends. No keys/tokens should appear.
3. **Sync / 同步**：click 备份并同步配置与登录态. Confirm the remote backup path. Stopping listed backends may disconnect their conversations.
4. **Verify / 验证**：file-write success is separate from account/connection success. API-key status may only be credential-loaded; actual inference needs a new-chat test.
5. **Reconnect / 重连**：reconnect the SSH project, create a new chat and confirm the intended provider is used.
6. **Recovery / 重新授权**：if workspace-401 remains, use the official device-code login controls, verify again and reconnect. Workspace access must already exist on the account.
7. **Old chats / 旧对话**：after new chats work, optionally use the separate migration workflow for stale saved model/provider fields.
8. **Preservation / 保留设置**：confirm remote MCP/project/permission values remain suitable. Comments may be reformatted; original text is backed up.
9. **Restore / 回滚**：after stopping relevant backends, restore the completed configuration backup if needed. Never publish authentication backups.
10. **Publish gate / 发布门槛**：only explicit user acceptance authorizes pushing the preview or applying repository metadata drafts.

Credential-vault sessions, external helpers, parent-process environment overrides, device-code eligibility, real model/workspace access, network reachability and managed policies remain environment-dependent.
