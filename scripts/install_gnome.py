#!/usr/bin/env python3
"""Install the D-Bus daemon and the standalone GNOME 51 extension."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_UUID = "qcy-h3-control@andre-franciosi.github.io"
EXTENSION_SOURCE = ROOT / "gnome-extension"
BUS_NAME = "io.github.qcyh3control"


def run(*command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def main() -> int:
    # Do not resolve the symlink: on Linux venvs this may point to the global
    # Python and the service would lack the dependencies installed in the env.
    python = Path(sys.executable).absolute()
    if python.parent.name != "bin" or ".venv" not in python.parts:
        print("Run this installer with .venv/bin/python:")
        print(f"  {ROOT / '.venv/bin/python'} {ROOT / 'scripts/install_gnome.py'}")
        return 2

    try:
        version = run("gnome-shell", "--version").stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("GNOME Shell not found; install the extension in a GNOME session.")
        return 2
    if "51" not in version:
        print(f"Warning: this extension targets GNOME Shell 51; detected: {version}.")

    # Install the package in editable mode into the venv used by the service.
    run(str(python), "-m", "pip", "install", "-e", str(ROOT))

    extension_dir = Path.home() / ".local/share/gnome-shell/extensions" / EXTENSION_UUID
    extension_dir.mkdir(parents=True, exist_ok=True)
    for name in ("metadata.json", "extension.js", "prefs.js"):
        shutil.copy2(EXTENSION_SOURCE / name, extension_dir / name)

    service_dir = Path.home() / ".config/systemd/user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "qcy-h3-control.service"
    # Type=dbus + BusName makes systemd start the daemon at login (via
    # graphical-session.target) and also on demand when the extension opens the
    # D-Bus proxy. PartOf ensures it stops together with the graphical session.
    service_path.write_text(
        "[Unit]\n"
        "Description=QCY H3 BLE control D-Bus service\n"
        "PartOf=graphical-session.target\n"
        "After=graphical-session.target\n\n"
        "[Service]\n"
        "Type=dbus\n"
        f"BusName={BUS_NAME}\n"
        f"ExecStart={python} -m qcyh3control.daemon\n"
        f"WorkingDirectory={ROOT}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=graphical-session.target\n",
        encoding="utf-8",
    )

    # Remove the old symlink in default.target.wants: the user default.target
    # does not stop on logout, so the daemon would stay attached to the headphone
    # after leaving the session.
    stale_link = service_dir / "default.target.wants" / "qcy-h3-control.service"
    if stale_link.is_symlink():
        stale_link.unlink()

    try:
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", "--now", "qcy-h3-control.service")
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
        print(f"Failed to activate the user service: {exc}")
        return 1

    print("GNOME integration installed.")
    print(f"Extension: {extension_dir}")
    print("The BLE service starts at login and the headphone button appears in the top panel when QCY H3 audio is connected.")
    print("If it does not appear immediately, log out and back into the GNOME session (Wayland does not allow restarting the Shell with Alt+F2).")
    print("Diagnostics: systemctl --user status qcy-h3-control.service")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
