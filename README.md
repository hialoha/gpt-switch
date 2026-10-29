<p align="center"><img src="docs/assets/banner.svg" width="100%" alt="GPT Switch"></p>
<h3 align="center">Switch locally. Keep your SSH Codex environment in step.</h3>
<p align="center">Connection &amp; sign-in sync · Existing-thread migration · Preview · Backup · Verification</p>
<p align="center"><img src="https://img.shields.io/badge/version-2.6%20preview-2563eb" alt="2.6 preview"> <img src="https://img.shields.io/badge/desktop-Windows-0078d4" alt="Windows"> <img src="https://img.shields.io/badge/remote-SSH%20%2F%20Linux-0f766e" alt="SSH Linux"> <img src="https://img.shields.io/badge/dependencies-Python%20stdlib-475569" alt="Python standard library"></p>
<p align="center"><strong>English</strong> | <a href="README_ZH.md">简体中文</a> | <a href="README_JA.md">日本語</a> | <a href="README_DE.md">Deutsch</a> | <a href="CHANGELOG.md">Changelog</a></p>
<p align="center"><a href="#quick-start">Quick start</a> · <a href="#follow-your-local-connection">Connection sync</a> · <a href="#existing-threads">Existing threads</a> · <a href="#backup-and-security">Backup &amp; security</a> · <a href="docs/TESTING.md">Testing</a></p>

---

**GPT Switch** is a Windows desktop companion for Codex environments on your computer and SSH hosts. After switching providers with CC Switch, preview and transfer the required live Codex connection settings and supported credentials to a trusted Linux host—without hand-editing that host's configuration.

It also migrates model/provider fields on existing threads. These are separate operations: **connection sync fixes what new chats use; thread migration updates saved chats.**

> **2.6 preview — awaiting user acceptance testing.** This is an independent maintenance tool, not an OpenAI product. Repository publication of this version is pending. The GUI is currently Simplified Chinese; documentation is available in four languages.

## Features

| Capability | What it does |
| --- | --- |
| Follow local connection | Reads the Codex configuration currently written by CC Switch; transfers only connection-related settings and supported credentials |
| Separate local/remote defaults | Inspecting a remote client never silently replaces your chosen target |
| Authentication diagnostics | Distinguishes files written, account checks passed, credentials merely loaded, and verification failures such as `workspace-401` |
| Device-code recovery | Starts an independent remote ChatGPT login when supported; displays the official verification page and one-time code without putting them in tool logs |
| Existing-thread migration | Previews and updates `model` / `model_provider`, including archived threads; skips internal auto-review records |
| SSH repair | Reviews exact processes, backs up, stops approved holders, updates threads, and checks remaining mismatches |
| Recoverable changes | Consistent SQLite snapshots plus protected configuration/authentication backups; stale-state checks and write-failure rollback |

## Quick start

Requirements: **Windows + Python 3.11 or 3.12 with Tcl/Tk**; Linux **Python 3.10+** for remote sync; Windows OpenSSH with non-interactive login configured. Process-only remote diagnostics also support Python 3.6+.

```powershell
git clone https://github.com/hialoha/gpt-switch.git
cd gpt-switch
.\start.cmd
```

No `pip install` is needed. The launcher prefers an available Codex-bundled Python, then `python` on PATH. To see startup errors:

```powershell
python app.py
```

For the unpublished preview, use the local test copy supplied to you. Cloning the repository retrieves its currently published version.

## Follow your local connection

1. Switch with CC Switch and confirm a **new local chat** works.
2. Open **SSH 远端同步…**, select a host, then **跟随本机连接与登录态…**.
3. Click **预览本机 → 远端**. Review the destination, authentication type, endpoint, and exact backends to stop. Preview sends a fingerprint, not your credentials.
4. Click **备份并同步配置与登录态**. This explicitly transfers credentials over SSH to the displayed host, after remote backups.
5. Review the account/connection checks, reconnect the SSH project, and test a **new remote chat**.
6. If ChatGPT still reports `workspace-401`, use the device-code login controls and verify again. Device-code login must be permitted by the account/workspace.

