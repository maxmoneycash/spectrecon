"""Lilyshark analyzer-link (LSK) reader — USB CDC, not BLE.

The T-Deck streams newline-delimited `LSK <kind> {json}` over USB after
`LSK HELLO`. Firmware does not implement the LSK GATT service; Field BLE
GPS is a different join (phone next to the radio). This module is the
USB path: the deck's own GPS (`LSK T` lat/lon, only when the firmware
has a fix) and optional live frames (`LSK F` with `hex`).

`LSK ID.node` is `localMeshtasticNodeNum()` as `!xxxxxxxx`. Field decks
are MAC-derived; `0x4C534B01` is the simulator/fallback and is never
filled in when the identity line omits `node`. Unix time is the host
clock on a live listen, never invented for an undated log.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

SESSION_DDL = """
CREATE TABLE IF NOT EXISTS lsk.sessions (
    source VARCHAR,
    node VARCHAR,
    from_node BIGINT,
    from_bang VARCHAR,
    short VARCHAR,
    fw VARCHAR,
    board VARCHAR,
    app VARCHAR
)
"""

TELEMETRY_DDL = """
CREATE TABLE IF NOT EXISTS lsk.telemetry (
    source VARCHAR,
    sample INTEGER,
    node VARCHAR,
    gps VARCHAR,
    lat DOUBLE,
    lon DOUBLE,
    sat INTEGER,
    profile VARCHAR,
    freq_hz BIGINT,
    newest_frame BIGINT,
    sim BOOLEAN,
    unix_seconds BIGINT
)
"""

_LSK_KINDS = {"ID", "T", "F", "P", "S", "OK", "ERR"}


def parse_lsk_node(node: str | int | None) -> dict | None:
    """Parse `LSK ID.node` (`!xxxxxxxx` or 8 hex digits). None if omitted."""
    if node is None or node == "":
        return None
    if isinstance(node, int):
        n = int(node) & 0xFFFFFFFF
        return {
            "from_node": n,
            "from_bang": f"!{n:08x}",
            "short": f"{n & 0xFFFF:04X}",
        }
    text = str(node).strip()
    if text.startswith("!"):
        text = text[1:]
    if len(text) != 8:
        return None
    try:
        n = int(text, 16)
    except ValueError:
        return None
    return {
        "from_node": n,
        "from_bang": f"!{n:08x}",
        "short": f"{n & 0xFFFF:04X}",
    }


def parse_lsk_line(line: str) -> dict | None:
    """One `LSK <kind> {json}` line, or None if it is not analyzer-link text."""
    text = line.strip()
    if text.endswith("\r"):
        text = text[:-1]
    if not text.startswith("LSK "):
        return None
    parts = text.split(" ", 2)
    if len(parts) < 2:
        return None
    kind = parts[1]
    if kind not in _LSK_KINDS:
        return None
    body: dict = {}
    if len(parts) >= 3 and parts[2].startswith("{"):
        try:
            parsed = json.loads(parts[2])
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        body = parsed
    return {"kind": kind, "body": body}


def _fix_lat_lon(body: dict) -> tuple[float, float] | None:
    """Firmware prints lat/lon only with a GPS fix. Both keys required."""
    if "lat" not in body or "lon" not in body:
        return None
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (TypeError, ValueError):
        return None
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon


def parse_lsk_lines(lines, *, host_clock: bool = False) -> dict:
    """Fold a stream of LSK lines into identity, GPS samples, and frames.

    `host_clock` stamps unix seconds from the host as each line is read
    (live USB). Undated logs leave unix_seconds unset — do not guess.
    """
    identity: dict | None = None
    telemetry: list[dict] = []
    frames: list[dict] = []
    for line in lines:
        parsed = parse_lsk_line(line if isinstance(line, str) else str(line))
        if not parsed:
            continue
        unix = int(time.time()) if host_clock else None
        kind, body = parsed["kind"], parsed["body"]
        if kind == "ID":
            ident = parse_lsk_node(body.get("node"))
            identity = {
                "app": body.get("app"),
                "fw": body.get("fw"),
                "board": body.get("board"),
                "node": body.get("node"),
            }
            if ident:
                identity.update(ident)
            continue
        if kind == "T":
            sample = {
                "gps": body.get("gps"),
                "sat": body.get("sat"),
                "profile": body.get("profile"),
                "freq_hz": body.get("freq_hz"),
                "newest_frame": body.get("frames"),
                "sim": bool(body.get("sim")),
                "unix_seconds": unix,
                "lat": None,
                "lon": None,
            }
            fix = _fix_lat_lon(body)
            if fix:
                sample["lat"], sample["lon"] = fix
            if identity and identity.get("from_bang"):
                sample["node"] = identity["from_bang"]
            telemetry.append(sample)
            continue
        if kind == "F":
            frame = frame_from_lsk_f(body, index=len(frames), unix_seconds=unix)
            if frame:
                frames.append(frame)
    return {"identity": identity, "telemetry": telemetry, "frames": frames}


def frame_from_lsk_f(body: dict, index: int = 0,
                     unix_seconds: int | None = None) -> dict | None:
    """Rebuild an .lscap-shaped frame from `LSK F` when `hex` is present.

    Message `text` is discarded — same default-key policy as .lscap ingest.
    """
    hex_s = body.get("hex")
    if not hex_s or not isinstance(hex_s, str):
        return None
    try:
        payload = bytes.fromhex(hex_s)
    except ValueError:
        return None
    if not payload:
        return None
    mflags = int(body.get("mflags") or 0)
    direction = int(body.get("dir") or 0)
    present = int(body.get("pf") or 0)
    synthetic = bool(body.get("sim")) or bool(mflags & 4)
    net_relayed = bool(mflags & 8)
    rssi_x10 = body.get("rssi_x10")
    snr_x10 = body.get("snr_x10")
    from .lscap import DIRECTION_RX, PRESENT_RSSI, PRESENT_SNR

    def _scaled(raw, bit):
        if raw is None or not (present & bit) or direction != DIRECTION_RX:
            return None
        try:
            return int(raw) / 10.0
        except (TypeError, ValueError):
            return None

    original = int(body.get("olen") or len(payload))
    return {
        "index": index,
        "sequence": int(body.get("seq") or 0),
        "timestamp_us": int(body.get("ts") or 0),
        "freq_hz": int(body.get("freq") or body.get("freq_hz") or 0),
        "bandwidth_hz": int(body.get("bw") or 0),
        "airtime_us": int(body.get("air") or 0),
        "rssi_dbm": _scaled(rssi_x10, PRESENT_RSSI),
        "snr_db": _scaled(snr_x10, PRESENT_SNR),
        "profile_id": int(body.get("prof") or 0),
        "sync_word": int(body.get("sync") or 0),
        "spreading_factor": int(body.get("sf") or 0),
        "coding_rate": int(body.get("cr") or 0),
        "modulation": int(body.get("mod") or 0),
        "direction": direction,
        "crc_state": int(body.get("crc") or 0),
        "synthetic": synthetic,
        "net_relayed": net_relayed,
        "payload": payload,
        "original_length": original,
        "captured_length": len(payload),
        "present_fields": present,
        "unix_seconds": unix_seconds,
    }


def is_serial_port(path: Path | str) -> bool:
    """True for a USB CDC / tty device, not a regular log file."""
    p = Path(path)
    name = str(p)
    if name.startswith("COM") and name[3:].isdigit():
        return True
    try:
        return p.exists() and stat.S_ISCHR(os.stat(p).st_mode)
    except OSError:
        return name.startswith("/dev/cu.") or name.startswith("/dev/tty")


def is_lsk_log(path: Path) -> bool:
    """True when the file is an LSK session log, not WiGLE/Kismet/.lscap."""
    if is_serial_port(path):
        return False
    suffix = path.suffix.lower()
    if suffix in {".lsk", ".lsklog"}:
        return True
    if suffix in {".lscap", ".kismet", ".csv"}:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            chunk = f.read(4096)
    except OSError:
        return False
    for line in chunk.splitlines():
        s = line.strip()
        if not s:
            continue
        return s.startswith("LSK ")
    return False


def load_lsk(db_path: Path, path: Path, *, host_clock: bool = False) -> int:
    """Import an LSK session log (or live capture written to a file)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return load_lsk_text(db_path, text, path.name, host_clock=host_clock)


