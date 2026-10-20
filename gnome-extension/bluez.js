// Minimal BlueZ client for the QCY H3, implemented directly over D-Bus.
//
// This replaces the Python daemon: the extension talks to the system bus
// (org.bluez) itself, connects to the headphone's BLE control endpoint, opens
// the RCSP GATT channel and exchanges commands. No external process is needed.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {
    ANC_MODES,
    ADV_BATTERY,
    EQ_BAND_FREQS,
    GET_ANC_PAYLOAD,
    GET_EQ_PAYLOAD,
    OP_CUSTOM,
    OP_GET_ADV_INFO,
    OP_NOTIFY_COMMUNICATION_WAY,
    decodeAncMode,
    decodeBattery,
    decodeEqResponse,
    decodePacket,
    encodeCommand,
    encodeEqCommand,
} from './rcsp.js';

const BLUEZ = 'org.bluez';
const OM_IFACE = 'org.freedesktop.DBus.ObjectManager';
const PROPS_IFACE = 'org.freedesktop.DBus.Properties';
const DEVICE_IFACE = 'org.bluez.Device1';
const GATT_SERVICE_IFACE = 'org.bluez.GattService1';
const GATT_CHAR_IFACE = 'org.bluez.GattCharacteristic1';

const RCSP_SERVICE_UUID = '0000a002-0000-1000-8000-00805f9b34fb';
const RCSP_WRITE_UUID = '00000001-0000-1000-8000-00805f9b34fb';
const RCSP_NOTIFY_UUID = '00000002-0000-1000-8000-00805f9b34fb';

const CLASSIC_AUDIO_UUIDS = [
    '0000110a-0000-1000-8000-00805f9b34fb',
    '0000110b-0000-1000-8000-00805f9b34fb',
    '0000111e-0000-1000-8000-00805f9b34fb',
];

const QCY_H3_NAME = /^qcy[\s_-]*h3(?:[\s_-]*(?:anc|le|ble|app))?(?:[\s_-]*headphones)?$/i;
const BLE_NAME_HINT = /(?:app|le|ble)/i;

export function isQcyH3Name(name) {
    return QCY_H3_NAME.test((name ?? '').trim());
}

function classify(name, uuids, icon) {
    if (!isQcyH3Name(name))
        return null;
    const lowered = new Set((uuids ?? []).map(u => u.toLowerCase()));
    if (lowered.has(RCSP_SERVICE_UUID) || BLE_NAME_HINT.test(name))
        return 'ble';
    if (icon === 'audio-headset' || CLASSIC_AUDIO_UUIDS.some(u => lowered.has(u)))
        return 'audio';
    return 'audio';
}

// Method replies are tuples; return the first (only) child fully unpacked into
// plain JS values (strings, booleans, arrays, Uint8Array, nested objects).
function unpackReply(reply) {
    return reply ? reply.get_child_value(0).recursiveUnpack() : undefined;
}

const ADAPTER_IFACE = 'org.bluez.Adapter1';
const DISCOVERY_WINDOW_MS = 25000;
const DISCOVERY_COOLDOWN_MS = 15000;

/**
 * Manages the BlueZ connection and the RCSP channel for one QCY H3.
 * Emits state through the callbacks passed to the constructor.
 */
export class BluezController {
    constructor({onState, onLog, onError} = {}) {
        this._onState = onState ?? (() => {});
        this._onLog = onLog ?? (() => {});
        this._onError = onError ?? (() => {});

        this._bus = null;
        this._devicePath = null;
        this._deviceProxy = null;
        this._writeChar = null;
        this._notifyChar = null;
        this._notifySignalId = 0;
        this._deviceSignalId = 0;

        this._rxBuffer = [];
        this._sequence = 0;
        this._pending = new Map();
        this._ready = false;
        this._connected = false;
        this._audioConnected = false;
        this._battery = -1;
        this._mode = '';
        this._eq = new Array(EQ_BAND_FREQS.length).fill(0);
        this._status = 'Waiting for the QCY H3';
        this._deviceName = 'QCY H3';
        this._cancellable = new Gio.Cancellable();
        this._busy = false;
        this._discoveryAdapter = null;
        this._discoveryStarted = 0;
        this._discoveryStopped = 0;
    }

    get state() {
        return {
            connected: this._connected,
            audioConnected: this._audioConnected,
            protocolReady: this._ready,
            battery: this._battery,
            mode: this._mode,
            equalizer: this._eq,
            status: this._status,
            deviceName: this._deviceName,
        };
    }

