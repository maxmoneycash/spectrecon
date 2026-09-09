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
    net_relayed=False,
    crc=2,
    present=0x5FFF,
) -> None:
    rssi_x10 = -1021
    snr_x10 = 11
    captured = len(payload)
    meta = 0
    if synthetic:
        meta |= lscap.SYNTHETIC_FLAG
    if net_relayed:
        meta |= lscap.NET_RELAYED_FLAG
    file_hdr = lscap.FILE_HEADER.pack(
        lscap.FILE_MAGIC, 1, 1, lscap.FILE_HEADER_SIZE,
        lscap.RECORD_HEADER_SIZE, 0, 1_000_000, 0,
    )
    rec = lscap.RECORD_HEADER.pack(
        lscap.RECORD_MAGIC, lscap.RECORD_HEADER_SIZE, 1,
        captured, captured, sequence, timestamp_us, present,
        freq, 250_000, 0, 0, 1_000_000, 0,
        rssi_x10, snr_x10, 16, 43, 1, 0, 0,
        11, 5, 20, 0, 1, direction, crc, meta, b"\x00\x00\x00",
    )
    path.write_bytes(file_hdr + rec + payload)


def _meshtastic_frame(from_node: int, to_node: int = 0xFFFFFFFF,
                     *, via_mqtt=False) -> bytes:
    flags = 0x03 | (3 << 5)  # hop_limit 3, hop_start 3
    if via_mqtt:
        flags |= 0x10
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
    assert parsed["bang_mask"] == "!****4B01"
    assert "from_bang" not in parsed
    assert "from_node" not in parsed
    assert lscap.parse_lilyshark_name("Lilyshark-4B01")["short"] == "4B01"
    assert lscap.parse_lilyshark_name("Meshtastic_ab12") is None
    assert lscap.node_matches_short(lscap.LILYSHARK_NODE_NUM, "4B01")
    assert lscap.bang_mask("1B44") == "!****1B44"


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
    assert heard[0]["rssi_min"] is None
    assert heard[0]["rx_frames"] == 0


def test_ble_short_is_not_simulator_node(tmp_path):
    """A Field sighting of Lilyshark 4B01 is a suffix, not !4c534b01."""
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
    result = ingest_mod.debrief(db)
    decks = result["capturing_decks"]
    assert decks[0]["short"] == "4B01"
    assert decks[0]["bang_mask"] == "!****4B01"
    assert decks[0]["from_bang"] is None
    assert decks[0]["identity"] is None
    gadgets = result["gadgets"]
    assert gadgets[0]["lilyshark_short"] == "4B01"
    assert gadgets[0].get("from_bang") is None
    geo = ingest_mod.to_geojson(result)
    kinds = {f["properties"].get("kind") for f in geo["features"]}
    assert "capturing-deck" in kinds


def test_lsk_only_sensor_in_geojson(tmp_path):
    from spectrecon import ingest as ingest_mod
    from spectrecon import lsk as lsk_mod

    db = tmp_path / "t.db"
    lsk_mod.load_lsk_text(
        db,
        'LSK ID {"app":"lilyshark","fw":"x","board":"t-deck","node":"!96f61b44"}\n'
        'LSK T {"gps":"GPS 9","sim":false,"lat":37.911,"lon":-122.018,"sat":9}\n',
        "usb.lsk",
    )
    result = ingest_mod.debrief(db)
    assert result["lora"] == []
    deck = result["capturing_decks"][0]
    assert deck["from_bang"] == "!96f61b44"
    assert deck["position_via"] == "lsk-t"
    geo = ingest_mod.to_geojson(result)
    feats = [f for f in geo["features"]
             if f["properties"].get("kind") == "capturing-deck"]
    assert len(feats) == 1
    assert feats[0]["geometry"]["coordinates"][0] == -122.018


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


def test_on_air_excludes_synthetic_crc_mqtt_relay(tmp_path):
    db = tmp_path / "t.db"
    _write_lscap(tmp_path / "ok.lscap", _meshtastic_frame(0x11111111))
    _write_lscap(
        tmp_path / "sim.lscap", _meshtastic_frame(0x22222222), synthetic=True
    )
    _write_lscap(
        tmp_path / "badcrc.lscap", _meshtastic_frame(0x33333333), crc=3
    )
    _write_lscap(
        tmp_path / "mqtt.lscap", _meshtastic_frame(0x44444444, via_mqtt=True)
    )
    _write_lscap(
        tmp_path / "inj.lscap", _meshtastic_frame(0x55555555), net_relayed=True
    )
    for name in ("ok", "sim", "badcrc", "mqtt", "inj"):
        lscap.load_lscap(db, tmp_path / f"{name}.lscap")
    con = duckdb.connect(str(db), read_only=True)
    heard = lscap.heard(con)
    con.close()
    bangs = {h["from_bang"] for h in heard}
    assert bangs == {"!11111111"}


