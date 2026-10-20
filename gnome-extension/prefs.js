import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const BUS_NAME = 'io.github.qcyh3control';
const OBJECT_PATH = '/io/github/qcyh3control/Control';
const INTERFACE = 'io.github.qcyh3control.Control';
const FREQUENCIES = [
    '55 Hz', '220 Hz', '500 Hz', '1 kHz', '1.8 kHz',
    '2.8 kHz', '4.5 kHz', '7.5 kHz', '10 kHz', '22 kHz',
];
const PRESETS = [
    ['Custom', 0], ['Default', 1], ['Pop', 2], ['Heavy Bass', 3],
    ['Rock', 4], ['Soft', 5], ['Classic', 6],
];
const PRESET_OFFSETS = {
    0: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    1: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    2: [0, 150, 150, 100, 0, 100, 200, 200, 150, 100],
    3: [500, 400, 200, 0, -100, 0, 0, 100, 200, 100],
    4: [400, 300, 0, -200, -100, 0, 200, 300, 400, 200],
    5: [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
    6: [0, 0, 100, 300, 300, 300, 200, 100, 100, 0],
};

function unpack(proxy, name, fallback) {
    try {
        return proxy.get_cached_property(name)?.deep_unpack() ?? fallback;
    } catch (_error) {
        return fallback;
    }
}

export default class QcyH3Preferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        window.set_title('Advanced equalizer · QCY H3');
        window.set_default_size(760, 680);

        const page = new Adw.PreferencesPage({
            title: 'Equalizer',
            icon_name: 'audio-headphones-symbolic',
        });
        const group = new Adw.PreferencesGroup({
            title: '10-band equalizer',
            description: 'Adjust each band between −12 and +12 dB. Changes are sent to the headphone when applied.',
        });
        page.add(group);
        window.add(page);

        const statusRow = new Adw.ActionRow({
            title: 'Headphone status',
            subtitle: 'Connecting to the QCY H3 service…',
        });
        group.add(statusRow);

        let proxy;
        try {
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                null,
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                null,
            );
        } catch (error) {
            statusRow.set_subtitle(`Could not access the service: ${error.message}`);
            return;
        }

        const presetRow = new Adw.ActionRow({title: 'Preset'});
        const presetDropdown = Gtk.DropDown.new_from_strings(PRESETS.map(([name]) => name));
        presetRow.add_suffix(presetDropdown);
        group.add(presetRow);

        const sliders = [];
        const valueLabels = [];
        const controls = [presetDropdown];
        let offsets = Array(FREQUENCIES.length).fill(0);
        let selectedPresetId = 0;
        let syncing = false;

        const showOffsets = () => {
            syncing = true;
            try {
                sliders.forEach((slider, index) => {
                    const db100 = offsets[index] ?? 0;
                    slider.set_value(db100 / 100);
                    valueLabels[index].set_label(`${(db100 / 100).toFixed(1)} dB`);
                });
            } finally {
                syncing = false;
            }
        };

        const loadEqualizer = () => {
            const current = unpack(proxy, 'Equalizer', []);
            if (Array.isArray(current) && current.length === FREQUENCIES.length) {
                offsets = current.map(Number);
                selectedPresetId = 0;
                syncing = true;
                presetDropdown.set_selected(0);
                syncing = false;
                showOffsets();
            }
        };

        FREQUENCIES.forEach((frequency, index) => {
            const row = new Adw.ActionRow({title: frequency});
            const slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -12, 12, 0.1);
            slider.set_draw_value(false);
            slider.set_hexpand(true);
            slider.set_size_request(340, -1);
            const valueLabel = new Gtk.Label({label: '0.0 dB', width_chars: 7});
            row.add_suffix(slider);
            row.add_suffix(valueLabel);
            row.set_activatable_widget(slider);
            group.add(row);

            sliders.push(slider);
            valueLabels.push(valueLabel);
            controls.push(slider);
            slider.connect('value-changed', () => {
                if (syncing)
                    return;
                offsets[index] = Math.round(slider.get_value() * 100);
                valueLabel.set_label(`${(offsets[index] / 100).toFixed(1)} dB`);
                selectedPresetId = 0;
                syncing = true;
                presetDropdown.set_selected(0);
                syncing = false;
            });
        });

        const buttonRow = new Adw.ActionRow({title: 'Actions'});
        const resetButton = new Gtk.Button({label: 'Reset'});
        const applyButton = new Gtk.Button({label: 'Apply equalizer'});
        applyButton.add_css_class('suggested-action');
        buttonRow.add_suffix(resetButton);
        buttonRow.add_suffix(applyButton);
        group.add(buttonRow);
        controls.push(resetButton, applyButton);

        const isReady = () =>
            proxy.g_name_owner !== null &&
            unpack(proxy, 'Connected', false) &&
            unpack(proxy, 'ProtocolReady', false);

        const updateStatus = () => {
            const connected = unpack(proxy, 'Connected', false);
            const ready = unpack(proxy, 'ProtocolReady', false);
            if (!proxy.g_name_owner)
                statusRow.set_subtitle('Headphone service unavailable. Check that the QCY H3 daemon is running.');
            else if (ready)
                statusRow.set_subtitle('Connected · equalizer ready');
            else if (connected)
                statusRow.set_subtitle('BLE connected · waiting for the RCSP protocol…');
            else
                statusRow.set_subtitle('Waiting for the QCY H3 to connect through GNOME Bluetooth…');

            controls.forEach(control => control.set_sensitive(ready));
        };

        const sendEqualizer = (presetId, successMessage) => {
            if (!isReady()) {
                updateStatus();
                return;
            }
            statusRow.set_subtitle('Sending equalizer…');
            proxy.call(
                'SetEQ',
                new GLib.Variant('(aiy)', [offsets, presetId]),
                Gio.DBusCallFlags.NONE,
                5000,
                null,
                (source, result) => {
                    try {
                        source.call_finish(result);
                        statusRow.set_subtitle(successMessage);
                    } catch (error) {
                        statusRow.set_subtitle(`Failed to apply: ${error.message}`);
                    }
                },
            );
        };

        presetDropdown.connect('notify::selected', () => {
            if (syncing)
                return;
            const preset = PRESETS[presetDropdown.get_selected()];
            if (!preset)
                return;
            selectedPresetId = preset[1];
            if (selectedPresetId !== 0) {
                offsets = [...PRESET_OFFSETS[selectedPresetId]];
                showOffsets();
            }
        });

        resetButton.connect('clicked', () => {
            offsets = Array(FREQUENCIES.length).fill(0);
            selectedPresetId = 0;
            syncing = true;
            presetDropdown.set_selected(0);
            syncing = false;
            showOffsets();
            sendEqualizer(0, 'Equalizer reset.');
        });
        applyButton.connect('clicked', () =>
            sendEqualizer(selectedPresetId, 'Equalizer applied to the QCY H3.'));

        const propertySignalId = proxy.connect('g-properties-changed', (_source, changed) => {
            const changedProperties = changed.deep_unpack();
            if ('Equalizer' in changedProperties)
                loadEqualizer();
            updateStatus();
        });
        const ownerSignalId = proxy.connect('notify::g-name-owner', updateStatus);
        window.connect('close-request', () => {
            proxy.disconnect(propertySignalId);
            proxy.disconnect(ownerSignalId);
            return false;
        });

        loadEqualizer();
        updateStatus();
        if (proxy.g_name_owner)
            proxy.call('Refresh', new GLib.Variant('()', []), Gio.DBusCallFlags.NONE, 5000, null, () => {});
    }
}