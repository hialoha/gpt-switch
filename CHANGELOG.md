# Changelog / 更新日志

## Unreleased — 2.6 preview

**Publication gate: user acceptance testing. No push until the user confirms.**

- Follow the locally selected Codex connection on a trusted SSH host; portable connection-field and supported credential transfer.
- Preview without sending credentials, protected remote backups, active-profile cleanup, transactional file replacement and restoration.
- Separate configuration-write success from authentication/workspace verification, including safe `workspace-401` classification.
- Independent remote ChatGPT device-code login, polling and cancellation; no device codes in tool receipts.
- English, Simplified Chinese, Japanese and German READMEs; project-description drafts and updated security/testing documentation.
- Existing-thread repair remains a separate operation.

## 2.5

- Local and SSH thread model/provider migration with SQLite snapshots.
- One-step reviewed remote repair: backup, stop approved holders, migrate and verify.
- Windows file-holder diagnostics and Linux same-user process checks.
- Host-switch isolation, stale PID rejection, exit cancellation and synthetic live fixtures.
