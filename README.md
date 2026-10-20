# QCY H3 Control

[![CI](https://github.com/andre-franciosi/qcyh3control/actions/workflows/ci.yml/badge.svg)](https://github.com/andre-franciosi/qcyh3control/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![GNOME Shell 51](https://img.shields.io/badge/GNOME%20Shell-51-4a86cf.svg)](https://www.gnome.org/)

Standalone QCY H3 control on Linux over BLE/JieLi RCSP. It includes its own **GNOME Shell 51 extension** — it does not depend on or integrate with any other extension — with a headphone icon and battery bar in the top panel. Clicking it shows the battery percentage and the ANC controls; the advanced equalizer opens in a separate GNOME preferences window. A user daemon keeps the BLE control channel alive. The Tkinter desktop application remains available only as a diagnostic tool.

The app implements ANC off, transparency, low/medium/high ANC and adaptive, a 10-band equalizer with presets, state/battery reading and BLE reconnection. The A2DP audio connection remains managed by PipeWire/GNOME; control commands use a separate GATT connection. Firmware revisions may vary, so the interface only enables commands after the RCSP handshake and validates the headphone's responses.

## Structure

```text
.
├── profile.example.json
├── pyproject.toml
├── README.md
├── gnome-extension/
│   ├── extension.js
│   ├── metadata.json
│   └── prefs.js
├── scripts/install_gnome.py
├── src/qcyh3control/
│   ├── __init__.py
│   ├── app.py
│   ├── ble_manager.py
│   ├── daemon.py
│   ├── main.py
│   ├── protocol.py
│   └── rcsp.py
└── tests/test_protocol.py
```

## Requirements and installation

- Linux with BlueZ and the system D-Bus running.
- Python 3.10 or newer.
- GNOME Shell 51 to use the panel indicator.
- Tkinter is optional and only required for the diagnostic application.

Clone the repository and enter its directory:

```sh
git clone https://github.com/andre-franciosi/qcyh3control.git
cd qcy-h3-control
```

On Debian/Ubuntu and derivatives, install the system dependencies and set up a virtual environment:

```sh
sudo apt update
sudo apt install python3 python3-venv bluez bluetooth
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

These commands use the virtual environment's Python directly, without activation, and work in Bash/Zsh and Fish. If you prefer to activate it: in Bash/Zsh use `. .venv/bin/activate`; in Fish use `source .venv/bin/activate.fish`.

On Fedora, the equivalent packages are usually `python3`, `python3-pip`, `bluez` and `bluez-tools`. Names may vary by distribution. Tkinter is not required to install or use the GNOME indicator; install `python3-tk` (or `python3-tkinter`) only if you want to launch the diagnostic application.

Start the diagnostic app with `./.venv/bin/qcy-h3-control`. For additional diagnostics, use `./.venv/bin/qcy-h3-control --debug`. You can also run `./.venv/bin/python -m qcyh3control.main`.

## Standalone GNOME Shell 51 extension

The extension is self-contained and works without any other GNOME extension. It adds a button with a headphone symbol and a battery bar to the top panel. The button's menu shows the percentage, the ANC modes and a shortcut to **Advanced equalizer settings**, which opens a separate window. The audio connection remains managed by GNOME's Bluetooth/PipeWire.

### Installing without the GNOME Extensions store

This extension is **not published on [extensions.gnome.org](https://extensions.gnome.org)**. You install it manually (a "sideload" install) with the bundled installer, which copies the files into your user extension folder and sets up the daemon. No store, no `.zip` upload and no root access are required.

1. Clone the repository into a **permanent path** (the daemon runs from here, so do not delete it afterwards) and set up the virtual environment as described above:

   ```sh
   git clone https://github.com/andre-franciosi/qcyh3control.git
   cd qcy-h3-control
   python3 -m venv .venv
   ./.venv/bin/python -m pip install --upgrade pip
   ./.venv/bin/python -m pip install -e ".[dev]"
   ```

2. Run the installer **with the virtual environment's Python**:

   ```sh
   ./.venv/bin/python scripts/install_gnome.py
   ```

3. Log out and back into your GNOME session so the Shell loads the new extension (on Wayland, restarting the Shell with Alt+F2 is not supported).

The installer installs the Python dependencies into the venv, copies the extension and its preferences window to `~/.local/share/gnome-shell/extensions`, creates and enables `qcy-h3-control.service` in the user systemd, and tries to activate the extension. The interface uses APIs already provided by GNOME; no other extension needs to be installed or enabled.

### Verifying the install

```sh
gnome-extensions list | grep qcy-h3-control          # should list the extension
gnome-extensions info qcy-h3-control@andre-franciosi.github.io
systemctl --user status qcy-h3-control.service       # daemon status
```

If the button does not appear, confirm the extension is enabled with `gnome-extensions enable qcy-h3-control@andre-franciosi.github.io` and log out/in again.

To check the daemon: `systemctl --user status qcy-h3-control.service`. Logs: `journalctl --user -u qcy-h3-control.service -f`. To uninstall, disable the extension (`gnome-extensions disable qcy-h3-control@andre-franciosi.github.io`), stop/disable the service (`systemctl --user disable --now qcy-h3-control.service`) and remove the extension folder and the `~/.config/systemd/user/qcy-h3-control.service` file.

The button appears when the QCY H3 is connected over BlueZ audio or the BLE channel. The ANC and EQ controls become active after the BLE handshake. The GNOME extension talks to the daemon over the local session D-Bus; no root permission is required.

Tests: `./.venv/bin/python -m pytest`.

## Finding the headphone's Bluetooth address

The address shown by the interface is the BLE address discovered by BlueZ; on devices with a random/private address it may change. The address of the already-paired audio profile is not always identical to the BLE advertisement the app finds.

1. Turn on the QCY H3, keep it nearby and, if necessary, put it in pairing mode.
2. Check that the radio is on: `rfkill list bluetooth` and `bluetoothctl show`. If it is blocked, unblock it with `sudo rfkill unblock bluetooth`.
3. Check the service: `systemctl status bluetooth`. If it is inactive, start it with `sudo systemctl enable --now bluetooth`.
4. Open `bluetoothctl` and run `power on`, then `scan on`. Wait for the advertisements and look for the QCY H3 name; the device line shows the BLE MAC. Stop the scan with `scan off` and exit with `quit`.
5. Alternatively, open the desktop Bluetooth menu and check nearby/paired devices. From the terminal, `bluetoothctl devices` lists known devices and `bluetoothctl devices Paired` lists paired ones; `bluetoothctl info AA:BB:CC:DD:EE:FF` shows details for a known MAC.
6. On startup, the app queries BlueZ over D-Bus — the same source GNOME uses — and lists the QCY H3 if it is already connected for audio, even without a new BLE advertisement. The classic A2DP address (usually `QCY H3`) is different from the control endpoint (`QCY H3-APP`). If that endpoint already exists in BlueZ, the app connects to it directly, without waiting for a scan. It only looks for advertisements if the BLE channel is not yet known. The recognized BLE address is saved to `~/.config/qcyh3control/device.json`.

The headphone may appear connected in GNOME Bluetooth over A2DP. The app recognizes that connection and does not drop it. ANC control uses a separate GATT/BLE connection.

## BlueZ, D-Bus and permissions

1. Confirm that BlueZ is installed and running (`systemctl status bluetooth`) and that the adapter is on (`bluetoothctl show`, `rfkill list`). Bleak uses the BlueZ backend on Linux, accessing the Bluetooth objects over the system D-Bus.
2. Run the app as your normal user inside the graphical session. On a standard desktop installation, the D-Bus service and BlueZ rules already mediate access; do **not** run the GUI with `sudo` as a permission workaround.
3. Do not use `chmod 777` on the D-Bus socket, in `/var/run`, or change device permissions in `/dev`. This does not configure BlueZ correctly and reduces system security.
4. On a server/headless or custom distribution, check that the system bus is running and that the local D-Bus rules/policies authorize the session user to interact with `org.bluez`. Check the logs with `journalctl -u bluetooth -b` and `journalctl -b | grep -i dbus`; adjust the distribution policy instead of opening the socket globally.
5. Some old/custom distributions require membership in a `bluetooth` group. Check first with `getent group bluetooth`; only if the distribution documentation requires it, use `sudo usermod -aG bluetooth "$USER"` and log out/in. The existence of the group alone does not mean it is required.
6. If the BLE connection requires pairing, do it through the system/BlueZ Bluetooth interface and accept the authorization dialog. The app only unlocks the H3's RCSP controls after receiving the handshake response.

## ANC control, equalizer and GATT inspection

1. The app and the daemon try to connect automatically to the control endpoint as soon as the H3 appears in BlueZ. The program subscribes to the notification characteristic and sends the handshake.
2. The ANC and EQ commands use RCSP on characteristic `00000001-0000-1000-8000-00805f9b34fb`, using write-without-response and waiting for a response on `00000002-0000-1000-8000-00805f9b34fb`.
3. The available modes are ANC off, transparency, low/medium/high ANC and adaptive. The EQ has 10 bands between 55 Hz and 22 kHz, a range of −12 to +12 dB and presets. Changes are confirmed by the RCSP status before updating the displayed state.
4. On this device, the service seen over GATT is `0000a002-0000-1000-8000-00805f9b34fb`. The GATT tree remains available to verify whether different firmware/revisions advertise the same UUIDs and properties.
5. **Enable notifications** and **Read selected** remain available for inspection. The JSON profile is an optional generic mechanism for other devices; it is not required for the H3's built-in RCSP modes.

### Optional JSON profile for other devices

The built-in RCSP support does not read ANC commands from a file. The profile is only for testing another known protocol. **Illustrative and non-functional** example:

```json
{
  "enabled": true,
  "service_uuid": "<real service UUID>",
  "write_characteristic_uuid": "<real write characteristic UUID>",
  "write_type": "with_response",
  "modes": {
    "normal": "<real hex bytes>",
    "anc": "<real hex bytes>",
    "transparency": "<real hex bytes>"
  },
  "notify_characteristics": []
}
```

The placeholders do not pass validation. Do not use guessed payloads.

### Protocol reference

The RCSP framing and the ANC commands were implemented based on the public technical documentation of [QCY H3 Controller — Windows Edition](https://github.com/GMarzochi/QCY_H3_WIndowsEdition), which documents the JieLi RCSP service and the H3 handshake. This project has its own implementation and references the protocol documentation; it does not incorporate that application's source code.

## Common issues

- **No device listed:** check power, proximity, BLE advertisement, adapter and `rfkill` blocking; pairing mode may be required to make the headphone discoverable.
- **QCY H3 appears connected in GNOME, but the app does not list it:** the app reads BlueZ over D-Bus, not the advertisement list. Confirm with `bluetoothctl info <MAC>` that `Connected: yes`. The name must be QCY H3 (or QCY H3-APP). If audio is connected and the BLE channel does not open, the status bar keeps indicating the system connection; A2DP is not disconnected.
- **QCY H3 appears connected for audio, but BLE control does not open:** A2DP and GATT use different addresses. Close the QCY app on another phone/PC that may be using BLE, keep the headphone on and click **Scan BLE** again.
- **Connection failure/device disappears:** turn the headphone off and on, bring it closer, avoid a concurrent connection from the phone and scan again. BLE devices may limit to one central connection at a time.
- **Permission/BlueZ error:** see the D-Bus instructions above and the service logs; do not run as root or open permissions globally.
- **Characteristics not readable or commands rejected:** the GATT property, authentication, encryption or format may not be compatible. GATT discovery alone does not confirm the proprietary format.
- **Mode buttons disabled:** check whether "JieLi RCSP handshake completed" appears. If not, confirm that the headphone is on, nearby and advertising BLE; see the reason in the status bar and the Activity panel. A2DP may remain connected.
- **Command with no response:** reconnect, confirm that the headphone is not using the BLE channel through another application and check whether the RCSP characteristics are still advertised in GATT.

## Privacy

QCY H3 Control runs entirely on your machine. It has **no telemetry, no
analytics and makes no network requests** — it only talks to the local BlueZ
stack over D-Bus and to the headphone over Bluetooth. The only file written to
disk is `~/.config/qcyh3control/device.json`, which stores your headphone's
Bluetooth address for automatic reconnection. See [SECURITY.md](SECURITY.md)
for details.

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
