"""Testes unitários para validação segura do perfil vendor."""

import asyncio
import json

import pytest

from qcyh3control.protocol import ProfileError, VendorProfile, decode_hex_payload
from qcyh3control.ble_manager import (
    BleManager,
    classify_qcy_device,
    is_qcy_h3_name,
    parse_bluetoothctl_device_lines,
    parse_bluetoothctl_info,
)
from qcyh3control.rcsp import (
    ANC_MODES,
    EQ_BAND_FREQS,
    EQ_PRESET_OFFSETS,
    MODE_PAYLOADS,
    OP_NOTIFY_COMMUNICATION_WAY,
    decode_anc_mode,
    decode_battery,
    decode_eq_response,
    decode_packet,
    encode_command,
    encode_eq_command,
)


VALID_PROFILE = {
    "enabled": True,
    "service_uuid": "12345678-1234-5678-1234-56789abcdef0",
    "write_characteristic_uuid": "12345678-1234-5678-1234-56789abcdef1",
    "write_type": "with_response",
    "modes": {"normal": "01", "anc": "02 03", "transparency": "0A:0B"},
    "notify_characteristics": [],
}


def test_disabled_example_never_authorizes_commands():
    profile = VendorProfile.from_mapping(
        {
            "enabled": False,
            "service_uuid": None,
            "write_characteristic_uuid": None,
            "modes": {"normal": None, "anc": None, "transparency": None},
        }
    )
    assert not profile.enabled
    with pytest.raises(ProfileError, match="bloqueado"):
        profile.payload_for("anc")


def test_valid_profile_maps_all_three_modes_to_bytes():
    profile = VendorProfile.from_mapping(VALID_PROFILE)
    assert profile.payload_for("normal") == b"\x01"
    assert profile.payload_for("anc") == b"\x02\x03"
    assert profile.payload_for("transparency") == b"\x0a\x0b"


@pytest.mark.parametrize("payload", ["", "0", "0x01", "GG", "01:0", "01::02"])
def test_rejects_malformed_hex(payload):
    with pytest.raises(ProfileError):
        decode_hex_payload(payload, "test")


def test_enabled_profile_requires_all_modes():
    invalid = {**VALID_PROFILE, "modes": {"normal": "01", "anc": "02"}}
    with pytest.raises(ProfileError, match="transparency"):
        VendorProfile.from_mapping(invalid)


def test_enabled_profile_rejects_invalid_uuid():
    invalid = {**VALID_PROFILE, "service_uuid": "not-a-uuid"}
    with pytest.raises(ProfileError, match="UUID inválido"):
        VendorProfile.from_mapping(invalid)


def test_load_json_profile(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(VALID_PROFILE), encoding="utf-8")
    assert VendorProfile.load(path).payload_for("transparency") == b"\x0a\x0b"


@pytest.mark.parametrize(
    "name",
    ["QCY H3", "QCY-H3", "QCY H3 ANC", "QCY_H3_LE", "QCY H3 BLE Headphones", "QCY H3-APP"],
)
def test_recognizes_qcy_h3_device_names(name):
    assert is_qcy_h3_name(name)


@pytest.mark.parametrize("name", ["QCY T13", "QCY H3 Pro", "H3", "Headphone"])
def test_does_not_autoconnect_to_other_device_names(name):
    assert not is_qcy_h3_name(name)


def test_rcsp_ble_handshake_frame():
    packet = encode_command(OP_NOTIFY_COMMUNICATION_WAY, 1, b"\x00\x00")
    assert packet == bytes.fromhex("FE DC BA C0 0B 00 03 01 00 00 EF")


def test_decodes_rcsp_response_and_matches_sequence():
    response = decode_packet(bytes.fromhex("FE DC BA 00 0B 00 02 00 01 EF"))
    assert response is not None
    assert response.is_response
    assert response.status == 0
    assert response.sequence == 1


def test_rcsp_mode_payloads_are_framed_as_custom_commands():
    expected = {
        "normal": "FE DC BA C0 FF 00 06 01 17 03 02 00 00 EF",
        "anc": "FE DC BA C0 FF 00 06 01 17 03 01 01 02 EF",
        "transparency": "FE DC BA C0 FF 00 06 01 17 03 03 01 04 EF",
    }
    for name, payload in MODE_PAYLOADS.items():
        assert encode_command(0xFF, 1, payload).hex(" ").upper() == expected[name]


def test_rcsp_notification_parser_reassembles_fragmented_response():
    loop = asyncio.new_event_loop()
    try:
        manager = BleManager(VendorProfile.from_mapping({"enabled": False}))
        future = loop.create_future()
        manager._rcsp_pending[1] = future
        response = bytes.fromhex("FE DC BA 00 0B 00 02 00 01 EF")

        manager._handle_rcsp_notification(None, bytearray(response[:5]))
        assert not future.done()
        manager._handle_rcsp_notification(None, bytearray(response[5:]))

        assert future.result().sequence == 1
        assert future.result().status == 0
    finally:
        loop.close()


def test_classifies_system_audio_separately_from_ble_endpoint():
    audio_uuids = ["0000110b-0000-1000-8000-00805f9b34fb", "0000111e-0000-1000-8000-00805f9b34fb"]
    ble_uuids = ["0000a002-0000-1000-8000-00805f9b34fb", "00001800-0000-1000-8000-00805f9b34fb"]
    assert classify_qcy_device("QCY H3", audio_uuids, "audio-headset") == "audio"
    assert classify_qcy_device("QCY H3-APP", ble_uuids, "") == "ble"
    assert classify_qcy_device("QCY H3-APP", [], "") == "ble"
    assert classify_qcy_device("QCY T13", audio_uuids, "audio-headset") is None


