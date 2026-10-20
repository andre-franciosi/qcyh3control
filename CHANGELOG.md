# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Standalone GNOME Shell 51 extension with a panel button (headphone icon and
  battery bar), ANC menu and an advanced 10-band equalizer preferences window.
- Headless user D-Bus daemon (`qcy-h3-control.service`) that keeps the BLE
  control channel alive and starts on demand.
- JieLi RCSP protocol implementation: handshake, ANC modes and 10-band
  equalizer with presets.
- Automatic detection of the QCY H3 already connected through BlueZ, without
  requiring a new BLE advertisement.
- Optional JSON profile for testing other devices.
- Tkinter diagnostic application.

[Unreleased]: https://github.com/andre-franciosi/qcyh3control/commits/main
