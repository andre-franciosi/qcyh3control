import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {BluezController} from './bluez.js';
import {ANC_ORDER} from './rcsp.js';

const ANC_LABELS = {
    normal: 'Normal',
    transparency: 'Transparency',
    anc_low: 'ANC low',
    anc_med: 'ANC medium',
    anc_high: 'ANC high',
    adaptive: 'Adaptive',
};

// St does not provide a level bar widget (St.LevelBar does not exist in Shell 51),
// so we draw the battery bar with Cairo on top of a St.DrawingArea.
const BatteryBar = GObject.registerClass(
class BatteryBar extends St.DrawingArea {
    _init(params = {}) {
        super._init(params);
        this._value = 0.0;
        this.connect('repaint', () => this._onRepaint());
    }

    setValue(value) {
        const clamped = Math.max(0, Math.min(1, Number(value) || 0));
        if (clamped === this._value)
            return;
        this._value = clamped;
        this.queue_repaint();
    }

    _roundedRect(cr, x, y, width, height, radius) {
        const r = Math.max(0, Math.min(radius, width / 2, height / 2));
        cr.newSubPath();
        cr.arc(x + width - r, y + r, r, -Math.PI / 2, 0);
        cr.arc(x + width - r, y + height - r, r, 0, Math.PI / 2);
        cr.arc(x + r, y + height - r, r, Math.PI / 2, Math.PI);
        cr.arc(x + r, y + r, r, Math.PI, (3 * Math.PI) / 2);
        cr.closePath();
    }

    _onRepaint() {
        const cr = this.get_context();
        const [width, height] = this.get_surface_size();
        if (width <= 0 || height <= 0) {
            cr.$dispose();
            return;
        }

        cr.setSourceRGBA(1, 1, 1, 0.18);
        this._roundedRect(cr, 0, 0, width, height, height / 2);
        cr.fill();

        const fillWidth = width * this._value;
        if (fillWidth > 0.5) {
            let color = [0.30, 0.78, 0.42];
            if (this._value <= 0.2)
                color = [0.90, 0.30, 0.30];
            else if (this._value <= 0.5)
                color = [0.95, 0.70, 0.20];
            cr.setSourceRGBA(color[0], color[1], color[2], 1);
            this._roundedRect(cr, 0, 0, fillWidth, height, height / 2);
            cr.fill();
        }
        cr.$dispose();
    }
});