    _emitState() {
        this._onState(this.state);
    }

    _setStatus(text) {
        this._status = text;
        this._emitState();
    }

    _ensureBus() {
        if (this._bus)
            return this._bus;
        this._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, this._cancellable);
        return this._bus;
    }

    _call(bus, destination, path, iface, member, parameters = null, replyType = null) {
        return new Promise((resolve, reject) => {
            bus.call(
                destination, path, iface, member, parameters, replyType,
                Gio.DBusCallFlags.NONE, 15000, this._cancellable,
                (source, result) => {
                    try {
                        resolve(source.call_finish(result));
                    } catch (error) {
                        reject(error);
                    }
                });
        });
    }

    async _getManagedObjects() {
        const bus = await this._ensureBus();
        const reply = await this._call(
            bus, BLUEZ, '/', OM_IFACE, 'GetManagedObjects', null,
            new GLib.VariantType('(a{oa{sa{sv}}})'));
        return unpackReply(reply) ?? {};
    }

    /**
     * The BLE control endpoint ("QCY H3-APP") is only known to BlueZ while it
     * is being scanned for, so run a short discovery when it is missing.
     */
    async _startDiscovery(adapterPath) {
        const now = GLib.get_monotonic_time() / 1000;
        if (this._discoveryAdapter) {
            if (now - this._discoveryStarted > DISCOVERY_WINDOW_MS)
                await this._stopDiscovery();
            return;
        }
        if (this._discoveryStopped && now - this._discoveryStopped < DISCOVERY_COOLDOWN_MS)
            return;
        try {
            const bus = await this._ensureBus();
            await this._call(bus, BLUEZ, adapterPath, ADAPTER_IFACE, 'StartDiscovery');
            this._discoveryAdapter = adapterPath;
            this._discoveryStarted = now;
            this._onLog(`Scanning for the BLE control endpoint on ${adapterPath}.`);
        } catch (error) {
            this._onLog(`StartDiscovery failed: ${error.message}`);
            this._discoveryStopped = now;
        }
    }

    async _stopDiscovery() {
        const adapter = this._discoveryAdapter;
        if (!adapter)
            return;
        this._discoveryAdapter = null;
        this._discoveryStopped = GLib.get_monotonic_time() / 1000;
        try {
            const bus = await this._ensureBus();
            await this._call(bus, BLUEZ, adapter, ADAPTER_IFACE, 'StopDiscovery');
        } catch (error) {
            this._onLog(`StopDiscovery failed: ${error.message}`);
        }
    }

    /** Scan BlueZ for QCY H3 devices, separating audio from the BLE endpoint. */
    async _findDevices() {
        const objects = await this._getManagedObjects();
        const audio = [];
        const ble = [];
        let adapterPath = null;
        for (const [path, interfaces] of Object.entries(objects)) {
            if (interfaces[ADAPTER_IFACE] && !adapterPath)
                adapterPath = path;
            const props = interfaces[DEVICE_IFACE];
            if (!props)
                continue;
            const name = props.Name ?? '';
            const alias = props.Alias ?? name;
            const uuids = props.UUIDs ?? [];
            const icon = props.Icon ?? '';
            const kind = classify(name, uuids, icon) ?? classify(alias, uuids, icon);
            if (!kind)
                continue;
            const entry = {
                path,
                address: props.Address ?? '',
                name: alias || name,
                connected: Boolean(props.Connected),
                uuids,
                kind,
            };
            if (kind === 'ble')
                ble.push(entry);
            else
                audio.push(entry);
        }
        ble.sort((a, b) => Number(b.connected) - Number(a.connected));
        return {audio, ble, adapterPath};
    }

    /** Refresh audio presence and try to open the BLE control channel. */
    async refresh() {
        if (this._busy)
            return;
        this._busy = true;
        try {
            await this._refresh();
        } finally {
            this._busy = false;
        }
    }

    async _refresh() {
        try {
            const {audio, ble, adapterPath} = await this._findDevices();
            const audioDevice = audio.find(d => d.connected) ?? null;
            const audioConnected = audioDevice !== null;
            if (audioConnected !== this._audioConnected) {
                this._audioConnected = audioConnected;
                this._emitState();
            }
            if (audioDevice)
                this._deviceName = audioDevice.name;

            // Audio dropped in GNOME: release the BLE control channel so the
            // button disappears and the headphone is not kept attached to us.
            if (this._connected && !audioConnected) {
                this._onLog('System audio disconnected; closing the BLE control channel.');
                await this._disconnectBle();
                this._setStatus('Waiting for the QCY H3');
                return;
            }
            if (this._connected) {
                await this._stopDiscovery();
                await this._readBattery();
                return;
            }
            // BlueZ keeps the BLE endpoint cached after disconnecting, so only
            // open it while the headphone is connected for audio.
            if (ble.length && audioConnected) {
                await this._stopDiscovery();
                await this._connectDevice(ble[0]);
                return;
            }
            if (audioConnected) {
                this._setStatus('Audio connected · searching for the control channel…');
                const adapter = adapterPath ?? audioDevice.path.replace(/\/dev_[^/]+$/, '');
                await this._startDiscovery(adapter);
            } else {
                await this._stopDiscovery();
                this._setStatus('Waiting for the QCY H3');
            }
        } catch (error) {
            this._onLog(`BlueZ query failed: ${error.message}`);
        }
    }

    async _connectDevice(device) {
        try {
            this._setStatus(`Connecting to ${device.address}…`);
            const bus = await this._ensureBus();
            this._devicePath = device.path;

            // Connect the BLE endpoint if BlueZ has not done it yet.
            const props = await this._getDeviceProps(device.path);
            if (!props.Connected) {
                await this._call(bus, BLUEZ, device.path, DEVICE_IFACE, 'Connect');
            }
            await this._waitServicesResolved(device.path);

            this._deviceProxy = new Gio.DBusProxy({
                g_connection: bus,
                g_name: BLUEZ,
                g_object_path: device.path,
                g_interface_name: DEVICE_IFACE,
            });
            await new Promise((resolve, reject) => {
                this._deviceProxy.init_async(GLib.PRIORITY_DEFAULT, this._cancellable,
                    (source, result) => {
                        try {
                            source.init_finish(result);
                            resolve();
                        } catch (error) {
                            reject(error);
                        }
                    });
            });
            this._deviceSignalId = this._deviceProxy.connect('g-properties-changed',
                () => this._onDevicePropsChanged());

            this._connected = true;
            this._deviceName = device.name;
            this._setStatus('BLE connected · starting RCSP…');
            this._emitState();

            await this._openRcsp();
        } catch (error) {
            this._onLog(`BLE connection failed: ${error.message}`);
            this._connected = false;
            this._ready = false;
            this._setStatus('Could not open the BLE control channel');
        }
    }

    async _getDeviceProps(path) {
        const bus = await this._ensureBus();
        const reply = await this._call(
            bus, BLUEZ, path, PROPS_IFACE, 'GetAll',
            new GLib.Variant('(s)', [DEVICE_IFACE]),
            new GLib.VariantType('(a{sv})'));
        return unpackReply(reply) ?? {};
    }

    async _waitServicesResolved(path, attempts = 20) {
        for (let i = 0; i < attempts; i++) {
            const props = await this._getDeviceProps(path);
            if (props.ServicesResolved)
                return true;
            await new Promise(resolve => {
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
                    resolve();
                    return GLib.SOURCE_REMOVE;
                });
            });
        }
        return false;
    }

    _onDevicePropsChanged() {
        const connected = this._deviceProxy?.get_cached_property('Connected')?.deep_unpack();
        if (connected === false && this._connected) {
            this._onLog('Device disconnected.');
            this._teardown();
            this._setStatus('Connection lost. Reconnecting…');
        }
    }

    async _openRcsp() {
        const objects = await this._getManagedObjects();
        let servicePath = null;
        for (const [path, interfaces] of Object.entries(objects)) {
            const svc = interfaces[GATT_SERVICE_IFACE];
            if (svc && (svc.UUID ?? '').toLowerCase() === RCSP_SERVICE_UUID) {
                servicePath = path;
                break;
            }
        }
        if (!servicePath) {
            this._setStatus('RCSP service not found on the QCY H3');
            return;
        }

        let writePath = null;
        let notifyPath = null;
        for (const [path, interfaces] of Object.entries(objects)) {
            const chr = interfaces[GATT_CHAR_IFACE];
            if (!chr || chr.Service !== servicePath)
                continue;
            const uuid = (chr.UUID ?? '').toLowerCase();
            if (uuid === RCSP_WRITE_UUID)
                writePath = path;
            else if (uuid === RCSP_NOTIFY_UUID)
                notifyPath = path;
        }
        if (!writePath || !notifyPath) {
            this._setStatus('RCSP characteristics not available');
            return;
        }

        this._writeChar = writePath;
        this._notifyChar = notifyPath;
        this._rxBuffer = [];

        const bus = await this._ensureBus();
        this._notifySignalId = bus.signal_subscribe(
            BLUEZ, PROPS_IFACE, 'PropertiesChanged', notifyPath, null,
            Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, _signal, params) => {
                const [iface, changed] = params.recursiveUnpack();
                if (iface !== GATT_CHAR_IFACE)
                    return;
                if (changed.Value)
                    this._onNotification(changed.Value);
            });

        await this._call(bus, BLUEZ, notifyPath, GATT_CHAR_IFACE, 'StartNotify');

        const response = await this._sendRcsp(OP_NOTIFY_COMMUNICATION_WAY, [0x00, 0x00], 3000);
        if (!response) {
            this._setStatus('RCSP handshake timed out');
            return;
        }
        if (response.status !== 0) {
            this._setStatus(`RCSP handshake refused (status 0x${response.status.toString(16)})`);
            return;
        }
        this._ready = true;
        this._onLog('JieLi RCSP handshake completed; ANC controls enabled.');
        this._setStatus('Connected');
        await this._refreshState();
    }

    _onNotification(value) {
        const bytes = value instanceof Uint8Array ? value : Uint8Array.from(value);
        for (const byte of bytes)
            this._rxBuffer.push(byte);

        while (true) {
            const headerIndex = this._indexOfHeader();
            if (headerIndex < 0) {
                if (this._rxBuffer.length > 2)
                    this._rxBuffer = this._rxBuffer.slice(-2);
                return;
            }
            if (headerIndex > 0)
                this._rxBuffer = this._rxBuffer.slice(headerIndex);
            if (this._rxBuffer.length < 7)
                return;
            const bodyLength = (this._rxBuffer[5] << 8) | this._rxBuffer[6];
            const frameLength = 7 + bodyLength + 1;
            if (this._rxBuffer.length < frameLength)
                return;
            const frame = Uint8Array.from(this._rxBuffer.slice(0, frameLength));
            if (frame[frame.length - 1] !== 0xef) {
                this._rxBuffer.shift();
                continue;
            }
            this._rxBuffer = this._rxBuffer.slice(frameLength);
            const packet = decodePacket(frame);
            if (!packet)
                continue;
            if (packet.isResponse) {
                const pending = this._pending.get(packet.sequence);
                if (pending) {
                    this._pending.delete(packet.sequence);
                    pending.resolve(packet);
                }
            }
        }
    }

    _indexOfHeader() {
        for (let i = 0; i + 2 < this._rxBuffer.length; i++) {
            if (this._rxBuffer[i] === 0xfe && this._rxBuffer[i + 1] === 0xdc && this._rxBuffer[i + 2] === 0xba)
                return i;
        }
        return -1;
    }

    async _sendRcsp(opCode, params, timeout = 2500) {
        if (!this._writeChar)
            throw new Error('RCSP channel is not connected.');
        this._sequence = (this._sequence + 1) & 0xff;
        if (this._sequence === 0)
            this._sequence = 1;
        const sequence = this._sequence;
        const frame = encodeCommand(opCode, sequence, params, true);

        const promise = new Promise(resolve => {
            const timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, timeout, () => {
                this._pending.delete(sequence);
                resolve(null);
                return GLib.SOURCE_REMOVE;
            });
            this._pending.set(sequence, {
                resolve: packet => {
                    GLib.source_remove(timer);
                    resolve(packet);
                },
            });
        });

        const bus = await this._ensureBus();
        await this._call(
            bus, BLUEZ, this._writeChar, GATT_CHAR_IFACE, 'WriteValue',
            new GLib.Variant('(aya{sv})', [frame, {type: new GLib.Variant('s', 'command')}]));
        return promise;
    }

    async _refreshState() {
        if (!this._ready)
            return;
        try {
            const anc = await this._sendRcsp(OP_CUSTOM, GET_ANC_PAYLOAD, 3000);
            if (anc && anc.status === 0) {
                const mode = decodeAncMode(anc.params);
                if (mode) {
                    this._mode = mode;
                    this._emitState();
                }
            }
        } catch (error) {
            this._onLog(`ANC read failed: ${error.message}`);
        }
        try {
            const eq = await this._sendRcsp(OP_CUSTOM, GET_EQ_PAYLOAD, 3000);
            if (eq && eq.status === 0) {
                const offsets = decodeEqResponse(eq.params);
                if (offsets) {
                    this._eq = offsets;
                    this._emitState();
                }
            }
        } catch (error) {
            this._onLog(`EQ read failed: ${error.message}`);
        }
        await this._readBattery();
    }

    async _readBattery() {
        if (!this._ready)
            return;
        try {
            // 4-byte big-endian bitmask; bit 0 requests the battery level.
            const mask = [0, 0, 0, (1 << ADV_BATTERY) & 0xff];
            const response = await this._sendRcsp(OP_GET_ADV_INFO, mask, 3000);
            if (response && response.status === 0) {
                const battery = decodeBattery(response.params);
                if (battery !== null) {
                    this._battery = battery;
                    this._emitState();
                }
            }
        } catch (error) {
            this._onLog(`Battery read failed: ${error.message}`);
        }
    }

    async setMode(mode) {
        const payload = ANC_MODES[mode];
        if (!payload) {
            this._onError(`Unknown ANC mode: ${mode}`);
            return;
        }
        if (!this._ready) {
            this._onError('ANC control is locked: RCSP handshake not completed.');
            return;
        }
        try {
            const response = await this._sendRcsp(OP_CUSTOM, payload, 3000);
            if (!response)
                throw new Error('the headphone did not confirm the mode change');
            if (response.status !== 0)
                throw new Error(`command refused (status 0x${response.status.toString(16)})`);
            this._mode = mode === 'anc' ? 'anc_high' : mode;
            this._emitState();
        } catch (error) {
            this._onError(`Could not change the mode: ${error.message}`);
        }
    }

    async setEqualizer(offsets, presetId = 0x00) {
        if (!this._ready) {
            this._onError('Equalizer is locked: RCSP handshake not completed.');
            return;
        }
        if (offsets.length !== EQ_BAND_FREQS.length) {
            this._onError(`EQ needs ${EQ_BAND_FREQS.length} bands.`);
            return;
        }
        try {
            const payload = encodeEqCommand(offsets, presetId);
            const response = await this._sendRcsp(OP_CUSTOM, payload, 3000);
            if (!response)
                throw new Error('the headphone did not confirm the equalizer');
            if (response.status !== 0)
                throw new Error(`EQ refused (status 0x${response.status.toString(16)})`);
            this._eq = offsets.map(v => Math.max(-1200, Math.min(1200, Math.trunc(v))));
            this._emitState();
        } catch (error) {
            this._onError(`Could not apply the EQ: ${error.message}`);
        }
    }

    async _disconnectBle() {
        const path = this._devicePath;
        if (path) {
            try {
                const bus = await this._ensureBus();
                await this._call(bus, BLUEZ, path, DEVICE_IFACE, 'Disconnect');
            } catch (error) {
                this._onLog(`BLE disconnect failed: ${error.message}`);
            }
        }
        this._teardown();
    }

    _teardown() {
        if (this._notifySignalId && this._bus) {
            this._bus.signal_unsubscribe(this._notifySignalId);
            this._notifySignalId = 0;
        }
        if (this._deviceSignalId && this._deviceProxy) {
            this._deviceProxy.disconnect(this._deviceSignalId);
            this._deviceSignalId = 0;
        }
        for (const pending of this._pending.values())
            pending.resolve(null);
        this._pending.clear();
        this._writeChar = null;
        this._notifyChar = null;
        this._deviceProxy = null;
        this._devicePath = null;
        this._rxBuffer = [];
        this._ready = false;
        this._connected = false;
        this._battery = -1;
        this._mode = '';
        this._emitState();
    }

    destroy() {
        if (this._discoveryAdapter && this._bus) {
            try {
                this._bus.call_sync(BLUEZ, this._discoveryAdapter, ADAPTER_IFACE,
                    'StopDiscovery', null, null, Gio.DBusCallFlags.NONE, 2000, null);
            } catch (error) {
                this._onLog(`StopDiscovery failed: ${error.message}`);
            }
            this._discoveryAdapter = null;
        }
        this._cancellable.cancel();
        this._teardown();
        this._bus = null;
    }
}
