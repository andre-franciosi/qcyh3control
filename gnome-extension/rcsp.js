// JieLi RCSP protocol used by the QCY H3, ported to GJS.
//
// Implements framing, sequencing, ANC modes (custom sub-command 0x17) and the
// 10-band equalizer (custom sub-command 0x22), plus battery reading through
// GET_ADV_INFO. Based on the public technical documentation of
// QCY H3 Controller — Windows Edition (GMarzochi/QCY_H3_WIndowsEdition);
// this is an independent implementation.

export const HEADER = [0xfe, 0xdc, 0xba];
export const FOOTER = 0xef;
export const OP_NOTIFY_COMMUNICATION_WAY = 0x0b;
export const OP_CUSTOM = 0xff;
export const OP_GET_ADV_INFO = 0xc1;
export const OP_SET_ADV_INFO = 0xc0;
export const FLAG_COMMAND = 0x80;
export const FLAG_EXPECT_RESPONSE = 0x40;

// Custom (0xFF) sub-commands.
export const SUB_ANC = 0x17;
export const SUB_EQ = 0x22;

// Read requests: FE 01 <cmd>
export const GET_ANC_PAYLOAD = [0xfe, 0x01, SUB_ANC];
export const GET_EQ_PAYLOAD = [0xfe, 0x01, SUB_EQ];

// RCSP custom command 0x17: [0x17, 0x03, sub, group, level]
export const ANC_MODES = {
    normal: [0x17, 0x03, 0x02, 0x00, 0x00],       // ANC off
    transparency: [0x17, 0x03, 0x03, 0x01, 0x04], // Transparency
    anc_low: [0x17, 0x03, 0x01, 0x01, 0x00],      // Low ANC
    anc_med: [0x17, 0x03, 0x01, 0x01, 0x01],      // Medium ANC
    anc: [0x17, 0x03, 0x01, 0x01, 0x02],          // High ANC (alias "anc")
    anc_high: [0x17, 0x03, 0x01, 0x01, 0x02],     // High ANC
    adaptive: [0x17, 0x03, 0x01, 0x05, 0x00],     // Adaptive
};

// Display order in the UI (without duplicating the "anc" alias).
export const ANC_ORDER = [
    'normal', 'transparency', 'anc_low', 'anc_med', 'anc_high', 'adaptive',
];

// Equalizer: 10 bands, frequencies in Hz (little-endian on the wire).
export const EQ_BAND_FREQS = [
    0x0037, 0x00dc, 0x01f4, 0x03e8, 0x0708,
    0x0af0, 0x1194, 0x1d4c, 0x2710, 0x55f0,
];
export const EQ_BAND_LABELS = [
    '55 Hz', '220 Hz', '500 Hz', '1 kHz', '1.8 kHz',
    '2.8 kHz', '4.5 kHz', '7.5 kHz', '10 kHz', '22 kHz',
];
// (ref_gain, Q) per frequency — captured from the device GET response.
export const EQ_BAND_DEFAULTS = {
    0x0037: [40, 2],
    0x00dc: [120, 2],
    0x01f4: [150, 2],
    0x03e8: [150, 2],
    0x0708: [120, 2],
    0x0af0: [80, 2],
    0x1194: [130, 2],
    0x1d4c: [120, 2],
    0x2710: [120, 2],
    0x55f0: [80, 2],
};

export const EQ_PRESETS = [
    ['Custom', 0x00], ['Default', 0x01], ['Pop', 0x02], ['Heavy Bass', 0x03],
    ['Rock', 0x04], ['Soft', 0x05], ['Classic', 0x06],
];
export const EQ_PRESET_IDS = EQ_PRESETS.map(([, id]) => id);

