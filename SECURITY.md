# Security Policy

## Reporting a vulnerability

If you discover a security issue, please **do not** open a public issue.
Instead, report it privately using GitHub's
[Security Advisories](https://github.com/andre-franciosi/qcyh3control/security/advisories/new)
or by contacting the maintainer directly.

Please include:

- A description of the issue and its impact.
- Steps to reproduce.
- Affected version / commit.

You can expect an initial response within a few days.

## Data handling and privacy

QCY H3 Control is designed to keep your data local:

- **No telemetry, no analytics, no network calls.** The extension only talks
  to the local BlueZ stack over D-Bus and to the headphone over Bluetooth.
- **No credentials are stored.** There are no accounts, tokens or API keys.
- **No files are written to disk.** The extension keeps its state in memory
  only; nothing about your headphone is persisted.
- **Logs** may contain the Bluetooth address of your headphone. Review them
  before sharing publicly.

## Scope

This project controls a Bluetooth audio device. It does not require root and
does not modify system-wide Bluetooth configuration. Reports about the
underlying BlueZ stack or the headphone firmware are out of scope.