def test_parses_colored_bluetoothctl_device_list():
    text = "\x1b[0;91mDevice\x1b[0m AA:BB:CC:DD:EE:FF QCY H3\nDevice AA:BB:CC:DD:EE:00 QCY H3-APP\n"
    assert parse_bluetoothctl_device_lines(text) == [
        ("AA:BB:CC:DD:EE:FF", "QCY H3"),
        ("AA:BB:CC:DD:EE:00", "QCY H3-APP"),
    ]


def test_parses_bluetoothctl_info_connection_state():
    text = """
Device AA:BB:CC:DD:EE:FF (public)
        Name: QCY H3
        Alias: QCY H3
        Icon: audio-headset
        Paired: yes
        Connected: yes
        UUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)
"""
    info = parse_bluetoothctl_info(text)
    assert info["connected"] is True
    assert info["name"] == "QCY H3"
    assert info["uuids"] == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert classify_qcy_device(info["name"], info["uuids"], info["icon"]) == "audio"


def test_anc_modes_cover_all_six_levels():
    assert set(ANC_MODES) >= {
        "normal", "transparency", "anc_low", "anc_med", "anc_high", "adaptive",
    }
    # Compatibilidade: chaves antigas continuam válidas.
    assert MODE_PAYLOADS["normal"] == ANC_MODES["normal"]
    assert MODE_PAYLOADS["anc"] == ANC_MODES["anc_high"]
    assert MODE_PAYLOADS["transparency"] == ANC_MODES["transparency"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (bytes((0x17, 0x03, 0x02, 0x00, 0x00)), "normal"),
        (bytes((0x17, 0x03, 0x03, 0x01, 0x04)), "transparency"),
        (bytes((0x17, 0x03, 0x01, 0x01, 0x00)), "anc_low"),
        (bytes((0x17, 0x03, 0x01, 0x01, 0x01)), "anc_med"),
        (bytes((0x17, 0x03, 0x01, 0x01, 0x02)), "anc_high"),
        (bytes((0x17, 0x03, 0x01, 0x05, 0x00)), "adaptive"),
    ],
)
def test_decode_anc_mode(params, expected):
    assert decode_anc_mode(params) == expected


def test_encode_eq_command_layout():
    offsets = [0] * 10
    payload = encode_eq_command(offsets, 0x00)
    assert payload[0] == 0x22
    assert payload[1] == 0x49  # 3 + 10*7
    assert payload[2:5] == bytes((0x00, 0x70, 0xFE))
    assert len(payload) == 2 + 0x49
    # Primeira banda: 55 Hz, 0 dB, ref_gain 40, Q 2.
    assert payload[5:12] == bytes((0x37, 0x00, 0x00, 0x00, 0x28, 0x00, 0x02))


def test_encode_eq_command_clamps_range():
    payload = encode_eq_command([9999] + [-9999] + [0] * 8, 0x00)
    assert payload[5:12] == bytes((0x37, 0x00, 0xB0, 0x04, 0x28, 0x00, 0x02))  # +1200
    assert payload[12:19] == bytes((0xDC, 0x00, 0x50, 0xFB, 0x78, 0x00, 0x02))  # -1200


def test_decode_eq_response_roundtrip():
    offsets = [100, -200, 300, 0, 0, 0, 0, 0, 0, 0]
    payload = encode_eq_command(offsets, 0x00)
    # Resposta GET ecoa [0x22][len][dados...]; decode lê os offsets de volta.
    assert decode_eq_response(payload) == offsets


def test_decode_battery_from_adv_tlv():
    assert decode_battery(bytes((0x02, 0x00, 0x5A))) == 0x5A
    assert decode_battery(bytes((0x02, 0x05, 0x02))) is None


def test_eq_presets_have_ten_bands():
    for preset_id, offsets in EQ_PRESET_OFFSETS.items():
        assert len(offsets) == len(EQ_BAND_FREQS) == 10, preset_id


def test_gnome_dbus_service_exports_panel_contract():
    from qcyh3control.daemon import INTERFACE, QcyControlService

    class ManagerStub:
        pass

    service = QcyControlService(ManagerStub())
    interface = service.introspect()
    assert interface.name == INTERFACE
    assert {prop.name for prop in interface.properties} >= {
        "AudioConnected", "Connected", "ProtocolReady", "DeviceName",
        "Status", "Battery", "Mode", "Equalizer",
    }
    assert {method.name for method in interface.methods} >= {
        "SetMode", "SetEQ", "Refresh", "Connect", "Disconnect",
    }
    service.emit_properties_changed = lambda *_args, **_kwargs: None
    service.consume({
        "event": "system_devices",
        "devices": [{"kind": "audio", "connected": True, "name": "QCY H3"}],
    })
    assert service.AudioConnected is True


def test_system_transition_connects_when_audio_appears():
    from qcyh3control.daemon import system_transition_action

    assert system_transition_action(False, True, False) == "connect"
    # Já conectado por BLE: nada a fazer.
    assert system_transition_action(False, True, True) is None


def test_system_transition_disconnects_when_audio_drops():
    from qcyh3control.daemon import system_transition_action

    assert system_transition_action(True, False, True) == "disconnect"
    # Sem canal BLE aberto: nada a fazer.
    assert system_transition_action(True, False, False) is None


def test_system_transition_ignores_steady_state():
    from qcyh3control.daemon import system_transition_action

    assert system_transition_action(True, True, True) is None
    assert system_transition_action(False, False, False) is None
