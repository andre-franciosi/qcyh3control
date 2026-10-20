import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {BluezController} from './bluez.js';
import {EQ_BAND_LABELS, EQ_PRESETS, EQ_PRESET_OFFSETS} from './rcsp.js';

const FREQUENCIES = EQ_BAND_LABELS;
const PRESETS = EQ_PRESETS;

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
            subtitle: 'Connecting to the QCY H3…',
        });
        group.add(statusRow);

        const controller = new BluezController({
            onState: state => updateStatus(state),
            onLog: message => console.debug(`QCY H3 prefs: ${message}`),
            onError: message => statusRow.set_subtitle(message),
        });

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

        const loadEqualizer = state => {
            const current = state.equalizer;
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

        const updateStatus = state => {
            if (state.protocolReady)
                statusRow.set_subtitle('Connected · equalizer ready');
            else if (state.connected)
                statusRow.set_subtitle('BLE connected · waiting for the RCSP protocol…');
            else if (state.audioConnected)
                statusRow.set_subtitle('Audio connected · opening the control channel…');
            else
                statusRow.set_subtitle('Waiting for the QCY H3 to connect through GNOME Bluetooth…');

            controls.forEach(control => control.set_sensitive(state.protocolReady));
            if (state.protocolReady)
                loadEqualizer(state);
        };

        const sendEqualizer = (presetId, successMessage) => {
            if (!controller.state.protocolReady) {
                updateStatus(controller.state);
                return;
            }
            statusRow.set_subtitle('Sending equalizer…');
            controller.setEqualizer(offsets, presetId);
            statusRow.set_subtitle(successMessage);
        };

        presetDropdown.connect('notify::selected', () => {
            if (syncing)
                return;
            const preset = PRESETS[presetDropdown.get_selected()];
            if (!preset)
                return;
            selectedPresetId = preset[1];
            if (selectedPresetId !== 0) {
                offsets = [...EQ_PRESET_OFFSETS[selectedPresetId]];
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

        window.connect('close-request', () => {
            controller.destroy();
            return false;
        });

        controller.refresh();
    }
}