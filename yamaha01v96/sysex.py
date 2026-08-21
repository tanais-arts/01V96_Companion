"""Low-level SysEx message building/parsing for the Yamaha 01V96 V2.

Wire format (from "01V96 MIDI SPEC.pdf", section 5.8.3 PARAMETER CHANGE,
"Parameter change (Edit buffer)" / universal-format variant, page 27):

    F0 43 1n 3E 7F 01 ee pp cc dd...dd F7      (parameter change)
    F0 43 3n 3E 7F 01 ee pp cc F7              (parameter request)

    F0          SysEx start
    43          Yamaha manufacturer ID
    1n / 3n     sub-status: 1n = parameter change, 3n = parameter request
                n = device number (0-15), defaults to 0
    3E          group ID (digital mixer)
    7F          model ID (universal)
    01          address type: 0x01 = Edit Buffer (current/live data)
    ee          element number (identifies the parameter group: EQ, comp, ...)
    pp          parameter number within the element
    cc          channel number (0-based; meaning depends on the element -
                input channel 0-31, bus 0-7, aux 0-7, stereo 0, effect 0-3, ...)
    dd...dd     parameter data, 7-bit bytes, MSB first (see encode_value)
    F7          SysEx end

Scene/library store, recall and clear use a different layout, "Function
call" (address type 0x10, spec sections 5.8.3.12/.15), still under
model_id=7F (Universal):

    F0 43 1n 3E 7F 10 ff mh ml ch cl F7        (store / recall)
    F0 43 1n 3E 7F 10 ff mh ml F7              (clear - no channel field)

    ff          function code (see FUNC_* constants, e.g. FUNC_SCENE_RECALL)
    mh/ml       item number (scene/library index), 14-bit value split into
                7-bit High/Low bytes
    ch/cl       channel, 14-bit value split into 7-bit High/Low bytes;
                use CHANNEL_SINGLE_ITEM (256) when not per-channel (e.g. a
                whole scene)

See build_function_call / build_function_clear below.

ee/pp for every known parameter are extracted in docs/parameter_map.json
(built from "01V96 V2 Parameter Change List.xls") and looked up via
yamaha01v96.params.ParameterMap.

NOTE ON DATA ENCODING: confirmed on real 01V96 V2 hardware (2026-08-21) -
the data field for this Parameter Change/Request format is ALWAYS exactly
4 bytes (28 bits, MSB-first two's complement), regardless of the
parameter's real [min, max] range - a 1-bit boolean (kEQOn) and a 10-bit
fader (kFader) both use 4 data bytes. encode_value() always emits 4 bytes
for this reason (PARAMETER_DATA_BYTES). An earlier version computed the
minimal byte count from [min, max] instead (2 bytes for kFader) - this
silently failed to move the real fader at all (wrong message length), so
don't reintroduce range-based sizing here without re-testing on hardware.
decode_value() already adapts to however many bytes were actually received,
so reads were unaffected by this bug.

BULK DUMP (spec section 5.8.2) is a THIRD, unrelated layout used to
transfer whole memories (scene, setup, libraries) rather than one
parameter at a time - this is what a full backup/restore needs:

    F0 43 0n 7E ch cl <ModelID> <DataName> tt bb data...data cs F7  (dump)
    F0 43 2n 7E <ModelID> <DataName> F7                             (request)

    0n / 2n     sub-status: 0n = bulk dump data, 2n = bulk dump request
    ch/cl       DATA COUNT (7-bit High/Low): byte count from <ModelID>
                through the last data byte (i.e. everything the checksum
                covers), NOT present in a request
    <ModelID>   fixed 8-byte 01V96 ID, BULK_MODEL_ID = b"LM  8C93"
    <DataName>  identifies what's being dumped (data-type byte + 2 more
                bytes whose meaning depends on the type - e.g. scene
                number for Scene memory, fixed for Setup/"Current")
    tt/bb       BLOCK INFO: total block number (0-based) / current block
                number - a dump can be split across several SysEx messages
    data...     the actual memory content, packed 7 bytes-of-8-bits into
                8 bytes-of-7-bits per spec (see pack_7to8/unpack_8to7)
    cs          CHECK SUM = (-sum(<ModelID>..last data byte)) & 0x7F

NOTE: the spec doesn't state a maximum per-message block size, and this
has not been tested against real hardware (no hardware access - see repo
memory). build_bulk_dump() here always sends the whole payload as a
single block (block_total=0); if the real console needs the payload
split into several blocks for large dumps (e.g. Setup memory), this is
the first thing to revisit once hardware is available.

See build_bulk_dump / build_bulk_dump_request / parse_bulk_dump,
scene_data_name, and SETUP_DATA_NAME below.
"""
from __future__ import annotations

