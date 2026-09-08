"""Mesh module tests: synthetic per-source payloads, fully offline.

Each fixture carries two geocoded nodes (one near the test LA point, one
far away) plus one with missing coordinates that must be skipped.
"""

import json

import duckdb
import pytest

from spectrecon import mesh, queries

# LA reference point used across the suite
LA = (34.0522, -118.2437)

MESHTASTIC_JSON = {
    "1000000001": {
        "longName": "LA Test Node", "shortName": "LATN",
        "hwModel": "HELTEC_V3", "role": "CLIENT",
        "latitude": 340522000, "longitude": -1182437000,
        "altitude": 100, "batteryLevel": 87,
        "lastDeviceMetrics": 1788890000,
        "seenBy": {"msh/US/2/e/LongFast/!abcdef01": 1788890000},
    },
    "1000000002": {
        "longName": "NYC Test Node", "shortName": "NYTN",
        "hwModel": "TBEAM", "role": "ROUTER",
        "latitude": 407128000, "longitude": -740060000,
    },
    "1000000003": {
        "longName": "No Coords Node", "shortName": "NCN",
        "hwModel": "UNSET", "role": "CLIENT",
    },
}

MESHCORE_JSON = [
    {"public_key": "aa11" * 16, "type": 2, "adv_name": "LA Repeater",
     "adv_lat": 34.06, "adv_lon": -118.25,
     "last_advert": "2026-09-08T10:00:00.000Z",
     "inserted_date": "2026-08-01T00:00:00.000Z",
     "updated_date": "2026-09-08T10:05:00.000Z",
     "params": {"freq": 910.525, "bw": 62.5, "cr": 8, "sf": 8}},
    {"public_key": "bb22" * 16, "type": 1, "adv_name": "Tokyo Client",
     "adv_lat": 35.68, "adv_lon": 139.69,
     "last_advert": "2026-09-07T00:00:00.000Z"},
    {"public_key": "cc33" * 16, "type": 3, "adv_name": "No Coords Room",
     "adv_lat": None, "adv_lon": None},
]

TTN_JSON = [
    {"netID": "000013", "tenantID": "ttn", "id": "eui-aabbccdd00000001",
     "eui": "AABBCCDD00000001", "clusterID": "nam1",
     "updatedAt": "2026-09-08T06:00:00.000Z",
     "location": {"latitude": 34.05, "longitude": -118.24,
                  "altitude": 55, "accuracy": 0},
     "antennaPlacement": "OUTDOOR", "online": True},
    {"netID": "000013", "tenantID": "ttn", "id": "eui-aabbccdd00000002",
     "updatedAt": "2026-09-01T06:00:00.000Z",
     "location": {"latitude": 51.5, "longitude": -0.12},
     "online": False},
    {"netID": "000013", "tenantID": "ttn", "id": "eui-aabbccdd00000003"},
]

AREDN_JS = (
    'const out = {"version":"1","date":1788890863929,"nodeInfo":['
    '{"data":{"node":"W6LA-TEST-1","lat":34.0525,"lon":-118.2440,'
    '"lastseen":1788890400,"node_details":{"hardware":"MikroTik hAP ac3",'
    '"firmware_version":"20260908-e0ed34ed","mesh_supernode":true},'
    '"link_info":{"10.1.2.3":{"hostname":"w6la-test-2","linkType":"RF"}}}},'
    '{"data":{"node":"F4FAR-TEST","lat":48.85,"lon":2.35,'
    '"lastseen":1788890000,"node_details":{"hardware":"Ubiquiti"},'
    '"link_info":{}}},'
    '{"data":{"node":"NOCOORDS-TEST","node_details":{}}}'
    ']};'
)

RETICULUM_JSON = {
    "generated_at": "2026-09-08 20:12:07 UTC",
    "nodes": [
        {"hash": "a1b2c3d4" * 4, "node_type": "RNode_LoRa",
         "iface_type": "RNodeInterface",
         "display_name": "LA Public RNode", "lat": 34.06, "lon": -118.24,
         "frequency": "915.0 MHz", "bandwidth": "125 kHz",
         "spreading_factor": 7, "altitude": 40.0, "transport_enabled": True,
         "first_seen": "2026-03-01 00:00:00 UTC",
         "last_seen": "2026-09-08 20:00:00 UTC"},
        {"hash": "e5f6a7b8" * 4, "node_type": "LoRa",
         "display_name": "Sydney RNode", "lat": -33.87, "lon": 151.21},
        {"hash": "c9d0e1f2" * 4, "node_type": "RNode_LoRa",
         "display_name": "No Coords RNode", "lat": None, "lon": None},
    ],
}

