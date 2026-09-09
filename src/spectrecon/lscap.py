"""Lilyshark .lscap reader, protocol decode, and licensing join.

`.lscap` is Lilyshark's protocol-neutral LoRa capture (docs at
lilyshark.com / docs/lilyshark-capture-format.md). Layout is little-endian
v1.1: 24-byte file header (LSCP) then 80-byte records (LSFR) + payload.

Decode is profile-gated the same way the firmware is: Meshtastic, MeshCore,
and Reticulum do not share a magic byte, so the T-Deck records which radio
profile was tuned. PHY (bandwidth / SF / sync word) is the fallback when
the profile id is unknown.

Meshtastic payloads on the published default channel key (AQ== / defaultpsk)
are readable without attacking a cipher. We keep NodeInfo names and Position
coordinates for the licensing join and discard message text. A real PSK
stays opaque. MeshCore advertisement bodies are public broadcasts (key,
optional GPS, name) — not contact expansion, not payload decrypt.

Joins beyond the radio header: TX frames identify the capturing T-Deck;
BLE name `Lilyshark XXXX` is only `node_num & 0xffff` (`!****XXXX`) — never
a full node id; Field GPS fills in when the payload has no Position; USB
`LSK T` supplies the deck's own GPS when Field is not next to the radio;
witness keys (or payload SHA-256) corroborate the same over-the-air frame
across two captures. `0x4C534B01` is the simulator/fallback in a radio
header, not something a BLE short invents.
"""

from __future__ import annotations

import hashlib
import re
import struct
from pathlib import Path

FILE_MAGIC = b"LSCP"
RECORD_MAGIC = b"LSFR"
FILE_HEADER_SIZE = 24
RECORD_HEADER_SIZE = 80
MAX_CAPTURED_LENGTH = 255
SYNTHETIC_FLAG = 1 << 2
NET_RELAYED_FLAG = 1 << 3  # LSK INJ / USB-relayed; not this deck's air

FILE_HEADER = struct.Struct("<4sHHHHIII")
RECORD_HEADER = struct.Struct("<4sHHHHQQIIIIIIihhHHHhbBBBBBBBB3s")

# Firmware identity on Meshtastic LongFast (meshtastic_encode.h).
LILYSHARK_NODE_NUM = 0x4C534B01

MESHTASTIC_HEADER_LEN = 16
BROADCAST = 0xFFFFFFFF

# Published Meshtastic default channel key (Channels.h defaultpsk / AQ==).
MESHTASTIC_DEFAULT_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")

PORT_LABELS = {
    1: "TEXT",
    3: "POSITION",
    4: "NODEINFO",
    5: "ROUTING",
    67: "TELEMETRY",
    70: "TRACEROUTE",
    71: "NEIGHBORINFO",
}

MESHCORE_KINDS = {
    0: "request",
    1: "response",
    2: "text",
    3: "ack",
    4: "advert",
    5: "group_text",
    6: "group_data",
    7: "anon_request",
    8: "returned_path",
    9: "trace",
    10: "multipart",
    11: "control",
    15: "raw",
}

# Lilyshark builtin_profiles.cpp — id, protocol, name, freq, bw, sf, sync.
PROFILES: dict[int, dict] = {
    1: {"protocol": "meshtastic", "name": "MESHTASTIC US LF",
        "freq_hz": 906_875_000, "bandwidth_hz": 250_000, "sf": 11, "sync": 0x2B},
    2: {"protocol": "meshcore", "name": "MESHCORE US",
        "freq_hz": 910_525_000, "bandwidth_hz": 62_500, "sf": 7, "sync": 0x1424},
    3: {"protocol": "meshcore", "name": "MESHCORE LEGACY",
        "freq_hz": 915_000_000, "bandwidth_hz": 250_000, "sf": 10, "sync": 0x1424},
    4: {"protocol": "meshtastic", "name": "MESHTASTIC BAY MF",
        "freq_hz": 913_125_000, "bandwidth_hz": 250_000, "sf": 9, "sync": 0x2B},
    5: {"protocol": "reticulum", "name": "RNODE EXAMPLE US",
        "freq_hz": 915_000_000, "bandwidth_hz": 125_000, "sf": 8, "sync": 0x1424},
}

_PHY_HINT = {
    (p["bandwidth_hz"], p["sf"], p["sync"]): p["protocol"]
    for p in PROFILES.values()
}

# (lo_hz, hi_hz, label) — Part 15 / SRD bands LoRa meshes actually use.
_BANDS = (
    (902_000_000, 928_000_000, "ISM 902-928"),
    (863_000_000, 870_000_000, "EU 868"),
    (433_050_000, 434_790_000, "ISM 433"),
    (2_400_000_000, 2_483_500_000, "ISM 2.4"),
)

# FrameDirection / CrcStatus — docs/lilyshark-capture-format.md
DIRECTION_UNKNOWN = 0
DIRECTION_RX = 1
DIRECTION_TX = 2
CRC_VALID = 2
PRESENT_TIMESTAMP = 1 << 0
PRESENT_CENTER_FREQUENCY = 1 << 1
PRESENT_RSSI = 1 << 5
PRESENT_SNR = 1 << 6

# Field Receipts witness key (docs/protocol/field-receipts.md).
WITNESS_FREQ_STEP_HZ = 25_000
WITNESS_BUCKET_SECONDS = 60
WITNESS_VECTOR_PAYLOAD = bytes(range(0xA0, 0xC0))
WITNESS_VECTOR_FREQ_HZ = 906_862_500
WITNESS_VECTOR_UNIX_SECONDS = 1_893_456_000
WITNESS_VECTOR_KEY_HEX = (
    "94ed6915ddbbfb1b5c2557f5ecb61cfe3783f40be380323af53beb8c3b610125"
)

# Firmware BLE local name is `Lilyshark %04X` (sim_main.cpp); mesh long
# name is `Lilyshark-%04X` (mesh_identity.cpp). The four hex digits are
# localMeshtasticNodeNum() & 0xffff — so `Lilyshark 4B01` joins !4c534b01.
_LILYSHARK_SHORT_RE = re.compile(
    r"^lilyshark[\s\-_](?P<short>[0-9a-f]{4})$", re.I
)


class LscapError(ValueError):
    pass