MANUFACTURER_ID = 0x43
GROUP_ID = 0x3E
MODEL_ID_UNIVERSAL = 0x7F
ADDR_TYPE_EDIT_BUFFER = 0x01
ADDR_TYPE_FUNCTION_CALL = 0x10  # scene/library store/recall/clear (spec 5.8.3.12/.15)

# Function call codes - "Function call: Library store / recall" (spec 5.8.3.12)
FUNC_SCENE_RECALL = 0x00
FUNC_EQ_LIB_RECALL = 0x01
FUNC_GATE_LIB_RECALL = 0x02
FUNC_COMP_LIB_RECALL = 0x03
FUNC_EFF_LIB_RECALL = 0x04
FUNC_CHANNEL_LIB_RECALL = 0x06
FUNC_INPATCH_LIB_RECALL = 0x07
FUNC_OUTPATCH_LIB_RECALL = 0x08
FUNC_SCENE_STORE = 0x20
FUNC_EQ_LIB_STORE = 0x21
FUNC_GATE_LIB_STORE = 0x22
FUNC_COMP_LIB_STORE = 0x23
FUNC_EFF_LIB_STORE = 0x24
FUNC_CHANNEL_LIB_STORE = 0x26
FUNC_INPATCH_LIB_STORE = 0x27
FUNC_OUTPATCH_LIB_STORE = 0x28
# "Function call: Scene/Library Clear" (spec 5.8.3.15) - no channel field
FUNC_SCENE_CLEAR = 0x60
FUNC_EQ_LIB_CLEAR = 0x61
FUNC_GATE_LIB_CLEAR = 0x62
FUNC_COMP_LIB_CLEAR = 0x63
FUNC_EFF_LIB_CLEAR = 0x64
FUNC_CHANNEL_LIB_CLEAR = 0x66
FUNC_INPATCH_LIB_CLEAR = 0x67
FUNC_OUTPATCH_LIB_CLEAR = 0x68

# "Use 256 if the recall destination or store source is a single data item."
CHANNEL_SINGLE_ITEM = 256

# "Function call: title" (spec 5.8.3.13/.14) - same low-nibble codes as the
# recall/store family above, but with high nibble 0x40 instead of 0x00/0x20.
# Unlike store/recall, there is no channel field: just function/number, then
# (for a write) TITLE_SIZE data bytes holding the title text.
FUNC_SCENE_TITLE = 0x40
FUNC_EQ_LIB_TITLE = 0x41
FUNC_GATE_LIB_TITLE = 0x42
FUNC_COMP_LIB_TITLE = 0x43
FUNC_EFF_LIB_TITLE = 0x44
FUNC_CHANNEL_LIB_TITLE = 0x46
FUNC_INPATCH_LIB_TITLE = 0x47
FUNC_OUTPATCH_LIB_TITLE = 0x48

TITLE_SIZE = 16  # character count for every *_TITLE function (spec table, 5.8.3.13)

# Bulk dump (spec 5.8.2) - fixed 01V96 identifier: "LM  8C93"
BULK_MODEL_ID = bytes([0x4C, 0x4D, 0x20, 0x20, 0x38, 0x43, 0x39, 0x33])
BULK_FORMAT_ID = 0x7E  # "Universal bulk dump"

# DATA NAME type byte (first byte of the 3-byte DATA NAME field)
DATA_NAME_SCENE = 0x6D  # 'm' - Scene memory bulk dump (compressed)
DATA_NAME_SETUP = 0x53  # 'S' - Setup memory bulk dump

# Scene number special values (spec 5.8.2.1/.2)
SCENE_EDIT_BUFFER = 256
SCENE_UNDO_BUFFER = 8192

# Setup memory only has one addressable target: "No.256 = Current"
SETUP_DATA_NAME = bytes([DATA_NAME_SETUP, 0x02, 0x00])


# Confirmed on real hardware: every Parameter Change/Request (Edit Buffer /
# universal format) uses exactly this many 7-bit data bytes, regardless of
# the parameter's [min, max] range (see "NOTE ON DATA ENCODING" above).
PARAMETER_DATA_BYTES = 4


def encode_value(value: int, min_value: int, max_value: int) -> list[int]:
    """Encode a signed integer as PARAMETER_DATA_BYTES MSB-first 7-bit bytes."""
    if not (min_value <= value <= max_value):
        raise ValueError(f"value {value} out of range [{min_value}, {max_value}]")
    total_bits = PARAMETER_DATA_BYTES * 7
    uval = value & ((1 << total_bits) - 1)
    return [(uval >> (7 * i)) & 0x7F for i in range(PARAMETER_DATA_BYTES - 1, -1, -1)]


def decode_value(data: list[int], min_value: int, max_value: int) -> int:
    """Decode MSB-first 7-bit data bytes back into a signed integer."""
    total_bits = len(data) * 7
    uval = 0
    for b in data:
        uval = (uval << 7) | (b & 0x7F)
    if min_value < 0 and uval & (1 << (total_bits - 1)):
        uval -= 1 << total_bits
    return uval


