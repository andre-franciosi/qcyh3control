"""Mensagens do protocolo JieLi RCSP usado pelo QCY H3.

Implementa framing, sequência, modos ANC (sub-comando 0x17) e equalizador
de 10 bandas (sub-comando 0x22), além de leitura de bateria via GET_ADV_INFO.
Baseado na documentação técnica pública de QCY H3 Controller — Windows Edition
(GMarzochi/QCY_H3_WIndowsEdition); implementação própria.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = bytes((0xFE, 0xDC, 0xBA))
FOOTER = 0xEF
OP_NOTIFY_COMMUNICATION_WAY = 0x0B
OP_CUSTOM = 0xFF
OP_GET_ADV_INFO = 0xC1
OP_SET_ADV_INFO = 0xC0
FLAG_COMMAND = 0x80
FLAG_EXPECT_RESPONSE = 0x40

# Sub-comandos do opCode CUSTOM (0xFF).
SUB_ANC = 0x17
SUB_EQ = 0x22

# Leitura: FE 01 <cmd>
GET_ANC_PAYLOAD = bytes((0xFE, 0x01, SUB_ANC))
GET_EQ_PAYLOAD = bytes((0xFE, 0x01, SUB_EQ))

# RCSP Custom command 0x17: [0x17, 0x03, sub, group, level]
# Mantém as chaves antigas ("normal", "anc", "transparency") por compatibilidade.
ANC_MODES: dict[str, bytes] = {
    "normal": bytes((0x17, 0x03, 0x02, 0x00, 0x00)),       # ANC desligado
    "transparency": bytes((0x17, 0x03, 0x03, 0x01, 0x04)),  # Transparência
    "anc_low": bytes((0x17, 0x03, 0x01, 0x01, 0x00)),       # ANC baixo
    "anc_med": bytes((0x17, 0x03, 0x01, 0x01, 0x01)),       # ANC médio
    "anc": bytes((0x17, 0x03, 0x01, 0x01, 0x02)),           # ANC alto (alias "anc")
    "anc_high": bytes((0x17, 0x03, 0x01, 0x01, 0x02)),      # ANC alto
    "adaptive": bytes((0x17, 0x03, 0x01, 0x05, 0x00)),      # Adaptativo
}

MODE_PAYLOADS: dict[str, bytes] = {
    "normal": ANC_MODES["normal"],
    "anc": ANC_MODES["anc"],
    "transparency": ANC_MODES["transparency"],
}

ANC_LABELS: dict[str, str] = {
    "normal": "Normal (ANC off)",
    "transparency": "Transparência",
    "anc_low": "ANC Baixo",
    "anc_med": "ANC Médio",
    "anc": "ANC Alto",
    "anc_high": "ANC Alto",
    "adaptive": "Adaptativo",
}

# Ordem de exibição na interface (sem duplicar o alias "anc").
ANC_ORDER: tuple[str, ...] = (
    "normal",
    "transparency",
    "anc_low",
    "anc_med",
    "anc_high",
    "adaptive",
)

# Equalizador: 10 bandas, frequências em Hz (little-endian no protocolo).
EQ_BAND_FREQS: tuple[int, ...] = (
    0x0037,  # 55 Hz
    0x00DC,  # 220 Hz
    0x01F4,  # 500 Hz
    0x03E8,  # 1 kHz
    0x0708,  # 1.8 kHz
    0x0AF0,  # 2.8 kHz
    0x1194,  # 4.5 kHz
    0x1D4C,  # 7.5 kHz
    0x2710,  # 10 kHz
    0x55F0,  # 22 kHz
)
EQ_BAND_LABELS: tuple[str, ...] = (
    "55 Hz", "220 Hz", "500 Hz", "1 kHz", "1.8 kHz",
    "2.8 kHz", "4.5 kHz", "7.5 kHz", "10 kHz", "22 kHz",
)
# (ref_gain, Q) por frequência — capturados da resposta GET do aparelho.
EQ_BAND_DEFAULTS: dict[int, tuple[int, int]] = {
    0x0037: (40, 2),
    0x00DC: (120, 2),
    0x01F4: (150, 2),
    0x03E8: (150, 2),
    0x0708: (120, 2),
    0x0AF0: (80, 2),
    0x1194: (130, 2),
    0x1D4C: (120, 2),
    0x2710: (120, 2),
    0x55F0: (80, 2),
}

EQ_PRESETS: tuple[tuple[str, int], ...] = (
    ("Custom", 0x00),
    ("Default", 0x01),
    ("Pop", 0x02),
    ("Heavy Bass", 0x03),
    ("Rock", 0x04),
    ("Soft", 0x05),
    ("Classic", 0x06),
)
EQ_PRESET_IDS: tuple[int, ...] = tuple(pid for _, pid in EQ_PRESETS)
EQ_PRESET_NAMES: tuple[str, ...] = tuple(name for name, _ in EQ_PRESETS)

# Offsets em centésimos de dB (-1200 .. +1200), 10 bandas.
EQ_PRESET_OFFSETS: dict[int, list[int]] = {
    0x00: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    0x01: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    0x02: [0, 150, 150, 100, 0, 100, 200, 200, 150, 100],
    0x03: [500, 400, 200, 0, -100, 0, 0, 100, 200, 100],
    0x04: [400, 300, 0, -200, -100, 0, 200, 300, 400, 200],
    0x05: [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
    0x06: [0, 0, 100, 300, 300, 300, 200, 100, 100, 0],
}

EQ_MIN_DB100 = -1200
EQ_MAX_DB100 = 1200

ADV_BATTERY = 0
ADV_WORK_MODE = 5


@dataclass(frozen=True)
class RCSPPacket:
    """Pacote RCSP extraído de uma notificação GATT."""

    op_code: int
    sequence: int
    params: bytes
    is_response: bool
    status: int = 0


def encode_command(op_code: int, sequence: int, params: bytes, *, expect_response: bool = True) -> bytes:
    """Codifica um comando RCSP com número de sequência e framing H3."""
    if not 0 <= op_code <= 0xFF or not 0 <= sequence <= 0xFF:
        raise ValueError("op_code e sequence precisam ser valores de 1 byte.")
    body = bytes((sequence,)) + bytes(params)
    if len(body) > 0xFFFF:
        raise ValueError("O corpo RCSP excede o tamanho máximo de 65535 bytes.")
    flags = FLAG_COMMAND | (FLAG_EXPECT_RESPONSE if expect_response else 0)
    return (
        HEADER
        + bytes((flags, op_code, (len(body) >> 8) & 0xFF, len(body) & 0xFF))
        + body
        + bytes((FOOTER,))
    )


def decode_packet(frame: bytes) -> RCSPPacket | None:
    """Decodifica um único quadro completo; bytes extras ou truncados são recusados."""
    if len(frame) < 8 or frame[:3] != HEADER:
        return None
    flags = frame[3]
    op_code = frame[4]
    body_length = (frame[5] << 8) | frame[6]
    if len(frame) != 7 + body_length + 1 or frame[-1] != FOOTER:
        return None

    is_response = not bool(flags & FLAG_COMMAND)
    body = frame[7:-1]
    if is_response:
        if len(body) < 2:
            return None
        return RCSPPacket(
            op_code=op_code,
            sequence=body[1],
            params=body[2:],
            is_response=True,
            status=body[0],
        )
    if not body:
        return None
    return RCSPPacket(
        op_code=op_code,
        sequence=body[0],
        params=body[1:],
        is_response=False,
    )


def decode_anc_mode(params: bytes) -> str | None:
    """Decodifica a chave do modo ANC a partir dos params da resposta GET 0x17."""
    if len(params) < 5 or params[0] != SUB_ANC:
        return None
    sub, group, level = params[2], params[3], params[4]
    if sub == 0x02:
        return "normal"
    if sub == 0x03:
        return "transparency"
    if sub == 0x01:
        if group == 0x01:
            return ("anc_low", "anc_med", "anc_high")[level] if level <= 2 else None
        if group == 0x05:
            return "adaptive"
    return None


def decode_eq_response(params: bytes) -> list[int] | None:
    """Decodifica a resposta GET 0x22 em 10 offsets (centésimos de dB).

    Para frequências duplicadas (ex.: 4.5 kHz aparece duas vezes), a última
    entrada vence — é a cópia ajustável pelo usuário (Q=2).
    """
    if len(params) < 2 or params[0] != SUB_EQ:
        return None
    data_len = params[1]
    data = params[2 : 2 + data_len]
    if len(data) < 3 + 7:
        return None
    bands: dict[int, int] = {}
    num_entries = (len(data) - 3) // 7
    for i in range(num_entries):
        base = 3 + i * 7
        entry = data[base : base + 7]
        if len(entry) < 7:
            break
        freq = int.from_bytes(entry[0:2], "little")
        db_off = int.from_bytes(entry[2:4], "little", signed=True)
        if freq in EQ_BAND_FREQS:
            bands[freq] = db_off
    if not bands:
        return None
    return [bands.get(freq, 0) for freq in EQ_BAND_FREQS]


def encode_eq_command(offsets: list[int], preset_id: int = 0x00) -> bytes:
    """Monta os params do SET de EQ: [0x22][len][preset][0x70][0xFE][10×7B].

    Cada banda: [freq:2 LE][delta_db:2 LE signed][ref_gain:2 LE][Q:1].
    Offsets em centésimos de dB, limitados a -1200..+1200.
    """
    if len(offsets) != len(EQ_BAND_FREQS):
        raise ValueError(f"EQ precisa de {len(EQ_BAND_FREQS)} bandas, recebeu {len(offsets)}.")
    band_bytes = bytearray()
    for i, freq in enumerate(EQ_BAND_FREQS):
        ref_gain, q = EQ_BAND_DEFAULTS[freq]
        db_off = max(EQ_MIN_DB100, min(EQ_MAX_DB100, int(offsets[i])))
        band_bytes += freq.to_bytes(2, "little")
        band_bytes += db_off.to_bytes(2, "little", signed=True)
        band_bytes += ref_gain.to_bytes(2, "little")
        band_bytes += bytes((q,))
    header = bytes((preset_id & 0xFF, 0x70, 0xFE))
    data_len = len(header) + len(band_bytes)
    return bytes((SUB_EQ, data_len & 0xFF)) + header + bytes(band_bytes)


def parse_tlv(data: bytes) -> list[tuple[int, bytes]]:
    """Quebra sequência [len:1][type:1][value:len-1] usada no GET_ADV_INFO."""
    out: list[tuple[int, bytes]] = []
    i = 0
    while i < len(data):
        if i + 1 >= len(data):
            break
        length = data[i]
        if length < 1 or i + 1 + length > len(data):
            break
        out.append((data[i + 1], bytes(data[i + 2 : i + 1 + length])))
        i += 1 + length
    return out


def decode_battery(params: bytes) -> int | None:
    """Extrai o percentual de bateria (ADV type 0) da resposta GET_ADV_INFO."""
    for adv_type, value in parse_tlv(params):
        if adv_type == ADV_BATTERY and value:
            return value[0]
    return None
