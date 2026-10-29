# Security / 安全说明

## Credentials and destinations

Connection sync can transfer file-based ChatGPT access/refresh tokens or a selected provider API key to the displayed SSH host/user. Only use trusted destinations. SSH protects transport, not a compromised server.

Preview sends metadata and a fingerprint, not credentials. Apply keeps the bundle in memory and SSH stdin. Summaries, receipts and errors are designed to omit/redact credential values. Device codes appear in authorization controls, not the general tool log.

Credential-vault/memory-only sessions and external helpers are not blindly exported. ChatGPT tokens are refused for custom proxy configurations. The tool does not bypass managed policies or grant workspace/model access.

## Remote files

- Credential and generated connection files: `0600`.
- Configuration-backup directories: `0700`; backed-up files and manifest: `0600`.
- Device-code logs/session metadata: private remote files. They may contain short-lived codes and must not be published.

Backups intentionally contain usable credentials. Keep them private. An old token may no longer be valid after restoration. Independent remote authorization can be preferable to sharing a refresh-token cache.

The main window attempts to cancel known pending device-code sessions on exit, including a start result returned during shutdown. Network failure can prevent cancellation; the official flow has its own expiry.

## Processes and recovery

Only reviewed same-user Codex backends associated with the selected home/database may be stopped. PID identity/path are rechecked. Parents and newly restarted PIDs are not pursued. Shared backends may carry multiple conversations. Linux scans do not cover other users' handles.

TOML is reserialized: unrelated values are retained, but comments/formatting may change. Original bytes are backed up. Stale previews are rejected. Partial write failure restores files; authentication failure after a completed sync is reported separately rather than silently undoing it.

SQLite snapshots include committed WAL content. Never combine restored databases with stale WAL/SHM. After network interruption, verify before retrying an operation that might have committed.

## Reporting

Use versions and redacted errors. **Never include API keys, auth.json, raw login logs, credential bundles or database/authentication backups.** Use private security reporting if available for sensitive vulnerabilities; do not disclose secrets in public issues.

---

连接同步会把必要凭证发往预览中的 SSH 主机，目标必须可信。凭证通过内存和 SSH 标准输入传送，备份也含认证信息，必须私有保存。

写入成功与认证成功是两个结果；账号权限、网络和策略仍可能导致失败。断网后先验证状态，不要盲目重试。不要向 Issue 或聊天上传密钥、auth.json、数据库备份或完整登录日志。