const QcyPanelButton = GObject.registerClass(
class QcyPanelButton extends PanelMenu.Button {
    constructor(extension, controller) {
        super(0.0, 'QCY H3 Control');

        this._extension = extension;
        this._controller = controller;
        this._modeItems = new Map();
        this._state = controller.state;

        const panelContent = new St.BoxLayout({
            style_class: 'panel-status-menu-box',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._headphoneIcon = new St.Icon({
            icon_name: 'audio-headphones-symbolic',
            style_class: 'system-status-icon',
        });
        this._panelBatteryBar = new BatteryBar({
            style_class: 'qcy-battery-bar',
            width: 30,
            height: 8,
            y_align: Clutter.ActorAlign.CENTER,
        });
        panelContent.add_child(this._headphoneIcon);
        panelContent.add_child(this._panelBatteryBar);
        this.add_child(panelContent);

        this._buildMenu();
        this._menuSignalId = this.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._controller.refresh();
        });
        this._sync();
    }

    _buildMenu() {
        const batteryItem = new PopupMenu.PopupBaseMenuItem({
            activate: false,
            hover: false,
            can_focus: false,
        });
        const batteryContent = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
            style: 'spacing: 6px; padding: 4px 0;',
        });
        const batteryHeader = new St.BoxLayout({x_expand: true});
        batteryHeader.add_child(new St.Label({text: 'Headphone battery', x_expand: true}));
        this._batteryPercent = new St.Label({text: '—'});
        batteryHeader.add_child(this._batteryPercent);
        this._menuBatteryBar = new BatteryBar({
            style_class: 'qcy-battery-bar',
            x_expand: true,
            height: 8,
        });
        this._connectionStatus = new St.Label({
            text: 'Waiting for the QCY H3…',
            style_class: 'dim-label',
        });
        batteryContent.add_child(batteryHeader);
        batteryContent.add_child(this._menuBatteryBar);
        batteryContent.add_child(this._connectionStatus);
        batteryItem.add_child(batteryContent);
        this.menu.addMenuItem(batteryItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Noise cancellation'));
        for (const key of ANC_ORDER) {
            const item = new PopupMenu.PopupMenuItem(ANC_LABELS[key]);
            item.connect('activate', () => this._controller.setMode(key));
            this.menu.addMenuItem(item);
            this._modeItems.set(key, item);
        }

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Equalizer'));
        const equalizerItem = new PopupMenu.PopupMenuItem('Advanced settings…');
        equalizerItem.connect('activate', () => this._extension.openPreferences());
        this.menu.addMenuItem(equalizerItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const refreshItem = new PopupMenu.PopupMenuItem('Refresh headphone state');
        refreshItem.connect('activate', () => this._controller.refresh());
        this.menu.addMenuItem(refreshItem);
        this._connectItem = new PopupMenu.PopupMenuItem('Connect to the QCY H3');
        this._connectItem.connect('activate', () => this._controller.refresh());
        this.menu.addMenuItem(this._connectItem);
    }

    updateState(state) {
        this._state = state;
        this._sync();
    }

    _sync() {
        const {audioConnected, connected, protocolReady, battery, mode, status} = this._state;

        // The button only appears in the panel when the headphone is connected
        // (system audio or BLE control channel); it disappears when it disconnects.
        this.visible = audioConnected || connected;
        this._headphoneIcon.icon_name = 'audio-headphones-symbolic';
        const batteryValue = battery >= 0 ? Math.max(0, Math.min(100, battery)) / 100 : 0;
        this._panelBatteryBar.setValue(batteryValue);
        this._panelBatteryBar.visible = battery >= 0;
        this._menuBatteryBar.setValue(batteryValue);
        this._menuBatteryBar.visible = battery >= 0;
        this._batteryPercent.text = battery >= 0 ? `${battery}%` : 'Unavailable';

        if (connected && protocolReady) {
            const modeName = ANC_LABELS[mode] ?? 'ready';
            this._connectionStatus.text = `Connected · ${modeName}`;
        } else if (connected) {
            this._connectionStatus.text = 'BLE connected · starting RCSP…';
        } else if (audioConnected) {
            this._connectionStatus.text = 'Audio connected · opening controls…';
        } else {
            this._connectionStatus.text = status;
        }

        this._modeItems.forEach((item, key) => {
            item.setOrnament(key === mode ? PopupMenu.Ornament.CHECK : PopupMenu.Ornament.NONE);
            item.reactive = connected && protocolReady;
        });
        this._connectItem.visible = audioConnected && !connected;
        this.accessible_name = battery >= 0
            ? `QCY H3, battery ${battery}%`
            : 'QCY H3';
    }

    destroy() {
        this.menu.disconnect(this._menuSignalId);
        super.destroy();
    }
});

export default class QcyH3Extension extends Extension {
    enable() {
        // The extension talks to BlueZ directly over the system D-Bus; no
        // external daemon is required.
        this._controller = new BluezController({
            onState: state => this._indicator?.updateState(state),
            onLog: message => console.debug(`QCY H3: ${message}`),
            onError: message => Main.notifyError('QCY H3 Control', message),
        });
        this._indicator = new QcyPanelButton(this, this._controller);
        Main.panel.addToStatusArea('qcy-h3-control', this._indicator, 0, 'right');

        this._controller.refresh();
        this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            this._controller.refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        this._indicator?.destroy();
        this._indicator = null;
        this._controller?.destroy();
        this._controller = null;
    }
}