def test_lsk_t_gps_joins_without_field(tmp_path):
    """USB LSK T positions the capturing deck when Field is not present."""
    from spectrecon import ingest as ingest_mod
    from spectrecon import lsk as lsk_mod

    node = 0x96F61B44  # MAC-derived; not the simulator fallback
    cap = tmp_path / "deck.lscap"
    _write_lscap(cap, _meshtastic_frame(node), direction=2)
    log = tmp_path / "deck.lsk"
    log.write_text(
        'LSK ID {"app":"lilyshark","fw":"0.4.2","board":"t-deck",'
        '"node":"!96f61b44"}\n'
        'LSK T {"bat":"BAT 84%","gps":"GPS 9","profile":"MESHTASTIC US LF",'
        '"frames":41,"rssi_x10":-912,"snr_x10":63,"sim":false,'
        '"lat":37.911,"lon":-122.018,"sat":9,"freq_hz":906875000,'
        '"sf":11,"bw_hz":250000,"rx":128,"crc":3}\n'
        'LSK T {"bat":"BAT 84%","gps":"NOFIX","profile":"MESHTASTIC US LF",'
        '"frames":41,"rssi_x10":-912,"snr_x10":63,"sim":false,'
        '"sat":0,"freq_hz":906875000,"sf":11,"bw_hz":250000,"rx":128,"crc":3}\n'
    )
    db = tmp_path / "t.db"
    ingest_mod.import_capture(db, cap)
    n = ingest_mod.import_capture(db, log)
    assert n >= 1
    result = ingest_mod.debrief(db)
    deck = result["capturing_decks"][0]
    assert deck["from_bang"] == "!96f61b44"
    assert deck["short"] == "1B44"
    assert deck["position_via"] == "lsk-t"
    assert abs(deck["lat"] - 37.911) < 1e-6
    assert abs(deck["lon"] - (-122.018)) < 1e-6
    node_row = result["lora"][0]
    assert node_row["capturing_deck"] is True
    assert node_row["position_via"] == "lsk-t"
    assert abs(node_row["sensor_lat"] - 37.911) < 1e-6
    parsed = lsk_mod.parse_lsk_lines(log.read_text().splitlines())
    assert parsed["identity"]["from_node"] == node
    assert parsed["telemetry"][1]["lat"] is None  # NOFIX line has no lat/lon
    assert parsed["telemetry"][0]["unix_seconds"] is None  # undated log


def test_lsk_id_omitted_node_is_not_simulator_fallback(tmp_path):
    from spectrecon import lsk as lsk_mod

    session = lsk_mod.parse_lsk_lines([
        'LSK ID {"app":"lilyshark","fw":"0.1.0","board":"t-deck"}\n',
        'LSK T {"gps":"GPS 4","sim":false,"lat":35.0,"lon":-80.0,"sat":4}\n',
    ])
    assert session["identity"].get("from_bang") is None
    assert session["identity"].get("from_node") is None
    db = tmp_path / "t.db"
    n = lsk_mod.write_session(db, "usb.log", session)
    assert n == 1
    con = duckdb.connect(str(db), read_only=True)
    decks = lscap.capturing_decks(con)
    con.close()
    assert decks[0]["from_bang"] is None
    assert decks[0]["position_via"] == "lsk-t"
    assert decks[0]["lat"] == 35.0


