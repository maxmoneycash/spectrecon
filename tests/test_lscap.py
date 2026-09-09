"""Lilyshark .lscap import and Meshtastic header → mesh.nodes join."""

from pathlib import Path

import duckdb

from spectrecon import lscap
from spectrecon import mesh as mesh_mod


def _write_lscap(
    path: Path,
    payload: bytes,
    freq=906_875_000,
    *,
    direction=1,
    sequence=0,
    timestamp_us=9_000_000,
    synthetic=False,
    crc=2,
) -> None:
    rssi_x10 = -1021
    snr_x10 = 11
    captured = len(payload)
    meta = lscap.SYNTHETIC_FLAG if synthetic else 0
    file_hdr = lscap.FILE_HEADER.pack(
        lscap.FILE_MAGIC, 1, 1, lscap.FILE_HEADER_SIZE,
        lscap.RECORD_HEADER_SIZE, 0, 1_000_000, 0,
    )
    rec = lscap.RECORD_HEADER.pack(
        lscap.RECORD_MAGIC, lscap.RECORD_HEADER_SIZE, 1,
        captured, captured, sequence, timestamp_us, 0x5FFF,
        freq, 250_000, 0, 0, 1_000_000, 0,
        rssi_x10, snr_x10, 16, 43, 1, 0, 0,
        11, 5, 20, 0, 1, direction, crc, meta, b"\x00\x00\x00",
    )
    path.write_bytes(file_hdr + rec + payload)


def _meshtastic_frame(from_node: int, to_node: int = 0xFFFFFFFF) -> bytes:
    flags = 0x03 | (3 << 5)  # hop_limit 3, hop_start 3
    return (
        to_node.to_bytes(4, "little")
        + from_node.to_bytes(4, "little")
        + (1).to_bytes(4, "little")
        + bytes([flags, 8, 0, 0])
        + b"\x00\x00\x00\x00"
    )


def test_parse_meshtastic_header():
    payload = _meshtastic_frame(lscap.LILYSHARK_NODE_NUM)
    hdr = lscap.parse_meshtastic_header(payload)
    assert hdr is not None
    assert hdr["from_bang"] == "!4c534b01"
    assert hdr["lilyshark_deck"] is True
    assert hdr["broadcast"] is True


def test_import_and_join_mesh(tmp_path):
    payload = _meshtastic_frame(lscap.LILYSHARK_NODE_NUM)
    cap = tmp_path / "deck.lscap"
    _write_lscap(cap, payload)
    db = tmp_path / "t.db"
    n = lscap.load_lscap(db, cap)
    assert n == 1

    mesh_mod.load_nodes(db, "meshtastic", [{
        "node_id": "!4c534b01",
        "name": "Lilyshark 4B01",
        "node_type": "CLIENT",
        "hw_or_radio": "T-DECK",
        "lat": 35.0, "lon": -80.0, "altitude": None,
        "first_seen": None, "last_seen": None, "detail": "{}",
    }])
    con = duckdb.connect(str(db), read_only=True)
    heard = lscap.heard(con)
    con.close()
    assert len(heard) == 1
    assert heard[0]["lilyshark_deck"] is True
    assert heard[0]["gadget"] == "Lilyshark T-Deck"
    assert heard[0]["mesh_name"] == "Lilyshark 4B01"
    assert heard[0]["mesh_source"] == "meshtastic"


def test_debrief_lscap_only(tmp_path):
    from spectrecon import ingest as ingest_mod

    cap = tmp_path / "deck.lscap"
    _write_lscap(cap, _meshtastic_frame(0x1234ABCD))
    db = tmp_path / "t.db"
    ingest_mod.import_capture(db, cap)
    result = ingest_mod.debrief(db)
    assert result["summary"]["unique_devices"] == 0
    assert result["lora"][0]["from_bang"] == "!1234abcd"
    assert result["lora"][0]["lilyshark_deck"] is False
    assert result["lora"][0]["role"] == "rx"
    assert result["lora"][0]["direct_frames"] == 1


