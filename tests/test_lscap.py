"""Lilyshark .lscap import and Meshtastic header → mesh.nodes join."""

from pathlib import Path

import duckdb

from spectrecon import lscap
from spectrecon import mesh as mesh_mod


def _write_lscap(path: Path, payload: bytes, freq=906_875_000) -> None:
    rssi_x10 = -1021
    snr_x10 = 11
    captured = len(payload)
    file_hdr = lscap.FILE_HEADER.pack(
        lscap.FILE_MAGIC, 1, 1, lscap.FILE_HEADER_SIZE,
        lscap.RECORD_HEADER_SIZE, 0, 1_000_000, 0,
    )
    rec = lscap.RECORD_HEADER.pack(
        lscap.RECORD_MAGIC, lscap.RECORD_HEADER_SIZE, 1,
        captured, captured, 0, 9_000_000, 0x5FFF,
        freq, 250_000, 0, 0, 1_000_000, 0,
        rssi_x10, snr_x10, 16, 43, 1, 0, 0,
        11, 5, 20, 0, 1, 1, 2, 0, b"\x00\x00\x00",
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
