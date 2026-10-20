#!/usr/bin/env python3
"""Install the standalone GNOME 51 extension.

The extension is self-contained: it talks to BlueZ directly over D-Bus and does
not need the Python daemon or any virtual environment. This script only copies
the extension files into the user extension folder and enables it.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_UUID = "qcy-h3-control@andre-franciosi.github.io"
EXTENSION_SOURCE = ROOT / "gnome-extension"
FILES = ("metadata.json", "extension.js", "prefs.js", "bluez.js", "rcsp.js")


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def main() -> int:
    try:
        version = run("gnome-shell", "--version").stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("GNOME Shell not found; install the extension in a GNOME session.")
        return 2
    if "51" not in version:
        print(f"Warning: this extension targets GNOME Shell 51; detected: {version}.")

    extension_dir = Path.home() / ".local/share/gnome-shell/extensions" / EXTENSION_UUID
    extension_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(EXTENSION_SOURCE / name, extension_dir / name)

    try:
        # GNOME extensions can only be enabled inside an active Shell session.
        result = run("gnome-extensions", "enable", EXTENSION_UUID, check=False)
        if result.returncode:
            # The Shell may not have reloaded extensions copied just now.
            # Persist the activation so it is loaded on the next login.
            enabled_output = run(
                "gsettings", "get", "org.gnome.shell", "enabled-extensions"
            ).stdout
            enabled = ast.literal_eval(enabled_output)
            if EXTENSION_UUID not in enabled:
                enabled.append(EXTENSION_UUID)
                run(
                    "gsettings", "set", "org.gnome.shell", "enabled-extensions",
                    repr(enabled),
                )
            print("Extension marked for activation; a new GNOME login may be required.")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"Failed to enable the extension: {exc}")
        return 1

    print("GNOME extension installed.")
    print(f"Extension: {extension_dir}")
    print("The headphone button appears in the top panel when the QCY H3 is connected.")
    print("If it does not appear immediately, log out and back into the GNOME session (Wayland does not allow restarting the Shell with Alt+F2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