PARSERS = {
    "meshtastic": lambda: mesh.parse_meshtastic(MESHTASTIC_JSON),
    "meshcore": lambda: mesh.parse_meshcore(MESHCORE_JSON),
    "ttn_gateway": lambda: mesh.parse_ttn(TTN_JSON),
    "aredn": lambda: mesh.parse_aredn(AREDN_JS),
    "reticulum": lambda: mesh.parse_reticulum(RETICULUM_JSON),
}


@pytest.fixture()
def mesh_db(tmp_path):
    """A DuckDB with mesh.nodes loaded from every synthetic fixture."""
    db = tmp_path / "mesh_test.db"  # avoid catalog name 'mesh' (schema collision)
    for source, parse in PARSERS.items():
        mesh.load_nodes(db, source, parse())
    return db


def test_parsers_skip_missing_coords():
    for source, parse in PARSERS.items():
        rows = parse()
        assert len(rows) == 2, source
        assert all(r["lat"] is not None and r["lon"] is not None
                   for r in rows)


def test_meshtastic_scaling_and_detail():
    rows = mesh.parse_meshtastic(MESHTASTIC_JSON)
    la = next(r for r in rows if r["node_id"] == "1000000001")
    assert la["lat"] == pytest.approx(34.0522)
    assert la["lon"] == pytest.approx(-118.2437)
    assert la["name"] == "LA Test Node"
    assert la["first_seen"] is not None and la["last_seen"] is not None
    detail = json.loads(la["detail"])
    assert detail["seenBy"] == ["msh/US/2/e/LongFast/!abcdef01"]
    assert detail["batteryLevel"] == 87


def test_aredn_js_wrapper():
    rows = mesh.parse_aredn(AREDN_JS)
    la = next(r for r in rows if r["node_id"] == "W6LA-TEST-1")
    assert la["node_type"] == "supernode"
    assert la["hw_or_radio"] == "MikroTik hAP ac3"
    assert json.loads(la["detail"])["links"] == {"w6la-test-2": "RF"}


def test_load_and_stats(mesh_db):
    con = duckdb.connect(str(mesh_db), read_only=True)
    rows = mesh.stats(con)
    assert {r["source"] for r in rows} == set(PARSERS)
    assert all(r["nodes"] == 2 for r in rows)
    assert all(r["last_fetch"] is not None for r in rows)
    con.close()


def test_near(mesh_db):
    con = duckdb.connect(str(mesh_db), read_only=True)
    rows = mesh.near(con, *LA, 5.0)
    # one near node per source; the far and coord-less nodes stay out
    assert len(rows) == 5
    assert {r["source"] for r in rows} == set(PARSERS)
    assert rows[0]["dist_km"] <= rows[-1]["dist_km"]
    only_meshcore = mesh.near(con, *LA, 5.0, source="meshcore")
    assert [r["name"] for r in only_meshcore] == ["LA Repeater"]
    assert only_meshcore[0]["node_type"] == "repeater"
    con.close()


def test_search(mesh_db):
    con = duckdb.connect(str(mesh_db), read_only=True)
    rows = mesh.search(con, "LA")
    names = {r["name"] for r in rows}
    assert {"LA Test Node", "LA Repeater", "LA Public RNode"} <= names
    # node-id substring hits too
    assert mesh.search(con, "aabbccdd00000001")
    assert mesh.search(con, "definitely-not-a-node") == []
    con.close()


def test_survey_includes_mesh(built_db):
    for source, parse in PARSERS.items():
        mesh.load_nodes(built_db, source, parse())
    con = duckdb.connect(str(built_db), read_only=True)
    rows = queries.survey(con, *LA, 2.0)
    mesh_rows = [r for r in rows if r["source"] == "mesh"]
    assert len(mesh_rows) == 5
    assert all(r["ident"] and r["owner"] for r in mesh_rows)
    assert {r["detail"].split()[0] for r in mesh_rows} == set(PARSERS)
    con.close()