def test_parse_lilyshark_ble_name():
    parsed = lscap.parse_lilyshark_name("Lilyshark 4B01")
    assert parsed is not None
    assert parsed["short"] == "4B01"
    assert parsed["from_bang"] == "!4c534b01"
    assert lscap.parse_lilyshark_name("Lilyshark-4B01")["short"] == "4B01"
    assert lscap.parse_lilyshark_name("Meshtastic_ab12") is None
    assert lscap.node_matches_short(lscap.LILYSHARK_NODE_NUM, "4B01")


def test_witness_vector():
    key = lscap.witness_key(
        lscap.WITNESS_VECTOR_PAYLOAD,
        lscap.WITNESS_VECTOR_FREQ_HZ,
        lscap.WITNESS_VECTOR_UNIX_SECONDS,
    )
    assert key.hex() == lscap.WITNESS_VECTOR_KEY_HEX


def test_tx_identifies_capturing_deck(tmp_path):
    cap = tmp_path / "deck.lscap"
    _write_lscap(
        cap, _meshtastic_frame(lscap.LILYSHARK_NODE_NUM), direction=2
    )
    db = tmp_path / "t.db"
    lscap.load_lscap(db, cap)
    con = duckdb.connect(str(db), read_only=True)
    decks = lscap.capturing_decks(con)
    heard = lscap.heard(con)
    con.close()
    assert decks[0]["from_bang"] == "!4c534b01"
    assert decks[0]["short"] == "4B01"
    assert heard[0]["capturing_deck"] is True
    assert heard[0]["role"] == "tx"
    assert heard[0]["tx_frames"] == 1


def test_ble_short_name_joins_field_gps(tmp_path):
    from spectrecon import ingest as ingest_mod

    csv = tmp_path / "field.csv"
    csv.write_text(
        "WigleWifi-1.4,appRelease=test,model=iPhone,release=1,"
        "device=test,display=x,board=x,brand=Apple\n"
        "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,"
        "CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type\n"
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE,Lilyshark 4B01,[RIG:lilyshark],"
        "2026-09-01 10:01:00,0,-55,35.1,-80.2,100,8,BLE\n"
    )
    db = tmp_path / "t.db"
    ingest_mod.import_capture(db, csv)
    cap = tmp_path / "deck.lscap"
    _write_lscap(cap, _meshtastic_frame(lscap.LILYSHARK_NODE_NUM))
    ingest_mod.import_capture(db, cap)
    result = ingest_mod.debrief(db)
    node = result["lora"][0]
    assert node["lilyshark_short"] == "4B01"
    assert node["ble_name"] == "Lilyshark 4B01"
    assert node["position_via"] == "field-ble"
    assert abs(node["lat"] - 35.1) < 1e-6
    assert node["gadget"] == "Lilyshark T-Deck"


def test_sidecar_and_corroboration(tmp_path):
    epoch = 1_700_000_000
    payload = _meshtastic_frame(0x1234ABCD)
    a = tmp_path / "a.lscap"
    b = tmp_path / "b.lscap"
    _write_lscap(a, payload, sequence=7, timestamp_us=2_000_000)
    _write_lscap(b, payload, sequence=7, timestamp_us=2_000_000)
    unix = epoch + 2
    key = lscap.witness_key(payload, 906_875_000, unix).hex()
    (tmp_path / "a.lscap.witness").write_text(
        f"lilyshark-witness 1 gps {epoch}\n7 {key}\n"
    )
    db = tmp_path / "t.db"
    lscap.load_lscap(db, a)
    lscap.load_lscap(db, b, epoch=epoch)
    con = duckdb.connect(str(db), read_only=True)
    pairs = lscap.corroborate(con)
    frames = con.execute(
        "SELECT capture_file, witness_key FROM lscap.frames ORDER BY capture_file"
    ).fetchall()
    con.close()
    assert frames[0][1] == key
    assert frames[1][1] == key
    assert len(pairs) == 1
    assert pairs[0]["via"] == "witness"
    assert pairs[0]["decks"] == 2