def build_parameter_change(
    element: int, param: int, channel: int, value: int,
    min_value: int, max_value: int, device: int = 0,
    model_id: int = MODEL_ID_UNIVERSAL, addr_type: int = ADDR_TYPE_EDIT_BUFFER,
) -> bytes:
    data = encode_value(value, min_value, max_value)
    msg = [
        0xF0, MANUFACTURER_ID, 0x10 | (device & 0x0F), GROUP_ID,
        model_id, addr_type, element, param, channel,
        *data, 0xF7,
    ]
    return bytes(msg)


def build_parameter_request(
    element: int, param: int, channel: int, device: int = 0,
    model_id: int = MODEL_ID_UNIVERSAL, addr_type: int = ADDR_TYPE_EDIT_BUFFER,
) -> bytes:
    msg = [
        0xF0, MANUFACTURER_ID, 0x30 | (device & 0x0F), GROUP_ID,
        model_id, addr_type, element, param, channel, 0xF7,
    ]
    return bytes(msg)


def parse_parameter_change(
    msg: bytes, min_value: int, max_value: int,
    model_id: int = MODEL_ID_UNIVERSAL, addr_type: int = ADDR_TYPE_EDIT_BUFFER,
) -> dict | None:
    """Parse a received parameter-change SysEx.

    Returns a dict with keys device/element/param/channel/value, or None if
    `msg` doesn't match the expected layout for the given model_id/addr_type.
    """
    b = list(msg)
    if len(b) < 10 or b[0] != 0xF0 or b[-1] != 0xF7:
        return None
    if b[1] != MANUFACTURER_ID or (b[2] & 0xF0) != 0x10 or b[3] != GROUP_ID:
        return None
    if b[4] != model_id or b[5] != addr_type:
        return None
    device = b[2] & 0x0F
    element = b[6]
    param = b[7]
    channel = b[8]
    data = b[9:-1]
    value = decode_value(data, min_value, max_value)
    return {
        "device": device,
        "element": element,
        "param": param,
        "channel": channel,
        "value": value,
    }


def build_function_call(
    function: int, number: int, channel: int = CHANNEL_SINGLE_ITEM, device: int = 0,
) -> bytes:
    """Build a scene/library Store or Recall Function call message (spec 5.8.3.12).

    `number` (item index) and `channel` are each split into 7-bit
    High/Low byte pairs, as required by the spec.
    """
    mh, ml = (number >> 7) & 0x7F, number & 0x7F
    ch, cl = (channel >> 7) & 0x7F, channel & 0x7F
    msg = [
        0xF0, MANUFACTURER_ID, 0x10 | (device & 0x0F), GROUP_ID,
        MODEL_ID_UNIVERSAL, ADDR_TYPE_FUNCTION_CALL, function, mh, ml, ch, cl,
        0xF7,
    ]
    return bytes(msg)


def build_function_clear(function: int, number: int, device: int = 0) -> bytes:
    """Build a scene/library Clear Function call message (spec 5.8.3.15)."""
    mh, ml = (number >> 7) & 0x7F, number & 0x7F
    msg = [
        0xF0, MANUFACTURER_ID, 0x10 | (device & 0x0F), GROUP_ID,
        MODEL_ID_UNIVERSAL, ADDR_TYPE_FUNCTION_CALL, function, mh, ml,
        0xF7,
    ]
    return bytes(msg)


def build_title_change(function: int, number: int, title: str, device: int = 0) -> bytes:
    """Build a Function call: title (write) message (spec 5.8.3.13).

    `title` is truncated/space-padded to TITLE_SIZE characters, as 7-bit
    ASCII bytes (non-ASCII characters are replaced).
    """
    mh, ml = (number >> 7) & 0x7F, number & 0x7F
    data = [b & 0x7F for b in title.encode("ascii", errors="replace")]
    data = (data + [0x20] * TITLE_SIZE)[:TITLE_SIZE]
    msg = [
        0xF0, MANUFACTURER_ID, 0x10 | (device & 0x0F), GROUP_ID,
        MODEL_ID_UNIVERSAL, ADDR_TYPE_FUNCTION_CALL, function, mh, ml,
        *data, 0xF7,
    ]
    return bytes(msg)


def build_title_request(function: int, number: int, device: int = 0) -> bytes:
    """Build a Function call: title (request) message (spec 5.8.3.14)."""
    mh, ml = (number >> 7) & 0x7F, number & 0x7F
    msg = [
        0xF0, MANUFACTURER_ID, 0x30 | (device & 0x0F), GROUP_ID,
        MODEL_ID_UNIVERSAL, ADDR_TYPE_FUNCTION_CALL, function, mh, ml, 0xF7,
    ]
    return bytes(msg)


