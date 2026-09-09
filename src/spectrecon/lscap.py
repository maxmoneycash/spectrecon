"""Lilyshark .lscap reader and Meshtastic outer-header parse.

`.lscap` is Lilyshark's protocol-neutral LoRa capture (docs at
lilyshark.com / docs/lilyshark-capture-format.md). Layout is little-endian
v1.1: 24-byte file header (LSCP) then 80-byte records (LSFR) + payload.

Meshtastic's 16-byte outer radio header is unencrypted (to, from, id, flags,
channel hash). That is enough to join a heard transmitter to mesh.nodes
without touching the payload cipher.
"""

from __future__ import annotations

import struct
from pathlib import Path

FILE_MAGIC = b"LSCP"
RECORD_MAGIC = b"LSFR"
FILE_HEADER_SIZE = 24
RECORD_HEADER_SIZE = 80
MAX_CAPTURED_LENGTH = 255
SYNTHETIC_FLAG = 1 << 2

FILE_HEADER = struct.Struct("<4sHHHHIII")
RECORD_HEADER = struct.Struct("<4sHHHHQQIIIIIIihhHHHhbBBBBBBBB3s")

# Firmware identity on Meshtastic LongFast (meshtastic_encode.h).
LILYSHARK_NODE_NUM = 0x4C534B01

MESHTASTIC_HEADER_LEN = 16
BROADCAST = 0xFFFFFFFF


class LscapError(ValueError):
    pass


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
         _present, freq_hz, bw_hz, _bitrate, _fdev, airtime_us, _ferr,
         rssi_x10, snr_x10, _preamble, _sync, profile_id, _rstat, _txpwr,
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
            "rssi_dbm": rssi_x10 / 10.0,
            "snr_db": snr_x10 / 10.0,
            "profile_id": profile_id,
            "spreading_factor": sf,
            "coding_rate": cr,
            "modulation": modulation,
            "direction": direction,
            "crc_state": crc,
            "synthetic": bool(meta & SYNTHETIC_FLAG),
            "payload": payload,
            "original_length": original,
        })
        offset = payload_off + captured
        index += 1
    return frames


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
    }


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
    from_node BIGINT,
    from_bang VARCHAR,
    to_node BIGINT,
    lilyshark_deck BOOLEAN
)
"""


def load_lscap(db_path: Path, path: Path) -> int:
    """Load .lscap frames + Meshtastic from-nodes into lscap.frames."""
    import duckdb

    frames = iter_frames(path)
    rows = []
    for f in frames:
        hdr = parse_meshtastic_header(f["payload"])
        rows.append((
            path.name, f["index"], f["sequence"], f["timestamp_us"],
            f["freq_hz"], f["bandwidth_hz"], f["rssi_dbm"], f["snr_db"],
            f["spreading_factor"], f["crc_state"], f["synthetic"],
            hdr["from_node"] if hdr else None,
            hdr["from_bang"] if hdr else None,
            hdr["to_node"] if hdr else None,
            hdr["lilyshark_deck"] if hdr else False,
        ))
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS lscap")
        con.execute(FRAMES_DDL)
        con.execute("DELETE FROM lscap.frames WHERE capture_file = ?", [path.name])
        con.executemany(
            "INSERT INTO lscap.frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    finally:
        con.close()
    return len(rows)


def heard(con, capture_file: str | None = None) -> list[dict]:
    """Unique Meshtastic transmitters in imported .lscap, joined to mesh.nodes."""
    from .queries import _has_table, _rows

    if not _has_table(con, "lscap", "frames"):
        return []
    where = "WHERE from_node IS NOT NULL"
    params: list = []
    if capture_file:
        where += " AND capture_file = ?"
        params.append(capture_file)
    transmitters = _rows(con, f"""
        SELECT from_node, any_value(from_bang) AS from_bang,
               bool_or(lilyshark_deck) AS lilyshark_deck,
               count(*) AS frames,
               min(rssi_dbm) AS rssi_min, max(rssi_dbm) AS rssi_max,
               any_value(freq_hz) AS freq_hz
        FROM lscap.frames {where}
        GROUP BY from_node
        ORDER BY frames DESC
    """, params)
    mesh_by_alias: dict[str, dict] = {}
    if _has_table(con, "mesh", "nodes"):
        for n in _rows(con, "SELECT * FROM mesh.nodes"):
            mesh_by_alias[str(n["node_id"]).lower()] = n
            mesh_by_alias[str(n["node_id"]).lower().lstrip("!")] = n
    for t in transmitters:
        aliases = [a.lower() for a in node_id_aliases(int(t["from_node"]))]
        match = None
        for a in aliases:
            match = mesh_by_alias.get(a) or mesh_by_alias.get(a.lstrip("!"))
            if match:
                break
        t["mesh_name"] = match.get("name") if match else None
        t["mesh_source"] = match.get("source") if match else None
        t["mesh_lat"] = match.get("lat") if match else None
        t["mesh_lon"] = match.get("lon") if match else None
        if t["lilyshark_deck"]:
            t["gadget"] = "Lilyshark T-Deck"
        elif match and (match.get("name") or "").lower().startswith("lilyshark"):
            t["gadget"] = "Lilyshark T-Deck"
        else:
            t["gadget"] = None
    return transmitters


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