def band_for_hz(freq_hz: int | None) -> str | None:
    """Named unlicensed/SRD band for a center frequency, if any."""
    if not freq_hz:
        return None
    for lo, hi, label in _BANDS:
        if lo <= freq_hz <= hi:
            return label
    return None


def bang_mask(short: str | None) -> str | None:
    """Field's LoRa label for a BLE short: `!****4B01`. Not a node number."""
    if not short:
        return None
    text = str(short).strip().upper()
    if len(text) != 4:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return f"!****{text}"


def parse_lilyshark_name(name: str | None) -> dict | None:
    """Parse firmware BLE / mesh names: `Lilyshark 4B01` or `Lilyshark-4B01`.

    Returns None when the string is not a Lilyshark identity. `short` is the
    4-hex suffix (`localMeshtasticNodeNum() & 0xffff`). That is not a full
    node id — `4B01` does not mean `0x4C534B01`. Full `!xxxxxxxx` values come
    from a radio header or `LSK ID.node`.
    """
    if not name:
        return None
    text = name.strip()
    if not text.lower().startswith("lilyshark"):
        return None
    match = _LILYSHARK_SHORT_RE.match(text)
    short = match.group("short").upper() if match else None
    out: dict = {"name": text, "short": short, "bang_mask": bang_mask(short)}
    if short:
        out["suffix_int"] = int(short, 16)
    return out


def node_matches_short(from_node: int | None, short: str | None) -> bool:
    """True when `from_node`'s low 16 bits equal the BLE/mesh short name."""
    if from_node is None or not short:
        return False
    try:
        return (int(from_node) & 0xFFFF) == int(short, 16)
    except ValueError:
        return False