def load_lsk_text(db_path: Path, text: str, source: str,
                  *, host_clock: bool = False) -> int:
    session = parse_lsk_lines(text.splitlines(), host_clock=host_clock)
    return write_session(db_path, source, session)


def write_session(db_path: Path, source: str, session: dict) -> int:
    """Persist LSK identity + GPS samples; load F-hex frames into lscap."""
    import duckdb

    identity = session.get("identity") or {}
    telemetry = session.get("telemetry") or []
    frames = session.get("frames") or []
    gps_rows = [t for t in telemetry if t.get("lat") is not None]
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS lsk")
        con.execute(SESSION_DDL)
        con.execute(TELEMETRY_DDL)
        con.execute("DELETE FROM lsk.sessions WHERE source = ?", [source])
        con.execute("DELETE FROM lsk.telemetry WHERE source = ?", [source])
        if identity.get("from_bang") or identity.get("fw") or gps_rows:
            con.execute(
                "INSERT INTO lsk.sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [source, identity.get("node"), identity.get("from_node"),
                 identity.get("from_bang"), identity.get("short"),
                 identity.get("fw"), identity.get("board"),
                 identity.get("app")],
            )
        rows = []
        for i, t in enumerate(gps_rows):
            rows.append((
                source, i, t.get("node") or identity.get("from_bang"),
                t.get("gps"), t["lat"], t["lon"], t.get("sat"),
                t.get("profile"), t.get("freq_hz"), t.get("newest_frame"),
                t.get("sim"), t.get("unix_seconds"),
            ))
        if rows:
            con.executemany(
                "INSERT INTO lsk.telemetry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    finally:
        con.close()
    n_frames = 0
    if frames:
        from . import lscap as lscap_mod
        n_frames = lscap_mod.write_frames(db_path, source, frames)
    # Identity-only still counts: it names the capturing deck.
    n = len(gps_rows) + n_frames
    if n == 0 and identity.get("from_bang"):
        return 1
    return n


def lsk_decks(con) -> list[dict]:
    """Latest USB GPS fix per LSK session, keyed for capturing-deck join."""
    from .queries import _has_table, _rows

    if not _has_table(con, "lsk", "sessions"):
        return []
    has_t = _has_table(con, "lsk", "telemetry")
    sessions = _rows(con, """
        SELECT source, node, from_node, from_bang, short, fw, board, app
        FROM lsk.sessions
    """)
    fixes: dict[str, dict] = {}
    if has_t:
        for row in _rows(con, """
            SELECT source, node, lat, lon, gps, sat, unix_seconds, sample
            FROM lsk.telemetry
            WHERE lat IS NOT NULL AND lon IS NOT NULL
            ORDER BY sample
        """):
            fixes[row["source"]] = row
    out = []
    for s in sessions:
        fix = fixes.get(s["source"])
        bang = s.get("from_bang")
        node = s.get("from_node")
        short = s.get("short")
        if not bang and not node and s.get("node"):
            parsed = parse_lsk_node(s["node"])
            if parsed:
                bang, node, short = (
                    parsed["from_bang"], parsed["from_node"], parsed["short"]
                )
        out.append({
            "source": s["source"],
            "capture_file": s["source"],
            "identity": bang,
            "from_node": node,
            "from_bang": bang,
            "short": short,
            "fw": s.get("fw"),
            "board": s.get("board"),
            "lat": fix["lat"] if fix else None,
            "lon": fix["lon"] if fix else None,
            "gps": fix.get("gps") if fix else None,
            "tx_frames": 0,
            "gadget": "Lilyshark T-Deck",
            "ble_name": None,
            "position_via": "lsk-t" if fix else None,
        })
    return out


def apply_lsk_gps(decks: list[dict], lsk: list[dict]) -> list[dict]:
    """Fill capturing-deck GPS from USB `LSK T`. Does not invent identity.

    Payload Position wins. Deck GPS (`lsk-t`) wins over Field BLE. A lone
    LSK session with no `node` may stamp the only unnamed deck; it never
    becomes `0x4C534B01`.
    """
    if not lsk:
        return decks
    by_bang = {d["from_bang"]: d for d in lsk if d.get("from_bang")}
    by_short = {d["short"]: d for d in lsk if d.get("short")}
    used: set[int] = set()

    def take(hit: dict | None) -> dict | None:
        if hit is None:
            return None
        used.add(id(hit))
        return hit

    for d in decks:
        hit = None
        bang = d.get("identity") or d.get("from_bang")
        if bang:
            hit = by_bang.get(bang)
        if hit is None and d.get("short"):
            hit = by_short.get(str(d["short"]).upper())
        anonymous = [row for row in lsk if not row.get("from_bang")]
        if hit is None and len(decks) == 1 and len(anonymous) == 1:
            hit = anonymous[0]
        hit = take(hit)
        if not hit:
            continue
        if hit.get("lat") is not None and d.get("position_via") != "payload":
            d["lat"] = hit["lat"]
            d["lon"] = hit["lon"]
            d["position_via"] = "lsk-t"
        if not d.get("from_bang") and hit.get("from_bang"):
            d["from_bang"] = hit["from_bang"]
            d["from_node"] = hit.get("from_node")
            d["identity"] = hit["from_bang"]
            d["short"] = hit.get("short")
        d["gadget"] = "Lilyshark T-Deck"

    for row in lsk:
        if id(row) in used:
            continue
        if not row.get("from_bang") and row.get("lat") is None:
            continue
        decks.append({
            "capture_file": row.get("source") or row.get("capture_file"),
            "identity": row.get("from_bang"),
            "from_node": row.get("from_node"),
            "from_bang": row.get("from_bang"),
            "lat": row.get("lat"),
            "lon": row.get("lon"),
            "tx_frames": 0,
            "short": row.get("short"),
            "gadget": "Lilyshark T-Deck",
            "ble_name": None,
            "position_via": row.get("position_via"),
            "fw": row.get("fw"),
        })
    return decks


def listen_serial(port: str, seconds: float | None = None,
                  hello_repeat_s: float = 1.2) -> str:
    """Handshake `LSK HELLO` and collect lines until timeout or disconnect.

    Requires pyserial. The firmware only streams after HELLO; a passive
    reader looks like a dead radio.
    """
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "pyserial is required for live USB: uv add pyserial"
        ) from exc
    link = serial.Serial(port, 115200, timeout=1)
    collected: list[str] = []
    try:
        time.sleep(0.3)
        link.reset_input_buffer()
        link.write(b"LSK HELLO\n")
        link.flush()
        end = time.time() + seconds if seconds else None
        last_hello = time.time()
        saw_id = False
        while end is None or time.time() < end:
            try:
                raw = link.readline()
            except serial.SerialException:
                break
            if not raw:
                if not saw_id and time.time() - last_hello >= hello_repeat_s:
                    link.write(b"LSK HELLO\n")
                    link.flush()
                    last_hello = time.time()
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            collected.append(line)
            if line.startswith("LSK ID"):
                saw_id = True
    finally:
        try:
            link.write(b"LSK BYE\n")
            link.flush()
        except (OSError, serial.SerialException):
            pass
        link.close()
    return "\n".join(collected)