def test_lsk_t_beats_field_ble_but_not_payload(tmp_path):
    from spectrecon import ingest as ingest_mod
    from spectrecon import lsk as lsk_mod

    node = 0x96F61B44
    csv = tmp_path / "field.csv"
    csv.write_text(
        "WigleWifi-1.4,appRelease=test,model=iPhone,release=1,"
        "device=test,display=x,board=x,brand=Apple\n"
        "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,"
        "CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type\n"
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE,Lilyshark 1B44,[RIG:lilyshark],"
        "2026-09-01 10:01:00,0,-55,35.1,-80.2,100,8,BLE\n"
    )
    db = tmp_path / "t.db"
    ingest_mod.import_capture(db, csv)
    cap = tmp_path / "deck.lscap"
    _write_lscap(cap, _meshtastic_frame(node), direction=2)
    ingest_mod.import_capture(db, cap)
    lsk_mod.load_lsk_text(
        db,
        'LSK ID {"app":"lilyshark","fw":"x","board":"t-deck","node":"!96f61b44"}\n'
        'LSK T {"gps":"GPS 9","sim":false,"lat":37.911,"lon":-122.018,"sat":9}\n',
        "usb.lsk",
    )
    result = ingest_mod.debrief(db)
    deck = result["capturing_decks"][0]
    assert deck["position_via"] == "lsk-t"
    assert abs(deck["lat"] - 37.911) < 1e-6
    assert deck["ble_name"] == "Lilyshark 1B44"


def test_lsk_gps_does_not_override_payload_position():
    from spectrecon import lsk as lsk_mod

    decks = [{
        "identity": "!96f61b44", "from_bang": "!96f61b44", "short": "1B44",
        "lat": 10.0, "lon": 20.0, "position_via": "payload", "tx_frames": 1,
    }]
    lsk = [{
        "from_bang": "!96f61b44", "short": "1B44",
        "lat": 37.9, "lon": -122.0, "position_via": "lsk-t",
    }]
    out = lsk_mod.apply_lsk_gps(decks, lsk)
    assert out[0]["lat"] == 10.0
    assert out[0]["position_via"] == "payload"


def test_is_lsk_log(tmp_path):
    from spectrecon import lsk as lsk_mod

    log = tmp_path / "session.txt"
    log.write_text('LSK ID {"app":"lilyshark","fw":"x","board":"t-deck"}\n')
    csv = tmp_path / "drive.csv"
    csv.write_text("WigleWifi-1.4\nMAC,SSID\n")
    named = tmp_path / "deck.lsk"
    named.write_text("")
    assert lsk_mod.is_lsk_log(log) is True
    assert lsk_mod.is_lsk_log(csv) is False
    assert lsk_mod.is_lsk_log(named) is True
    assert lsk_mod.parse_lsk_node(None) is None
    assert lsk_mod.parse_lsk_node("!96f61b44")["short"] == "1B44"


def test_lsk_f_hex_loads_as_heard_without_text(tmp_path):
    import json

    from spectrecon import ingest as ingest_mod
    from spectrecon import lsk as lsk_mod

    payload = _meshtastic_frame(0x1234ABCD)
    body = {
        "src": 0x1234ABCD,
        "dst": 0xFFFFFFFF,
        "proto": "Meshtastic",
        "port": 1,
        "hops": 0,
        "rssi_x10": -912,
        "snr_x10": 63,
        "kind": "TEXT",
        "sim": False,
        "text": "secret default-key chatter",
        "seq": 7,
        "ts": 1_000_000,
        "pf": 0x5FFF,
        "freq": 906_875_000,
        "bw": 250_000,
        "prof": 1,
        "sf": 11,
        "dir": 1,
        "crc": 2,
        "mflags": 0,
        "olen": len(payload),
        "hex": payload.hex(),
    }
    db = tmp_path / "t.db"
    n = lsk_mod.load_lsk_text(
        db,
        'LSK ID {"app":"lilyshark","fw":"x","board":"t-deck","node":"!96f61b44"}\n'
        f"LSK F {json.dumps(body)}\n",
        "live.lsk",
        host_clock=True,
    )
    assert n >= 1
    result = ingest_mod.debrief(db)
    heard = result["lora"]
    assert heard[0]["from_bang"] == "!1234abcd"
    assert heard[0]["role"] == "rx"
    assert abs(heard[0]["rssi_min"] - (-91.2)) < 1e-6
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute(
        "SELECT long_name, unix_seconds FROM lscap.frames"
    ).fetchone()
    con.close()
    assert row[0] is None  # text discarded; no NodeInfo names
    assert row[1] is not None  # live host clock


def test_rssi_absent_when_present_bit_clear(tmp_path):
    cap = tmp_path / "deck.lscap"
    _write_lscap(cap, _meshtastic_frame(0x1234ABCD), present=0x0003)
    db = tmp_path / "t.db"
    lscap.load_lscap(db, cap)
    con = duckdb.connect(str(db), read_only=True)
    heard = lscap.heard(con)
    row = con.execute("SELECT rssi_dbm, snr_db FROM lscap.frames").fetchone()
    con.close()
    assert row == (None, None)
    assert heard[0]["rssi_min"] is None