def parse_title_change(msg: bytes) -> dict | None:
    """Parse a received Function call: title message (the console's response
    to a title request has this same layout).

    Returns a dict with keys device/function/number/title, or None if `msg`
    doesn't match the expected layout.
    """
    b = list(msg)
    if len(b) < 9 or b[0] != 0xF0 or b[-1] != 0xF7:
        return None
    if b[1] != MANUFACTURER_ID or (b[2] & 0xF0) != 0x10 or b[3] != GROUP_ID:
        return None
    if b[4] != MODEL_ID_UNIVERSAL or b[5] != ADDR_TYPE_FUNCTION_CALL:
        return None
    device = b[2] & 0x0F
    function = b[6]
    number = (b[7] << 7) | b[8]
    title = bytes(x & 0x7F for x in b[9:-1]).decode("ascii", errors="replace").rstrip()
    return {"device": device, "function": function, "number": number, "title": title}


def pack_7to8(data: bytes) -> bytes:
    """Pack 7 bytes of 8-bit data into 8 bytes of 7-bit data (spec 5.8.2)."""
    out = bytearray()
    for i in range(0, len(data), 7):
        chunk = data[i:i + 7]
        b0 = 0
        for j, d in enumerate(chunk):
            if d & 0x80:
                b0 |= 1 << (6 - j)
        out.append(b0)
        out.extend(d & 0x7F for d in chunk)
    return bytes(out)


def unpack_8to7(data: bytes) -> bytes:
    """Reverse of pack_7to8: restore 8-bit bytes from 7-bit bulk groups."""
    out = bytearray()
    for i in range(0, len(data), 8):
        group = data[i:i + 8]
        b0 = group[0]
        for j in range(1, len(group)):
            high_bit = (b0 >> (7 - j)) & 1
            out.append(group[j] | (high_bit << 7))
    return bytes(out)


def _bulk_checksum(body: bytes) -> int:
    """CHECK SUM = (-sum(body)) & 0x7F (spec 5.8.2)."""
    return (-sum(body)) & 0x7F


def scene_data_name(scene_number: int) -> bytes:
    """DATA NAME for Scene memory bulk dump (spec 5.8.2.1/.2).

    `scene_number`: 0-99 (scene), SCENE_EDIT_BUFFER (256) or
    SCENE_UNDO_BUFFER (8192).
    """
    mh, ml = (scene_number >> 7) & 0x7F, scene_number & 0x7F
    return bytes([DATA_NAME_SCENE, mh, ml])


def build_bulk_dump_request(data_name: bytes, device: int = 0) -> bytes:
    """Build a Bulk Dump Request message (spec 5.8.2)."""
    msg = [0xF0, MANUFACTURER_ID, 0x20 | (device & 0x0F), BULK_FORMAT_ID, *BULK_MODEL_ID, *data_name, 0xF7]
    return bytes(msg)


def build_bulk_dump(
    data_name: bytes, payload: bytes, device: int = 0,
    block_total: int = 0, block_current: int = 0,
) -> bytes:
    """Build one Bulk Dump Data message (spec 5.8.2).

    `payload` must already be 7-bit-packed (see pack_7to8). See the module
    docstring's NOTE about block splitting not being implemented/tested.
    """
    body = bytes([*BULK_MODEL_ID, *data_name, block_total & 0x7F, block_current & 0x7F, *payload])
    checksum = _bulk_checksum(body)
    count = len(body)
    ch, cl = (count >> 7) & 0x7F, count & 0x7F
    msg = [0xF0, MANUFACTURER_ID, 0x00 | (device & 0x0F), BULK_FORMAT_ID, ch, cl, *body, checksum, 0xF7]
    return bytes(msg)


def parse_bulk_dump(msg: bytes) -> dict | None:
    """Parse a received Bulk Dump Data message.

    Returns a dict with keys device/data_name/block_total/block_current/
    payload (still 7-bit-packed, pass through unpack_8to7 once all blocks
    of a multi-block dump are collected), or None if `msg` doesn't match
    the expected layout or fails the checksum.
    """
    b = list(msg)
    if len(b) < 8 or b[0] != 0xF0 or b[-1] != 0xF7:
        return None
    if b[1] != MANUFACTURER_ID or (b[2] & 0xF0) != 0x00 or b[3] != BULK_FORMAT_ID:
        return None
    device = b[2] & 0x0F
    count = b[4] * 128 + b[5]
    body = bytes(b[6:6 + count])
    if len(body) != count or b[6 + count] != _bulk_checksum(body):
        return None
    if body[:8] != BULK_MODEL_ID:
        return None
    return {
        "device": device,
        "data_name": body[8:11],
        "block_total": body[11],
        "block_current": body[12],
        "payload": body[13:],
    }
