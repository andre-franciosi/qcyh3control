# QCY H3 Control

[![CI](https://github.com/andre-franciosi/qcyh3control/actions/workflows/ci.yml/badge.svg)](https://github.com/andre-franciosi/qcyh3control/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GNOME Shell 51](https://img.shields.io/badge/GNOME%20Shell-51-4a86cf.svg)](https://www.gnome.org/)

Standalone QCY H3 control on Linux over BLE/JieLi RCSP. It ships as a **self-contained GNOME Shell 51 extension** — it does not depend on any other extension, on a background daemon or on Python — with a headphone icon and battery bar in the top panel. Clicking it shows the battery percentage and the ANC controls; the advanced equalizer opens in a separate GNOME preferences window. The extension talks to BlueZ directly over D-Bus and speaks the RCSP protocol itself.

The extension implements ANC off, transparency, low/medium/high ANC and adaptive, a 10-band equalizer with presets, state/battery reading and automatic reconnection. The A2DP audio connection remains managed by PipeWire/GNOME; control commands use a separate GATT connection. Firmware revisions may vary, so the interface only enables commands after the RCSP handshake and validates the headphone's responses.

A Tkinter desktop application is also included, but only as an optional diagnostic tool.

## Structure

```text
.
├── gnome-extension/
│   ├── extension.js      # panel button, menu and lifecycle
│   ├── bluez.js          # BlueZ D-Bus client + RCSP channel
│   ├── rcsp.js           # JieLi RCSP protocol (framing, ANC, EQ)
│   ├── prefs.js          # advanced equalizer window
│   └── metadata.json
├── scripts/
│   ├── install_gnome.py  # installs the extension
│   └── package_extension.py
├── src/qcyh3control/     # optional Python diagnostic app
├── tests/test_protocol.py
├── pyproject.toml
└── README.md
```

## Requirements

- Linux with BlueZ and the system D-Bus running.
- GNOME Shell 51 for the panel indicator.
- No Python, no daemon and no root access are required for the extension.

## Standalone GNOME Shell 51 extension

The extension is self-contained and works without any other GNOME extension. It adds a button with a headphone symbol and a battery bar to the top panel. The button's menu shows the percentage, the ANC modes and a shortcut to **Advanced equalizer settings**, which opens a separate window. The audio connection remains managed by GNOME's Bluetooth/PipeWire.

### Installing the extension

The extension is installed manually (a "sideload" install) with the bundled installer, which copies the files into your user extension folder. No store, no `.zip` upload and no root access are required.

1. Clone the repository:

   ```sh
   git clone https://github.com/andre-franciosi/qcyh3control.git
   cd qcyh3control
   ```

2. Run the installer:

   ```sh
   python3 scripts/install_gnome.py
   ```

3. Log out and back into your GNOME session so the Shell loads the new extension (on Wayland, restarting the Shell with Alt+F2 is not supported).

The installer copies the extension files to `~/.local/share/gnome-shell/extensions` and enables the extension. The interface uses APIs already provided by GNOME; no other extension needs to be installed or enabled.

### Verifying the install

```sh
gnome-extensions list | grep qcy-h3-control          # should list the extension
gnome-extensions info qcy-h3-control@andre-franciosi.github.io
```

If the button does not appear, confirm the extension is enabled with `gnome-extensions enable qcy-h3-control@andre-franciosi.github.io` and log out/in again.

To uninstall, disable the extension (`gnome-extensions disable qcy-h3-control@andre-franciosi.github.io`) and remove the `~/.local/share/gnome-shell/extensions/qcy-h3-control@andre-franciosi.github.io` folder.

The button appears when the QCY H3 is connected over BlueZ audio or the BLE channel. The ANC and EQ controls become active after the BLE handshake. The extension talks to BlueZ over the system D-Bus; no root permission is required.

## Optional Python diagnostic app

The Tkinter application is only a diagnostic tool; the GNOME extension does not need it. To run it, set up a virtual environment:

```sh
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

These commands use the virtual environment's Python directly, without activation, and work in Bash/Zsh and Fish. If you prefer to activate it: in Bash/Zsh use `. .venv/bin/activate`; in Fish use `source .venv/bin/activate.fish`.

On Debian/Ubuntu install `python3 python3-venv bluez bluetooth`; on Fedora the equivalent packages are usually `python3`, `python3-pip`, `bluez` and `bluez-tools`. Install `python3-tk` (or `python3-tkinter`) only if you want to launch the diagnostic application.

Start it with `./.venv/bin/qcy-h3-control` (add `--debug` for verbose logs), or run `./.venv/bin/python -m qcyh3control.main`.

Tests: `./.venv/bin/python -m pytest`.

## Pairing the headphone

The extension reads the device list from BlueZ over D-Bus — the same source GNOME uses — so it finds the QCY H3 as soon as it is paired and connected, even without a new BLE advertisement. The classic A2DP endpoint (usually `QCY H3`) is different from the control endpoint (`QCY H3-APP`); the extension uses the latter for ANC and EQ.

1. Turn on the QCY H3, keep it nearby and, if necessary, put it in pairing mode.
2. Check that the radio is on: `rfkill list bluetooth` and `bluetoothctl show`. If it is blocked, unblock it with `sudo rfkill unblock bluetooth`.
3. Check the service: `systemctl status bluetooth`. If it is inactive, start it with `sudo systemctl enable --now bluetooth`.
4. Pair the headphone through the GNOME Bluetooth menu (or `bluetoothctl`: `power on`, `scan on`, `pair <MAC>`, `connect <MAC>`). Accept the authorization dialog if one appears.
5. Confirm the connection with `bluetoothctl info AA:BB:CC:DD:EE:FF` (`Connected: yes`).

The headphone may appear connected in GNOME Bluetooth over A2DP. The extension recognizes that connection and does not drop it. ANC control uses a separate GATT/BLE connection.

## BlueZ, D-Bus and permissions

1. Confirm that BlueZ is installed and running (`systemctl status bluetooth`) and that the adapter is on (`bluetoothctl show`, `rfkill list`). The extension accesses the Bluetooth objects over the system D-Bus.
2. Run GNOME as your normal user inside the graphical session. On a standard desktop installation, the D-Bus service and BlueZ rules already mediate access; do **not** run anything with `sudo` as a permission workaround.
3. Do not use `chmod 777` on the D-Bus socket, in `/var/run`, or change device permissions in `/dev`. This does not configure BlueZ correctly and reduces system security.
4. On a server/headless or custom distribution, check that the system bus is running and that the local D-Bus rules/policies authorize the session user to interact with `org.bluez`. Check the logs with `journalctl -u bluetooth -b` and `journalctl -b | grep -i dbus`; adjust the distribution policy instead of opening the socket globally.
5. Some old/custom distributions require membership in a `bluetooth` group. Check first with `getent group bluetooth`; only if the distribution documentation requires it, use `sudo usermod -aG bluetooth "$USER"` and log out/in. The existence of the group alone does not mean it is required.
6. If the BLE connection requires pairing, do it through the system/BlueZ Bluetooth interface and accept the authorization dialog. The extension only unlocks the H3's RCSP controls after receiving the handshake response.

## ANC control and equalizer

1. The extension connects automatically to the control endpoint as soon as the H3 appears in BlueZ. It subscribes to the notification characteristic and sends the handshake.
2. The ANC and EQ commands use RCSP on characteristic `00000001-0000-1000-8000-00805f9b34fb`, using write-without-response and waiting for a response on `00000002-0000-1000-8000-00805f9b34fb`.
3. The available modes are ANC off, transparency, low/medium/high ANC and adaptive. The EQ has 10 bands between 55 Hz and 22 kHz, a range of −12 to +12 dB and presets. Changes are confirmed by the RCSP status before updating the displayed state.
4. The RCSP service is `0000a002-0000-1000-8000-00805f9b34fb`. Different firmware revisions may advertise different UUIDs or properties.

### Protocol reference

The RCSP framing and the ANC commands were implemented based on the public technical documentation of [QCY H3 Controller — Windows Edition](https://github.com/GMarzochi/QCY_H3_WIndowsEdition), which documents the JieLi RCSP service and the H3 handshake. This project has its own implementation and references the protocol documentation; it does not incorporate that application's source code.

## Common issues

- **No button in the panel:** the button only appears when the QCY H3 is connected over BlueZ audio or the BLE channel. Pair and connect the headphone first, then check `bluetoothctl info <MAC>` for `Connected: yes`.
- **QCY H3 appears connected in GNOME, but the controls stay disabled:** the extension reads BlueZ over D-Bus, not the advertisement list. The name must be QCY H3 (or QCY H3-APP). If audio is connected and the BLE channel does not open, the status keeps indicating the system connection; A2DP is not disconnected.
- **QCY H3 appears connected for audio, but BLE control does not open:** A2DP and GATT use different endpoints. Close the QCY app on another phone/PC that may be using BLE, keep the headphone on and reopen the menu.
- **Connection failure/device disappears:** turn the headphone off and on, bring it closer and avoid a concurrent connection from the phone. BLE devices may limit to one central connection at a time.
- **Permission/BlueZ error:** see the D-Bus instructions above and the Bluetooth logs; do not run as root or open permissions globally.
- **Characteristics not readable or commands rejected:** the GATT property, authentication, encryption or format may not be compatible. GATT discovery alone does not confirm the proprietary format.
- **Mode buttons disabled:** the controls unlock only after the RCSP handshake. Confirm that the headphone is on, nearby and connected; A2DP may remain connected.
- **Command with no response:** reopen the menu to reconnect, and confirm that the headphone is not using the BLE channel through another application.

## Privacy

QCY H3 Control runs entirely on your machine. It has **no telemetry, no
analytics and makes no network requests** — it only talks to the local BlueZ
stack over D-Bus and to the headphone over Bluetooth. It does not write any
files to disk. See [SECURITY.md](SECURITY.md) for details.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request.

## License

This project is licensed under the GNU General Public License v3.0 or later —
see the [LICENSE](LICENSE) file for details.

The RCSP framing and ANC commands were implemented from the public technical
documentation of [QCY H3 Controller — Windows Edition](https://github.com/GMarzochi/QCY_H3_WIndowsEdition).
This project has its own implementation and does not incorporate that
application's source code.
