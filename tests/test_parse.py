"""Parser unit tests: WiGLE CSV, Kismet SQLite, ULS normalization quirks."""

import sqlite3

from spectrecon.ingest import parse_kismet, parse_wigle_csv


def test_wigle_basic(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(
        "WigleWifi-1.4,appRelease=x,model=y\n"
        "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,"
        "CurrentLongitude,AltitudeMeters,AccuracyMeters,Type\n"
        "AA:BB:CC:DD:EE:FF,TestNet,[WPA2][ESS],2026-01-01 00:00:00,6,-50,"
        "34.0,-118.0,100,5,WIFI\n"
    )
    rows = parse_wigle_csv(p)
    assert len(rows) == 1
    assert rows[0]["bssid"] == "AA:BB:CC:DD:EE:FF"
    assert rows[0]["ssid"] == "TestNet"
    assert rows[0]["lat"] == 34.0
    assert rows[0]["obs_type"] == "WIFI"


def test_wigle_skips_garbage(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text(
        "WigleWifi-1.4,junk\n"
        "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,"
        "CurrentLongitude,AltitudeMeters,AccuracyMeters,Type\n"
        "\n"
        "AA:BB:CC:DD:EE:01,TooShort,[x],x,1\n"
        "AA:BB:CC:DD:EE:02,BadNum,[x],x,1,-50,notalat,-118.0,0,0,WIFI\n"
        "AA:BB:CC:DD:EE:03,Good,[x],x,1,-50,34.0,-118.0,0,0,WIFI\n"
    )
    rows = parse_wigle_csv(p)
    assert [r["ssid"] for r in rows] == ["Good"]


def test_kismet(tmp_path):
    import json
    p = tmp_path / "t.kismet"
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE devices (first_time INT, last_time INT, devmac TEXT, "
        "phyname TEXT, devtype TEXT, strongest_signal INT, min_lat REAL, "
        "min_lon REAL, max_lat REAL, max_lon REAL, avg_lat REAL, "
        "avg_lon REAL, device TEXT)"
    )
    con.execute(
        "INSERT INTO devices VALUES (1757260000,1757260100,"
        "'24:5A:4C:77:88:99','IEEE802.11','AP',-48,0,0,0,0,34.05,-118.25,?)",
        (json.dumps({"dot11.device": {"last_beaconed_ssid": "TestSSID"}}),),
    )
    # zero-coordinate device must be skipped
    con.execute(
        "INSERT INTO devices VALUES (1757260000,1757260100,"
        "'00:11:22:33:44:55','Bluetooth','BT',-70,0,0,0,0,0,0,'{}')"
    )
    con.commit()
    con.close()
    rows = parse_kismet(p)
    assert len(rows) == 1
    assert rows[0]["ssid"] == "TestSSID"
    assert rows[0]["obs_type"] == "WIFI"
    assert rows[0]["bssid"] == "24:5A:4C:77:88:99"