def round_witness_frequency_hz(freq_hz: int) -> int:
    """Nearest 25 kHz step, half-up — Field Receipts rounding."""
    return ((int(freq_hz) + WITNESS_FREQ_STEP_HZ // 2)
            // WITNESS_FREQ_STEP_HZ) * WITNESS_FREQ_STEP_HZ


def witness_time_bucket(unix_seconds: int) -> int:
    return int(unix_seconds) // WITNESS_BUCKET_SECONDS


def witness_key(payload: bytes, freq_hz: int, unix_seconds: int) -> bytes:
    """SHA-256(payload || u32le(rounded_freq) || u32le(time_bucket))."""
    preimage = (
        payload
        + round_witness_frequency_hz(freq_hz).to_bytes(4, "little")
        + witness_time_bucket(unix_seconds).to_bytes(4, "little")
    )
    return hashlib.sha256(preimage).digest()


def frame_ineligibility(frame: dict, has_wall_clock: bool) -> str | None:
    """Why this record yields no witness key, or None if eligible."""
    if frame.get("synthetic"):
        return "synthetic"
    if int(frame.get("direction") or 0) == DIRECTION_TX:
        return "self_transmitted"
    if int(frame.get("crc_state") or 0) != CRC_VALID:
        return "crc_not_valid"
    captured = int(frame.get("captured_length") or len(frame.get("payload") or b""))
    original = int(frame.get("original_length") or captured)
    if captured < 1:
        return "empty_payload"
    if captured != original:
        return "truncated"
    present = int(frame.get("present_fields") or 0)
    if present & (PRESENT_TIMESTAMP | PRESENT_CENTER_FREQUENCY) != (
        PRESENT_TIMESTAMP | PRESENT_CENTER_FREQUENCY
    ):
        return "required_fields_absent"
    if not has_wall_clock:
        return "no_wall_clock"
    return None


def sidecar_paths(capture: Path) -> list[Path]:
    """Candidate `<capture>.witness` locations next to an .lscap."""
    return [
        capture.with_name(capture.name + ".witness"),
        capture.with_suffix(".witness"),
    ]


def read_witness_sidecar(path: Path) -> dict | None:
    """Parse a firmware witness sidecar: epoch + sequence → key hex."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    header = lines[0].split()
    if len(header) < 4 or header[0] != "lilyshark-witness":
        return None
    try:
        epoch = int(header[3])
    except ValueError:
        return None
    keys: dict[int, str] = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            seq = int(parts[0])
        except ValueError:
            continue
        key = parts[1].lower()
        if len(key) == 64 and all(c in "0123456789abcdef" for c in key):
            keys[seq] = key
    return {"epoch": epoch, "source": header[2], "version": header[1], "keys": keys}


def iter_frames(path: Path) -> list[dict]:
    """Read every complete frame from an .lscap v1 file."""
    data = path.read_bytes()
    if len(data) < FILE_HEADER_SIZE:
        raise LscapError("file too short for an .lscap header")
    magic, major, minor, header_size, rec_size, *_ = FILE_HEADER.unpack(
        data[:FILE_HEADER_SIZE]
    )
    if magic != FILE_MAGIC:
        raise LscapError(f"not an .lscap file (magic {magic!r})")
    if major != 1:
        raise LscapError(f"unsupported .lscap major version {major}")
    if header_size < FILE_HEADER_SIZE or rec_size < RECORD_HEADER_SIZE:
        raise LscapError("invalid header sizes")
    offset = header_size
    frames = []
    index = 0
    while offset < len(data):
        if len(data) - offset < RECORD_HEADER_SIZE:
            break  # trailing fragment; recovery readers stop here
        rec = data[offset:offset + RECORD_HEADER_SIZE]
        (rmagic, rheader, _layout, captured, original, sequence, timestamp_us,
         present_fields, freq_hz, bw_hz, _bitrate, _fdev, airtime_us, _ferr,
         rssi_x10, snr_x10, _preamble, sync_word, profile_id, _rstat, _txpwr,
         sf, cr, _ch, _radio, modulation, direction, crc, meta,
         _reserved) = RECORD_HEADER.unpack(rec)
        if rmagic != RECORD_MAGIC:
            raise LscapError(f"record {index}: bad magic {rmagic!r}")
        payload_off = offset + rheader
        payload = data[payload_off:payload_off + captured]
        if len(payload) != captured:
            break
        frames.append({
            "index": index,
            "sequence": sequence,
            "timestamp_us": timestamp_us,
            "freq_hz": freq_hz,
            "bandwidth_hz": bw_hz,
            "airtime_us": airtime_us,
            "rssi_dbm": (
                rssi_x10 / 10.0
                if (present_fields & PRESENT_RSSI) and direction == DIRECTION_RX
                else None
            ),
            "snr_db": (
                snr_x10 / 10.0
                if (present_fields & PRESENT_SNR) and direction == DIRECTION_RX
                else None
            ),
            "profile_id": profile_id,
            "sync_word": sync_word,
            "spreading_factor": sf,
            "coding_rate": cr,
            "modulation": modulation,
            "direction": direction,
            "crc_state": crc,
            "synthetic": bool(meta & SYNTHETIC_FLAG),
            "net_relayed": bool(meta & NET_RELAYED_FLAG),
            "payload": payload,
            "original_length": original,
            "captured_length": captured,
            "present_fields": present_fields,
        })
        offset = payload_off + captured
        index += 1
    return frames


def protocol_for_frame(frame: dict) -> str:
    """Pick Meshtastic / MeshCore / Reticulum from profile id, then PHY."""
    profile = PROFILES.get(int(frame.get("profile_id") or 0))
    if profile:
        return profile["protocol"]
    hint = _PHY_HINT.get((
        int(frame.get("bandwidth_hz") or 0),
        int(frame.get("spreading_factor") or 0),
        int(frame.get("sync_word") or 0),
    ))
    return hint or "unknown"


def parse_meshtastic_header(payload: bytes) -> dict | None:
    """Unencrypted 16-byte Meshtastic radio header, or None if too short."""
    if len(payload) < MESHTASTIC_HEADER_LEN:
        return None
    dest = int.from_bytes(payload[0:4], "little")
    src = int.from_bytes(payload[4:8], "little")
    pkt = int.from_bytes(payload[8:12], "little")
    flags = payload[12]
    if src == 0:
        return None
    hop_limit = flags & 0x07
    hop_start = (flags & 0xE0) >> 5
    return {
        "to_node": dest,
        "from_node": src,
        "from_bang": f"!{src:08x}",
        "packet_id": pkt,
        "channel_hash": payload[13],
        "hop_limit": hop_limit,
        "hop_start": hop_start,
        "want_ack": bool(flags & 0x08),
        "via_mqtt": bool(flags & 0x10),
        "broadcast": dest == BROADCAST,
        "lilyshark_deck": src == LILYSHARK_NODE_NUM,
        "ciphertext": payload[MESHTASTIC_HEADER_LEN:],
    }


def _aes_ctr(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """AES-128-CTR. Meshtastic's 16-byte nonce ends in a 4-byte counter at 0."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = nonce if len(nonce) >= 16 else nonce.ljust(16, b"\x00")
    encryptor = Cipher(algorithms.AES(key), modes.CTR(iv[:16])).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def _meshtastic_nonce(from_node: int, packet_id: int) -> bytes:
    nonce = bytearray(16)
    nonce[0:4] = packet_id.to_bytes(4, "little")
    nonce[8:12] = from_node.to_bytes(4, "little")
    return bytes(nonce)


def _read_varint(buf: bytes, cursor: int) -> tuple[int, int] | None:
    result = 0
    shift = 0
    while cursor < len(buf):
        byte = buf[cursor]
        cursor += 1
        if shift > 28:
            return None
        result |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return result, cursor
        shift += 7
    return None


def _skip_field(buf: bytes, cursor: int, wire: int) -> int | None:
    if wire == 0:
        got = _read_varint(buf, cursor)
        return None if got is None else got[1]
    if wire == 1:
        return None if cursor + 8 > len(buf) else cursor + 8
    if wire == 2:
        got = _read_varint(buf, cursor)
        if got is None:
            return None
        size, cursor = got
        if size > len(buf) - cursor:
            return None
        return cursor + size
    if wire == 5:
        return None if cursor + 4 > len(buf) else cursor + 4
    return None


def _iter_fields(buf: bytes):
    cursor = 0
    while cursor < len(buf):
        got = _read_varint(buf, cursor)
        if got is None:
            return
        tag, cursor = got
        field, wire = tag >> 3, tag & 0x07
        if field == 0:
            return
        start = cursor
        nxt = _skip_field(buf, cursor, wire)
        if nxt is None:
            return
        yield field, wire, buf[start:nxt], nxt
        cursor = nxt


def _parse_position(buf: bytes) -> tuple[float, float] | None:
    lat = lon = None
    cursor = 0
    while cursor < len(buf):
        got = _read_varint(buf, cursor)
        if got is None:
            return None
        tag, cursor = got
        field, wire = tag >> 3, tag & 0x07
        if field == 1 and wire == 5:
            if cursor + 4 > len(buf):
                return None
            lat = struct.unpack_from("<i", buf, cursor)[0] * 1e-7
            cursor += 4
        elif field == 2 and wire == 5:
            if cursor + 4 > len(buf):
                return None
            lon = struct.unpack_from("<i", buf, cursor)[0] * 1e-7
            cursor += 4
        else:
            nxt = _skip_field(buf, cursor, wire)
            if nxt is None:
                return None
            cursor = nxt
    if lat is None or lon is None:
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon


def _copy_ascii_name(buf: bytes, cap: int) -> str | None:
    if not buf or len(buf) > cap:
        return None
    if any(b < 0x20 or b >= 0x7F for b in buf):
        return None
    return buf.decode("ascii")


def _parse_user(buf: bytes) -> dict:
    out: dict = {}
    cursor = 0
    while cursor < len(buf):
        got = _read_varint(buf, cursor)
        if got is None:
            return out
        tag, cursor = got
        field, wire = tag >> 3, tag & 0x07
        if wire == 2:
            size_got = _read_varint(buf, cursor)
            if size_got is None:
                return out
            size, cursor = size_got
            if size > len(buf) - cursor:
                return out
            chunk = buf[cursor:cursor + size]
            cursor += size
            if field == 2:
                name = _copy_ascii_name(chunk, 39)
                if name:
                    out["long_name"] = name
            elif field == 3:
                name = _copy_ascii_name(chunk, 7)
                if name:
                    out["short_name"] = name
        else:
            nxt = _skip_field(buf, cursor, wire)
            if nxt is None:
                return out
            cursor = nxt
    return out


def _parse_nodeinfo(buf: bytes) -> dict:
    out: dict = {}
    cursor = 0
    while cursor < len(buf):
        got = _read_varint(buf, cursor)
        if got is None:
            return out
        tag, cursor = got
        field, wire = tag >> 3, tag & 0x07
        if wire == 2:
            size_got = _read_varint(buf, cursor)
            if size_got is None:
                return out
            size, cursor = size_got
            if size > len(buf) - cursor:
                return out
            chunk = buf[cursor:cursor + size]
            cursor += size
            if field == 2:
                out.update(_parse_user(chunk))
            elif field == 4:
                pos = _parse_position(chunk)
                if pos:
                    out["lat"], out["lon"] = pos
        else:
            nxt = _skip_field(buf, cursor, wire)
            if nxt is None:
                return out
            cursor = nxt
    return out


def parse_meshtastic_data(plain: bytes) -> dict | None:
    """Strict Data protobuf. Wrong-key noise must not become a message."""
    if not plain:
        return None
    portnum = None
    payload = b""
    cursor = 0
    while cursor < len(plain):
        got = _read_varint(plain, cursor)
        if got is None:
            return None
        tag, cursor = got
        field, wire = tag >> 3, tag & 0x07
        if field == 0:
            return None
        if wire == 0:
            val_got = _read_varint(plain, cursor)
            if val_got is None:
                return None
            value, cursor = val_got
            if field == 1:
                if value > 0xFFFF:
                    return None
                portnum = value
        elif wire == 2:
            size_got = _read_varint(plain, cursor)
            if size_got is None:
                return None
            size, cursor = size_got
            if size > len(plain) - cursor:
                return None
            if field == 2:
                payload = plain[cursor:cursor + size]
            cursor += size
        else:
            # Data has no fixed32/64 fields.
            return None
    if portnum is None:
        return None
    out: dict = {"portnum": portnum, "port_label": PORT_LABELS.get(portnum, f"PORT {portnum}")}
    if portnum == 3 and payload:
        pos = _parse_position(payload)
        if pos:
            out["lat"], out["lon"] = pos
    elif portnum == 4 and payload:
        out.update(_parse_nodeinfo(payload))
    return out


def try_meshtastic_default_payload(header: dict) -> dict | None:
    """Apply the published default PSK. Real channel keys stay opaque."""
    cipher = header.get("ciphertext") or b""
    if not cipher or len(cipher) > 256:
        return None
    try:
        nonce = _meshtastic_nonce(header["from_node"], header["packet_id"])
        plain = _aes_ctr(MESHTASTIC_DEFAULT_PSK, nonce, cipher)
    except (ValueError, TypeError):
        return None
    return parse_meshtastic_data(plain)


def parse_meshcore(payload: bytes) -> dict | None:
    """v1 MeshCore header; advertisement bodies yield public identity + GPS."""
    if not payload:
        return None
    header = payload[0]
    route = header & 0x03
    ptype = (header >> 2) & 0x0F
    version = (header >> 6) & 0x03
    if version != 0:
        return {"kind": "future", "payload_type": ptype}
    cursor = 1
    if route in (0, 3):  # transport flood / transport direct
        if len(payload) - cursor < 4:
            return None
        cursor += 4
    if cursor >= len(payload):
        return None
    encoded_path = payload[cursor]
    cursor += 1
    path_count = encoded_path & 0x3F
    path_size = (encoded_path >> 6) + 1
    path_bytes = path_count * path_size
    if path_size == 4 or path_bytes > 64 or path_bytes > len(payload) - cursor:
        return None
    cursor += path_bytes
    body = payload[cursor:]
    out: dict = {
        "kind": MESHCORE_KINDS.get(ptype, f"type_{ptype}"),
        "payload_type": ptype,
    }
    if ptype == 4:
        advert = _parse_meshcore_advert(body)
        if advert is None:
            return None
        out.update(advert)
    return out


def _parse_meshcore_advert(body: bytes) -> dict | None:
    # key(32) + timestamp(4) + signature(64) = 100; app data optional after.
    if len(body) < 100:
        return None
    pubkey = body[0:32].hex()
    out: dict = {"pubkey": pubkey, "identity": pubkey}
    app = body[100:]
    if not app:
        return out
    flags = app[0]
    cursor = 1
    if flags & 0x10:
        if cursor + 8 > len(app):
            return out
        lat = struct.unpack_from("<i", app, cursor)[0] / 1e6
        lon = struct.unpack_from("<i", app, cursor + 4)[0] / 1e6
        cursor += 8
        if abs(lat) <= 90 and abs(lon) <= 180 and not (lat == 0 and lon == 0):
            out["lat"], out["lon"] = lat, lon
    if flags & 0x20:
        cursor += 2
    if flags & 0x40:
        cursor += 2
    if flags & 0x80 and cursor < len(app):
        name = app[cursor:].decode("utf-8", errors="replace").strip("\x00")
        if name:
            out["long_name"] = name[:40]
    return out


def parse_reticulum(payload: bytes) -> dict | None:
    """RNode shim + clear RNS header: destination-hash prefix only."""
    if len(payload) < 2:
        return None
    shim = payload[0]
    if shim & 0x01:  # split frame
        return {"kind": "split"}
    flags = payload[1]
    if flags & 0x80:  # IFAC-protected
        return {"kind": "ifac"}
    header_two = bool(flags & 0x40)
    logical = 35 if header_two else 19
    if len(payload) < 1 + logical:
        return None
    dest_off = 1 + 2 + (16 if header_two else 0)  # shim + hops, then optional transport
    # Physical layout: [shim][flags][hops][transport? 16][dest 16][context]
    dest_off = 3 + (16 if header_two else 0)
    if dest_off + 4 > len(payload):
        return None
    prefix = payload[dest_off:dest_off + 4].hex()
    packet_type = flags & 0x03
    kinds = {0: "data", 1: "announce", 2: "link_request", 3: "proof"}
    return {
        "kind": kinds.get(packet_type, "rns"),
        "dest_prefix": prefix,
        "identity": prefix,
    }


def classify_frame(frame: dict) -> dict:
    """Attach protocol identity fields used by load_lscap / heard()."""
    protocol = protocol_for_frame(frame)
    profile = PROFILES.get(int(frame.get("profile_id") or 0), {})
    row = {
        "protocol": protocol,
        "profile_name": profile.get("name"),
        "from_node": None,
        "from_bang": None,
        "to_node": None,
        "lilyshark_deck": False,
        "portnum": None,
        "long_name": None,
        "short_name": None,
        "lat": None,
        "lon": None,
        "default_key": False,
        "meshcore_pubkey": None,
        "meshcore_kind": None,
        "reticulum_dest": None,
        "identity": None,
        "hop_start": None,
        "hop_limit": None,
        "hop_direct": False,
        "channel_hash": None,
        "via_mqtt": False,
    }
    payload = frame["payload"]
    if protocol == "meshtastic":
        hdr = parse_meshtastic_header(payload)
        if hdr:
            row.update({
                "from_node": hdr["from_node"],
                "from_bang": hdr["from_bang"],
                "to_node": hdr["to_node"],
                "lilyshark_deck": hdr["lilyshark_deck"],
                "identity": hdr["from_bang"],
                "hop_start": hdr["hop_start"],
                "hop_limit": hdr["hop_limit"],
                "hop_direct": hdr["hop_start"] == hdr["hop_limit"],
                "channel_hash": hdr["channel_hash"],
                "via_mqtt": hdr["via_mqtt"],
            })
            decoded = try_meshtastic_default_payload(hdr)
            if decoded:
                row["default_key"] = True
                row["portnum"] = decoded.get("portnum")
                row["long_name"] = decoded.get("long_name")
                row["short_name"] = decoded.get("short_name")
                row["lat"] = decoded.get("lat")
                row["lon"] = decoded.get("lon")
                parsed = parse_lilyshark_name(row["long_name"])
                if parsed:
                    row["lilyshark_deck"] = True
                    if parsed.get("short") and not row["short_name"]:
                        row["short_name"] = parsed["short"]
    elif protocol == "meshcore":
        mc = parse_meshcore(payload)
        if mc:
            row["meshcore_kind"] = mc.get("kind")
            row["meshcore_pubkey"] = mc.get("pubkey")
            row["identity"] = mc.get("identity")
            row["long_name"] = mc.get("long_name")
            row["lat"] = mc.get("lat")
            row["lon"] = mc.get("lon")
            parsed = parse_lilyshark_name(row["long_name"])
            if parsed:
                row["lilyshark_deck"] = True
                if parsed.get("short") and not row["short_name"]:
                    row["short_name"] = parsed["short"]
    elif protocol == "reticulum":
        rn = parse_reticulum(payload)
        if rn:
            row["reticulum_dest"] = rn.get("dest_prefix")
            row["identity"] = rn.get("identity")
            row["meshcore_kind"] = rn.get("kind")
    return row


FRAMES_DDL = """
CREATE TABLE IF NOT EXISTS lscap.frames (
    capture_file VARCHAR,
    frame_index INTEGER,
    sequence BIGINT,
    timestamp_us BIGINT,
    freq_hz BIGINT,
    bandwidth_hz BIGINT,
    rssi_dbm DOUBLE,
    snr_db DOUBLE,
    spreading_factor INTEGER,
    crc_state INTEGER,
    synthetic BOOLEAN,
    profile_id INTEGER,
    protocol VARCHAR,
    from_node BIGINT,
    from_bang VARCHAR,
    to_node BIGINT,
    lilyshark_deck BOOLEAN,
    portnum INTEGER,
    long_name VARCHAR,
    short_name VARCHAR,
    lat DOUBLE,
    lon DOUBLE,
    default_key BOOLEAN,
    meshcore_pubkey VARCHAR,
    meshcore_kind VARCHAR,
    reticulum_dest VARCHAR,
    identity VARCHAR,
    direction INTEGER,
    present_fields INTEGER,
    original_length INTEGER,
    captured_length INTEGER,
    hop_start INTEGER,
    hop_limit INTEGER,
    hop_direct BOOLEAN,
    channel_hash INTEGER,
    payload_sha256 VARCHAR,
    witness_key VARCHAR,
    witness_reason VARCHAR,
    unix_seconds BIGINT,
    via_mqtt BOOLEAN,
    net_relayed BOOLEAN
)
"""

_FRAME_COLS = 41
_REQUIRED_COLS = {"identity", "direction", "payload_sha256", "witness_key",
                  "hop_direct", "via_mqtt", "net_relayed"}


def _file_ticks_per_second(path: Path) -> int:
    data = path.read_bytes()[:FILE_HEADER_SIZE]
    if len(data) < FILE_HEADER_SIZE:
        return 1_000_000
    _magic, _maj, _min, _hs, _rs, _flags, ticks, _reserved = FILE_HEADER.unpack(data)
    return ticks or 1_000_000


def load_lscap(db_path: Path, path: Path, epoch: int | None = None) -> int:
    """Load .lscap frames with protocol identity into lscap.frames.

    `epoch` is unix seconds of capture tick 0 (GPS wall-clock anchor). When
    omitted, a `<capture>.witness` sidecar next to the file supplies it and
    any firmware-derived witness keys.
    """
    sidecar = None
    for candidate in sidecar_paths(path):
        if candidate.exists():
            sidecar = read_witness_sidecar(candidate)
            if sidecar:
                break
    if epoch is None and sidecar:
        epoch = sidecar["epoch"]
    keys_by_seq = sidecar["keys"] if sidecar else {}
    ticks = _file_ticks_per_second(path)
    return write_frames(
        db_path, path.name, iter_frames(path),
        epoch=epoch, ticks=ticks, keys_by_seq=keys_by_seq,
    )


def write_frames(
    db_path: Path,
    capture_file: str,
    frames: list[dict],
    epoch: int | None = None,
    ticks: int = 1_000_000,
    keys_by_seq: dict[int, str] | None = None,
) -> int:
    """Insert classified frames. Replaces any previous rows for this file.

    Per-frame `unix_seconds` (live USB host clock) wins over tick+epoch.
    Undated logs leave unix_seconds NULL — do not guess.
    """
    import duckdb

    keys_by_seq = keys_by_seq or {}
    rows = []
    for f in frames:
        info = classify_frame(f)
        payload_sha = hashlib.sha256(f["payload"]).hexdigest() if f["payload"] else None
        unix = f.get("unix_seconds")
        if unix is None and epoch is not None:
            unix = epoch + int(f.get("timestamp_us") or 0) // ticks
        has_clock = unix is not None
        reason = frame_ineligibility(f, has_wall_clock=has_clock)
        wkey = keys_by_seq.get(int(f["sequence"]))
        if wkey:
            reason = None
        elif reason is None and unix is not None:
            wkey = witness_key(f["payload"], int(f["freq_hz"]), unix).hex()
        rows.append((
            capture_file, f["index"], f["sequence"], f["timestamp_us"],
            f["freq_hz"], f["bandwidth_hz"], f["rssi_dbm"], f["snr_db"],
            f["spreading_factor"], f["crc_state"], f["synthetic"],
            f["profile_id"], info["protocol"],
            info["from_node"], info["from_bang"], info["to_node"],
            info["lilyshark_deck"], info["portnum"], info["long_name"],
            info["short_name"], info["lat"], info["lon"], info["default_key"],
            info["meshcore_pubkey"], info["meshcore_kind"],
            info["reticulum_dest"], info["identity"],
            f["direction"], f["present_fields"], f["original_length"],
            f["captured_length"], info["hop_start"], info["hop_limit"],
            info["hop_direct"], info["channel_hash"],
            payload_sha, wkey, reason, unix,
            info["via_mqtt"], f["net_relayed"],
        ))
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS lscap")
        existing = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='lscap' AND table_name='frames'"
        ).fetchall()
        have = {c[0] for c in existing}
        if existing and not _REQUIRED_COLS <= have:
            con.execute("DROP TABLE lscap.frames")
        con.execute(FRAMES_DDL)
        con.execute("DELETE FROM lscap.frames WHERE capture_file = ?",
                    [capture_file])
        if rows:
            placeholders = ",".join(["?"] * _FRAME_COLS)
            con.executemany(
                f"INSERT INTO lscap.frames VALUES ({placeholders})", rows
            )
    finally:
        con.close()
    return len(rows)


def heard(con, capture_file: str | None = None) -> list[dict]:
    """Unique LoRa identities in imported .lscap, joined to mesh + licenses.

    Also joins Field BLE `Lilyshark XXXX` names (node-number suffix), the
    capturing deck's TX frames, USB `LSK T` GPS, and witness/payload
    corroboration across captures.
    """
    from .queries import _has_table, _haversine_expr, _rows

    if not _has_table(con, "lscap", "frames"):
        return []
    has_direction = _column_in(con, "lscap", "frames", "direction")
    has_hygiene = _column_in(con, "lscap", "frames", "net_relayed")
    # On-air: this deck received the frame over LoRa. TX, simulator, bad CRC,
    # USB/MQTT injection, and net-relayed copies are stored but are not heard.
    if has_hygiene:
        on_air = (
            "(direction = 1 AND (synthetic IS NULL OR synthetic = false) "
            "AND (crc_state IS NULL OR crc_state = 2) "
            "AND COALESCE(net_relayed, false) = false "
            "AND COALESCE(via_mqtt, false) = false)"
        )
        where = f"WHERE identity IS NOT NULL AND (direction = 2 OR {on_air})"
    else:
        on_air = "true"
        where = "WHERE identity IS NOT NULL"
    params: list = []
    if capture_file:
        where += " AND capture_file = ?"
        params.append(capture_file)
    extra = ""
    if has_direction:
        extra = f"""
               sum(CASE WHEN {on_air} THEN 1 ELSE 0 END) AS rx_frames,
               sum(CASE WHEN direction = 2 THEN 1 ELSE 0 END) AS tx_frames,
               sum(CASE WHEN hop_direct AND {on_air} THEN 1 ELSE 0 END)
                   AS direct_frames,
               count(DISTINCT witness_key) FILTER (
                   WHERE witness_key IS NOT NULL AND {on_air}) AS witness_keys,
               count(DISTINCT payload_sha256) FILTER (
                   WHERE payload_sha256 IS NOT NULL AND {on_air})
                   AS payload_hashes,
               bool_or(direction = 2) AS self_tx
        """
    else:
        extra = """
               count(*) AS rx_frames,
               0 AS tx_frames,
               0 AS direct_frames,
               0 AS witness_keys,
               0 AS payload_hashes,
               false AS self_tx
        """
    transmitters = _rows(con, f"""
        SELECT identity,
               any_value(protocol) AS protocol,
               any_value(from_node) AS from_node,
               any_value(from_bang) AS from_bang,
               bool_or(lilyshark_deck) AS lilyshark_deck,
               count(*) AS frames,
               min(rssi_dbm) FILTER (WHERE rssi_dbm IS NOT NULL AND {on_air})
                   AS rssi_min,
               max(rssi_dbm) FILTER (WHERE rssi_dbm IS NOT NULL AND {on_air})
                   AS rssi_max,
               any_value(freq_hz) AS freq_hz,
               any_value(profile_id) AS profile_id,
               bool_or(default_key) AS default_key,
               arg_max(long_name, CASE WHEN long_name IS NOT NULL THEN 1 ELSE 0 END)
                   AS long_name,
               arg_max(short_name, CASE WHEN short_name IS NOT NULL THEN 1 ELSE 0 END)
                   AS short_name,
               arg_max(lat, CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END) AS lat,
               arg_max(lon, CASE WHEN lon IS NOT NULL THEN 1 ELSE 0 END) AS lon,
               any_value(meshcore_pubkey) AS meshcore_pubkey,
               any_value(reticulum_dest) AS reticulum_dest,
               {extra}
        FROM lscap.frames {where}
        GROUP BY identity
        ORDER BY frames DESC
    """, params)

    mesh_by_alias: dict[str, dict] = {}
    mesh_by_prefix: list[tuple[str, dict]] = []
    if _has_table(con, "mesh", "nodes"):
        for n in _rows(con, "SELECT * FROM mesh.nodes"):
            nid = str(n["node_id"]).lower()
            mesh_by_alias[nid] = n
            mesh_by_alias[nid.lstrip("!")] = n
            mesh_by_prefix.append((nid, n))

    ble_by_short = _ble_lilyshark_positions(con)
    decks = capturing_decks(con, capture_file)
    deck_ids = {d["identity"] for d in decks if d.get("identity")}
    deck_shorts = {d["short"] for d in decks if d.get("short")}
    lsk_by_bang = {d["from_bang"]: d for d in decks
                   if d.get("from_bang") and d.get("position_via") == "lsk-t"}
    lsk_by_short = {d["short"]: d for d in decks
                    if d.get("short") and d.get("lat") is not None
                    and d.get("position_via") == "lsk-t"}
    sensor = next((d for d in decks if d.get("lat") is not None), None)
    witnesses = _witness_counts(con)

    has_sites = _has_table(con, "uls", "sites") and _has_table(con, "uls", "licenses")
    has_towers = _has_table(con, "asr", "towers")

    for t in transmitters:
        t["band"] = band_for_hz(t.get("freq_hz"))
        t["profile_name"] = PROFILES.get(int(t["profile_id"] or 0), {}).get("name")
        t["role"] = "tx" if t.get("self_tx") and not t.get("rx_frames") else (
            "tx+rx" if t.get("self_tx") else "rx"
        )
        match = _mesh_match(t, mesh_by_alias, mesh_by_prefix)
        t["mesh_name"] = match.get("name") if match else None
        t["mesh_source"] = match.get("source") if match else None
        t["mesh_lat"] = match.get("lat") if match else None
        t["mesh_lon"] = match.get("lon") if match else None
        parsed = parse_lilyshark_name(t.get("long_name") or t.get("short_name"))
        if parsed and parsed.get("short"):
            t["lilyshark_short"] = parsed["short"]
        elif t.get("from_node") is not None:
            t["lilyshark_short"] = f"{int(t['from_node']) & 0xFFFF:04X}"
        else:
            t["lilyshark_short"] = None
        t["bang_mask"] = bang_mask(t.get("lilyshark_short"))
        ble = None
        short = t.get("lilyshark_short")
        if short:
            ble = ble_by_short.get(short.upper())
        if ble:
            t["ble_name"] = ble["name"]
            t["ble_rssi"] = ble["rssi"]
            t["ble_lat"] = ble["lat"]
            t["ble_lon"] = ble["lon"]
        else:
            t["ble_name"] = t["ble_rssi"] = t["ble_lat"] = t["ble_lon"] = None
        t["capturing_deck"] = (
            t.get("identity") in deck_ids
            or t.get("self_tx")
            or (short and short.upper() in deck_shorts)
        )
        lsk = None
        if t.get("identity"):
            lsk = lsk_by_bang.get(t["identity"])
        if lsk is None and short:
            lsk = lsk_by_short.get(short.upper())
        if t.get("lat") is not None:
            t["position_via"] = "payload"
        elif t["capturing_deck"] and lsk and lsk.get("lat") is not None:
            t["lat"] = lsk["lat"]
            t["lon"] = lsk["lon"]
            t["position_via"] = "lsk-t"
        elif match:
            t["lat"] = match.get("lat")
            t["lon"] = match.get("lon")
            t["position_via"] = "mesh.nodes"
        elif ble and ble.get("lat") is not None:
            t["lat"] = ble["lat"]
            t["lon"] = ble["lon"]
            t["position_via"] = "field-ble"
        else:
            t["position_via"] = None
        if t["capturing_deck"] or t.get("lilyshark_deck") or (
            parsed is not None
        ) or (t.get("long_name") or t.get("mesh_name") or "").lower().startswith(
            "lilyshark"
        ):
            t["gadget"] = "Lilyshark T-Deck"
            t["lilyshark_deck"] = True
        else:
            t["gadget"] = None
        t["witnesses"] = witnesses.get(t.get("identity") or "", 0)
        t["sensor_lat"] = sensor["lat"] if sensor else None
        t["sensor_lon"] = sensor["lon"] if sensor else None
        t["licensed_nearby"] = []
        t["nearest_call"] = None
        t["nearest_entity"] = None
        t["nearest_km"] = None
        t["nearest_tower_km"] = None
        t["nearest_tower_owner"] = None
        lat, lon = t.get("lat"), t.get("lon")
        if lat is None or lon is None:
            continue
        if has_sites:
            dist = _haversine_expr(str(lat), str(lon), "s")
            nearby = con.execute(
                f"""
                SELECT {dist} AS dist_km, s.call_sign, l.entity_name,
                       l.radio_service_code
                FROM uls.sites s JOIN uls.licenses l
                  USING (unique_system_identifier)
                WHERE s.lat BETWEEN {lat - 0.02} AND {lat + 0.02}
                  AND s.lon BETWEEN {lon - 0.025} AND {lon + 0.025}
                ORDER BY dist_km LIMIT 3
                """
            ).fetchall()
            t["licensed_nearby"] = nearby
            if nearby:
                t["nearest_km"] = round(nearby[0][0], 3)
                t["nearest_call"] = nearby[0][1]
                t["nearest_entity"] = nearby[0][2]
        if has_towers:
            dist = _haversine_expr(str(lat), str(lon), "t")
            row = con.execute(
                f"""
                SELECT {dist} AS dist_km, t.owner_name
                FROM asr.towers t
                WHERE t.lat BETWEEN {lat - 0.05} AND {lat + 0.05}
                  AND t.lon BETWEEN {lon - 0.06} AND {lon + 0.06}
                ORDER BY dist_km LIMIT 1
                """
            ).fetchone()
            if row:
                t["nearest_tower_km"] = round(row[0], 3)
                t["nearest_tower_owner"] = row[1]
    return transmitters


def capturing_decks(con, capture_file: str | None = None) -> list[dict]:
    """The T-Deck that wrote each capture: TX frames, BLE name, USB LSK T."""
    from .queries import _has_table, _rows
    from .lsk import apply_lsk_gps, lsk_decks

    decks: list[dict] = []
    if _has_table(con, "lscap", "frames") and _column_in(
        con, "lscap", "frames", "direction"
    ):
        where = "WHERE direction = 2 AND identity IS NOT NULL"
        params: list = []
        if capture_file:
            where += " AND capture_file = ?"
            params.append(capture_file)
        decks = _rows(con, f"""
            SELECT capture_file,
                   identity,
                   any_value(from_node) AS from_node,
                   any_value(from_bang) AS from_bang,
                   arg_max(lat, CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END)
                       AS lat,
                   arg_max(lon, CASE WHEN lon IS NOT NULL THEN 1 ELSE 0 END)
                       AS lon,
                   count(*) AS tx_frames
            FROM lscap.frames {where}
            GROUP BY capture_file, identity
        """, params)
    ble = _ble_lilyshark_positions(con)
    for d in decks:
        short = (
            f"{int(d['from_node']) & 0xFFFF:04X}"
            if d.get("from_node") is not None else None
        )
        d["short"] = short
        d["bang_mask"] = bang_mask(short)
        d["gadget"] = "Lilyshark T-Deck"
        hit = ble.get(short) if short else None
        if hit:
            d["ble_name"] = hit["name"]
            if d.get("lat") is None:
                d["lat"] = hit["lat"]
                d["lon"] = hit["lon"]
                d["position_via"] = "field-ble"
            else:
                d["position_via"] = "payload"
        else:
            d["ble_name"] = None
            d["position_via"] = "payload" if d.get("lat") is not None else None
    if not decks:
        # No TX frames: still join BLE `Lilyshark XXXX` as the nearby deck.
        decks = [
            {
                "capture_file": None,
                "identity": None,
                "from_node": None,
                "from_bang": None,
                "bang_mask": v.get("bang_mask") or bang_mask(short),
                "lat": v.get("lat"),
                "lon": v.get("lon"),
                "tx_frames": 0,
                "short": short,
                "gadget": "Lilyshark T-Deck",
                "ble_name": v["name"],
                "position_via": "field-ble",
            }
            for short, v in ble.items()
        ]
    return apply_lsk_gps(decks, lsk_decks(con))


def corroborate(con) -> list[dict]:
    """Same over-the-air frame heard in two or more .lscap files.

    Prefers Field Receipts witness keys (payload + rounded freq + minute
    bucket). Falls back to payload SHA-256 when no wall-clock anchor exists.
    TX frames are excluded — a deck does not witness its own beacon.
    """
    from .queries import _has_table, _rows

    if not _has_table(con, "lscap", "frames"):
        return []
    if not _column_in(con, "lscap", "frames", "payload_sha256"):
        return []
    hygiene = "AND COALESCE(synthetic, false) = false"
    if _column_in(con, "lscap", "frames", "net_relayed"):
        hygiene += (
            " AND COALESCE(net_relayed, false) = false"
            " AND COALESCE(via_mqtt, false) = false"
            " AND (crc_state IS NULL OR crc_state = 2)"
        )
    by_key = _rows(con, f"""
        SELECT 'witness' AS via, witness_key AS key,
               count(DISTINCT capture_file) AS decks,
               count(*) AS frames,
               array_agg(DISTINCT capture_file) AS captures,
               array_agg(DISTINCT identity) AS identities
        FROM lscap.frames
        WHERE witness_key IS NOT NULL
          AND (direction IS NULL OR direction != 2)
          {hygiene}
        GROUP BY witness_key
        HAVING count(DISTINCT capture_file) >= 2
    """)
    if by_key:
        return by_key
    return _rows(con, f"""
        SELECT 'payload' AS via, payload_sha256 AS key,
               count(DISTINCT capture_file) AS decks,
               count(*) AS frames,
               array_agg(DISTINCT capture_file) AS captures,
               array_agg(DISTINCT identity) AS identities
        FROM lscap.frames
        WHERE payload_sha256 IS NOT NULL
          AND (direction IS NULL OR direction != 2)
          {hygiene}
        GROUP BY payload_sha256
        HAVING count(DISTINCT capture_file) >= 2
    """)


def _column_in(con, schema: str, table: str, column: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? AND column_name = ?",
            [schema, table, column],
        ).fetchone()[0]
    )


