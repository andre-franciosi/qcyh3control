# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Self-contained GNOME Shell 51 extension with a panel button (headphone icon
  and battery bar), ANC menu and an advanced 10-band equalizer preferences
  window. It talks to BlueZ directly over D-Bus and needs no daemon or Python.
- JieLi RCSP protocol implementation in JavaScript: handshake, ANC modes and
  10-band equalizer with presets.
- Automatic detection of the QCY H3 already connected through BlueZ, without
  requiring a new BLE advertisement.
- Optional Tkinter diagnostic application (Python).

[Unreleased]: https://github.com/andre-franciosi/qcyh3control/commits/main