### What is synchronized?

- Selected model, provider, endpoint, relevant login options, and optional model catalog.
- A supported file-based ChatGPT login cache or selected provider API key. API keys sourced from a selected environment variable are converted to remote file-based OpenAI-compatible authentication.
- Connection overrides in the active remote profile are updated too, so an old profile does not silently retain the third-party route.

### What stays remote?

Projects, MCP servers, plugins, permission/sandbox settings, other profiles, and unrelated environment variables retain their configuration values. TOML files are parsed and reserialized: **comments/formatting may change**, while the exact original files are backed up.

Windows credential-vault / memory-only sessions and external credential-helper commands are **not blindly exported**. ChatGPT account tokens are not copied into a custom proxy configuration. Local HTTP/loopback proxy URLs cannot be directly reused on another machine.

## Existing threads

After connection sync succeeds, use **一键修复远端…** to migrate old chats if needed. It previews records and the exact processes before the action button runs backup → stop approved holders → transaction update → verification.

This does not grant access to a model or repair an account's workspace permissions. A model list is not proof of inference authorization. No chat inference request is sent by connection verification; API-key checks may only establish that the credential was loaded.

Local thread migration is also available through **预览修改** and **立即同步全部**. Scanning distinguishes pending updates, matching records, internal records, session files, and local/SSH/ChatGPT catalog entries.

## Backup and security

| Location | Contents |
| --- | --- |
| `<CODEX_HOME>/gpt-switch-config-backups/<timestamp>/` | Original configuration/authentication files, a restore manifest, and a database snapshot when available before backend shutdown |
| `<database-parent>/gpt-switch-backups/` | Thread-migration snapshots and process-stop snapshots |
| Local tool folder `ssh-results/` | Sanitized successful-write receipts; no credential bundle |

Remote credential files use `0600`; configuration-backup directories use `0700`. Backups contain usable credentials: keep them private. Transfer credentials only to hosts and user accounts you trust. Token copies can expire or diverge after refresh; independent remote authorization may be needed.

The connection window offers restoration of its completed sync backup after the relevant backends are stopped. For manual database recovery: stop holders, preserve current SQLite/WAL/SHM, restore the snapshot, and move aside stale WAL/SHM before restarting. Full database restoration also rolls back later database changes.

If SSH disconnects during a write, **verify before retrying**; a missing receipt does not prove the operation failed. The main window's X exits the tool, waits for sent writes, and attempts to cancel tracked device-code sessions. Network failure can prevent remote cancellation.

## Troubleshooting

- **New remote chats still call a third party:** use connection sync, not just thread migration. Check the selected profile and backend launch/environment overrides.
- **`workspace routing discovery unauthorized (401)`:** verify remote ChatGPT sign-in and workspace access; retry independent device-code login if appropriate. File synchronization cannot grant workspace membership.
- **Unexpected old model:** remote PATH may point to an older client. The tool shows the selected executable/version; remote inspection does not change the sync target.
- **A backend keeps reappearing:** disconnect that host's Codex / VS Code remote session. The tool does not kill parents or chase new PIDs.
- **Keyring or helper-based authentication:** use a supported file cache or independent remote login; do not paste tokens into issues.

## Development

```powershell
python -m unittest -v test_connection_sync.py test_sync.py test_discovery.py test_occupancy.py test_ssh_remote.py
```

See [testing](docs/TESTING.md), [security](SECURITY.md), and [project metadata drafts](docs/PROJECT_METADATA.md). Live fixtures use independent temporary files and synthetic credentials; they do not migrate real chats or export your local login.

Inspired by [CC Switch](https://github.com/farion1231/cc-switch) and [codex-model-sync](https://github.com/WangShuai666/codex-model-sync). GPT Switch reads Codex's current configuration; it does not require CC Switch's internal database or a remote CC Switch installation.

Official references: [Authentication](https://learn.chatgpt.com/docs/auth) · [App Server](https://learn.chatgpt.com/docs/app-server) · [Configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

Report issues with versions and **redacted** error details. Never attach `auth.json`, database backups, API keys, or unredacted login logs.