def _ble_lilyshark_positions(con) -> dict[str, dict]:
    """Field / WiGLE BLE rows whose advertised name is `Lilyshark XXXX`."""
    from .queries import _has_table, _rows

    if not _has_table(con, "capture", "observations"):
        return {}
    out: dict[str, dict] = {}
    for row in _rows(con, """
        SELECT ssid, bssid, rssi, lat, lon, auth_mode
        FROM capture.observations
        WHERE ssid IS NOT NULL
    """):
        parsed = parse_lilyshark_name(row.get("ssid"))
        if not parsed or not parsed.get("short"):
            continue
        short = parsed["short"]
        prev = out.get(short)
        if prev is None or (row.get("rssi") or -999) > (prev.get("rssi") or -999):
            out[short] = {
                "name": parsed["name"],
                "short": short,
                "rssi": row.get("rssi"),
                "lat": row.get("lat"),
                "lon": row.get("lon"),
                "bssid": row.get("bssid"),
                "bang_mask": parsed.get("bang_mask"),
            }
    return out


def _witness_counts(con) -> dict[str, int]:
    """How many other captures share a witness/payload key with each identity."""
    from .queries import _has_table

    if not _has_table(con, "lscap", "frames"):
        return {}
    if not _column_in(con, "lscap", "frames", "payload_sha256"):
        return {}
    counts: dict[str, int] = {}
    for row in corroborate(con):
        for ident in row.get("identities") or []:
            if ident:
                counts[ident] = counts.get(ident, 0) + int(row["decks"]) - 1
    return counts