// Offsets in hundredths of dB (-1200 .. +1200), 10 bands.
export const EQ_PRESET_OFFSETS = {
    0x00: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    0x01: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    0x02: [0, 150, 150, 100, 0, 100, 200, 200, 150, 100],
    0x03: [500, 400, 200, 0, -100, 0, 0, 100, 200, 100],
    0x04: [400, 300, 0, -200, -100, 0, 200, 300, 400, 200],
    0x05: [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
    0x06: [0, 0, 100, 300, 300, 300, 200, 100, 100, 0],
};

export const EQ_MIN_DB100 = -1200;
export const EQ_MAX_DB100 = 1200;

export const ADV_BATTERY = 0;

function toBytes(value) {
    if (value instanceof Uint8Array)
        return value;
    return Uint8Array.from(value);
}

/** Encode an RCSP command with sequence number and H3 framing. */
export function encodeCommand(opCode, sequence, params, expectResponse = true) {
    if (opCode < 0 || opCode > 0xff || sequence < 0 || sequence > 0xff)
        throw new Error('opCode and sequence must be single bytes.');
    const paramsBytes = toBytes(params);
    const body = new Uint8Array(1 + paramsBytes.length);
    body[0] = sequence;
    body.set(paramsBytes, 1);
    if (body.length > 0xffff)
        throw new Error('RCSP body exceeds the 65535-byte maximum.');
    const flags = FLAG_COMMAND | (expectResponse ? FLAG_EXPECT_RESPONSE : 0);
    const frame = new Uint8Array(7 + body.length + 1);
    frame.set(HEADER, 0);
    frame[3] = flags;
    frame[4] = opCode;
    frame[5] = (body.length >> 8) & 0xff;
    frame[6] = body.length & 0xff;
    frame.set(body, 7);
    frame[frame.length - 1] = FOOTER;
    return frame;
}

/** Decode a single complete frame; extra or truncated bytes are rejected. */
export function decodePacket(frame) {
    const bytes = toBytes(frame);
    if (bytes.length < 8 || bytes[0] !== HEADER[0] || bytes[1] !== HEADER[1] || bytes[2] !== HEADER[2])
        return null;
    const flags = bytes[3];
    const opCode = bytes[4];
    const bodyLength = (bytes[5] << 8) | bytes[6];
    if (bytes.length !== 7 + bodyLength + 1 || bytes[bytes.length - 1] !== FOOTER)
        return null;

    const isResponse = !(flags & FLAG_COMMAND);
    const body = bytes.slice(7, bytes.length - 1);
    if (isResponse) {
        if (body.length < 2)
            return null;
        return {
            opCode,
            sequence: body[1],
            params: body.slice(2),
            isResponse: true,
            status: body[0],
        };
    }
    if (!body.length)
        return null;
    return {
        opCode,
        sequence: body[0],
        params: body.slice(1),
        isResponse: false,
        status: 0,
    };
}

/** Decode the ANC mode key from the params of a GET 0x17 response. */
export function decodeAncMode(params) {
    const p = toBytes(params);
    if (p.length < 5 || p[0] !== SUB_ANC)
        return null;
    const sub = p[2];
    const group = p[3];
    const level = p[4];
    if (sub === 0x02)
        return 'normal';
    if (sub === 0x03)
        return 'transparency';
    if (sub === 0x01) {
        if (group === 0x01)
            return level <= 2 ? ['anc_low', 'anc_med', 'anc_high'][level] : null;
        if (group === 0x05)
            return 'adaptive';
    }
    return null;
}

/** Decode a GET 0x22 response into 10 offsets (hundredths of dB). */
export function decodeEqResponse(params) {
    const p = toBytes(params);
    if (p.length < 2 || p[0] !== SUB_EQ)
        return null;
    const dataLen = p[1];
    const data = p.slice(2, 2 + dataLen);
    if (data.length < 3 + 7)
        return null;
    const bands = new Map();
    const numEntries = Math.floor((data.length - 3) / 7);
    for (let i = 0; i < numEntries; i++) {
        const base = 3 + i * 7;
        const entry = data.slice(base, base + 7);
        if (entry.length < 7)
            break;
        const freq = entry[0] | (entry[1] << 8);
        let dbOff = entry[2] | (entry[3] << 8);
        if (dbOff & 0x8000)
            dbOff -= 0x10000;
        if (EQ_BAND_FREQS.includes(freq))
            bands.set(freq, dbOff);
    }
    if (!bands.size)
        return null;
    return EQ_BAND_FREQS.map(freq => bands.get(freq) ?? 0);
}

/** Build the EQ SET params: [0x22][len][preset][0x70][0xFE][10×7B]. */
export function encodeEqCommand(offsets, presetId = 0x00) {
    if (offsets.length !== EQ_BAND_FREQS.length)
        throw new Error(`EQ needs ${EQ_BAND_FREQS.length} bands, got ${offsets.length}.`);
    const bandBytes = [];
    for (let i = 0; i < EQ_BAND_FREQS.length; i++) {
        const freq = EQ_BAND_FREQS[i];
        const [refGain, q] = EQ_BAND_DEFAULTS[freq];
        const dbOff = Math.max(EQ_MIN_DB100, Math.min(EQ_MAX_DB100, Math.trunc(offsets[i])));
        bandBytes.push(freq & 0xff, (freq >> 8) & 0xff);
        bandBytes.push(dbOff & 0xff, (dbOff >> 8) & 0xff);
        bandBytes.push(refGain & 0xff, (refGain >> 8) & 0xff);
        bandBytes.push(q & 0xff);
    }
    const header = [presetId & 0xff, 0x70, 0xfe];
    const dataLen = header.length + bandBytes.length;
    return Uint8Array.from([SUB_EQ, dataLen & 0xff, ...header, ...bandBytes]);
}

/** Split the [len:1][type:1][value:len-1] sequence used in GET_ADV_INFO. */
export function parseTlv(data) {
    const bytes = toBytes(data);
    const out = [];
    let i = 0;
    while (i < bytes.length) {
        if (i + 1 >= bytes.length)
            break;
        const length = bytes[i];
        if (length < 1 || i + 1 + length > bytes.length)
            break;
        out.push([bytes[i + 1], bytes.slice(i + 2, i + 1 + length)]);
        i += 1 + length;
    }
    return out;
}

/** Extract the battery percentage (ADV type 0) from a GET_ADV_INFO response. */
export function decodeBattery(params) {
    for (const [advType, value] of parseTlv(params)) {
        if (advType === ADV_BATTERY && value.length)
            return value[0];
    }
    return null;
}
