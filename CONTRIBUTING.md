# Contributing

Thanks for your interest in improving QCY H3 Control!

## Development setup

The GNOME extension is plain JavaScript and needs no build step. Clone the
repository and install it with:

```sh
git clone https://github.com/andre-franciosi/qcyh3control.git
cd qcyh3control
python3 scripts/install_gnome.py
```

The optional Python diagnostic app uses a virtual environment:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

## Running the tests

```sh
./.venv/bin/python -m pytest
```

For the GNOME extension, check the JavaScript syntax:

```sh
node --check gnome-extension/extension.js
node --check gnome-extension/prefs.js
node --check gnome-extension/bluez.js
node --check gnome-extension/rcsp.js
```

## Guidelines

- Keep changes focused and add tests for new behavior.
- **Never commit personal data**: no real Bluetooth addresses, usernames,
  absolute home paths or device-specific profiles. Use placeholders such as
  `AA:BB:CC:DD:EE:FF` in tests and documentation.
- Match the existing code style and keep user-facing strings in English.
- Update the README when you change behavior or installation steps.

## Submitting changes

1. Fork the repository and create a branch.
2. Make your changes and ensure tests pass.
3. Open a pull request describing what you changed and why.