def _mesh_match(t: dict, by_alias: dict, by_prefix: list) -> dict | None:
    protocol = t.get("protocol")
    if protocol == "meshtastic" and t.get("from_node") is not None:
        for a in node_id_aliases(int(t["from_node"])):
            hit = by_alias.get(a.lower()) or by_alias.get(a.lower().lstrip("!"))
            if hit:
                return hit
    if protocol == "meshcore" and t.get("meshcore_pubkey"):
        pk = t["meshcore_pubkey"].lower()
        hit = by_alias.get(pk)
        if hit:
            return hit
        for nid, n in by_prefix:
            if nid == pk or pk.startswith(nid) or nid.startswith(pk):
                return n
    if protocol == "reticulum" and t.get("reticulum_dest"):
        prefix = t["reticulum_dest"].lower()
        for nid, n in by_prefix:
            if nid.startswith(prefix) or prefix.startswith(nid[:8]):
                return n
    identity = (t.get("identity") or "").lower()
    if identity:
        return by_alias.get(identity) or by_alias.get(identity.lstrip("!"))
    return None


def node_id_aliases(node_num: int) -> list[str]:
    """Forms mesh.nodes.node_id might take for this Meshtastic node number."""
    hex8 = f"{node_num:08x}"
    return [
        str(node_num),
        hex8,
        hex8.upper(),
        f"!{hex8}",
        f"!{hex8.upper()}",
        f"!{node_num:x}",
    ]
