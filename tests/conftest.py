"""Shared fixtures: a tiny synthetic FCC dataset exercising every pipeline.

Rows are built positionally (field numbers match the schema order, 1-based)
so field placement is explicit instead of hand-counted pipes.
"""

import zipfile
from pathlib import Path

import pytest


def make_row(code: str, length: int, vals: dict[int, str], caret: bool = False) -> str:
    """Build a pipe-delimited row: field 1 is the record code; vals maps
    1-based field numbers to values. caret appends the IBFS |^ terminator."""
    fields = [code] + [""] * (length - 1)
    for pos, v in vals.items():
        fields[pos - 1] = v
    line = "|".join(fields)
    if caret:
        line += "|^"
    return line + "\n"


# ULS rows: one license (HD/EN/LO/FR) for TESTCO at a known LA coordinate.
ULS_FILES = {
    "HD.dat": make_row("HD", 61, {2: "7000001", 3: "0007000001", 5: "WTEST1",
                                  6: "A", 7: "PW", 8: "01/15/2020",
                                  9: "01/15/2030"}),
    "EN.dat": make_row("EN", 31, {2: "7000001", 3: "0007000001", 5: "WTEST1",
                                  6: "L", 8: "TESTCO INDUSTRIES LLC",
                                  23: "0001234567"}),
    "LO.dat": make_row("LO", 51, {2: "7000001", 3: "0007000001", 5: "WTEST1",
                                  7: "F", 8: "L", 9: "1",
                                  12: "123 Main St", 13: "LOS ANGELES",
                                  14: "LOS ANGELES", 15: "CA",
                                  20: "34", 21: "3", 22: "10.0", 23: "N",
                                  24: "118", 25: "14", 26: "45.0", 27: "W"}),
    "FR.dat": make_row("FR", 30, {2: "7000001", 3: "0007000001", 5: "WTEST1",
                                  7: "1", 8: "1", 9: "FB", 11: "461.5625",
                                  16: "50.0", 17: "75.0"}),
}

# ASR rows: one tower at a nearby coordinate, owned by TESTCO.
ASR_FILES = {
    "RA.dat": make_row("RA", 49, {2: "REG", 3: "A0999999", 4: "1000049",
                                  5: "893479", 6: "NE", 8: "I", 9: "G",
                                  18: "Tower", 19: "C", 20: "Contact",
                                  24: "123 Main St", 25: "LOS ANGELES",
                                  26: "CA", 27: "06037", 28: "90012",
                                  29: "30.0", 30: "100.0", 31: "35.0",
                                  32: "135.0", 33: "TOWER"}),
    "CO.dat": make_row("CO", 17, {2: "REG", 3: "A0999999", 4: "1000049",
                                  5: "893479", 6: "T",
                                  7: "34", 8: "3", 9: "20.0", 10: "N",
                                  11: "122320.0",
                                  12: "118", 13: "14", 14: "50.0", 15: "W",
                                  16: "424890.0"}),
    "EN.dat": make_row("EN", 25, {2: "REG", 3: "A0999999", 4: "1000049",
                                  5: "893478", 6: "O",
                                  10: "TESTCO INDUSTRIES LLC",
                                  21: "LOS ANGELES", 22: "CA", 23: "90012"}),
}

# IBFS rows: one satellite + one earth-station filing chain (caret-terminated).
IBFS_FILES = {
    "main.dat": make_row("main", 71, {2: "1", 3: "WTEST-SAT1",
                                      4: "SES-2020-0001", 5: "SES", 6: "GRA",
                                      11: "Jan 02 2020 12:00:00:000AM",
                                      13: "Jan 15 2020 12:00:00:000AM",
                                      18: "Jan 15 2035 12:00:00:000AM",
                                      41: "Test earth station filing",
                                      42: "-2001"},
                         caret=True),
    "address.dat": make_row("address", 14, {2: "0001234567",
                                            3: "TESTCO INDUSTRIES LLC",
                                            5: "123 Main St", 7: "LOS ANGELES",
                                            8: "CA", 9: "90012", 10: "USA",
                                            11: "TESTCO", 13: "0001234567",
                                            14: "N"}, caret=True),
    "site.dat": make_row("site", 33, {2: "-1001", 3: "1", 8: "LOS ANGELES",
                                      9: "LOS ANGELES", 10: "CA",
                                      11: "90012", 13: "100.0",
                                      14: "34", 15: "3", 16: "30.0", 17: "N",
                                      18: "118", 19: "14", 20: "55.0",
                                      21: "W"}, caret=True),
    "anten.dat": make_row("anten", 17, {2: "1", 3: "-3001", 4: "5.0",
                                        11: "TEST ANTENNA", 14: "MODEL-1",
                                        15: "1"}, caret=True),
    "freq.dat": make_row("freq", 13, {2: "-4001", 3: "Z", 6: "36000F9",
                                      7: "00003700.00000000",
                                      8: "00004200.00000000", 9: "R"},
                         caret=True),
    "space_sta.dat": make_row("space_sta", 7, {2: "KS999", 3: "TESTSAT-1",
                                               4: "NGSO",
                                               6: "Jan  1 1900 12:00:00:000AM"},
                              caret=True),
}
# fix record-code slot: IBFS rows carry the real key in field 1, not the name
IBFS_FILES["main.dat"] = IBFS_FILES["main.dat"].replace("main|", "-1001|", 1)
IBFS_FILES["address.dat"] = IBFS_FILES["address.dat"].replace("address|", "-2001|", 1)
IBFS_FILES["site.dat"] = IBFS_FILES["site.dat"].replace("site|", "-3001|", 1)
IBFS_FILES["anten.dat"] = IBFS_FILES["anten.dat"].replace("anten|", "-4001|", 1)
IBFS_FILES["freq.dat"] = IBFS_FILES["freq.dat"].replace("freq|", "-5001|", 1)
IBFS_FILES["space_sta.dat"] = IBFS_FILES["space_sta.dat"].replace("space_sta|", "-6001|", 1)

WIGLE_CSV = """WigleWifi-1.4,appRelease=test,model=test,release=1,device=test,display=x,board=x,brand=x
MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type
24:6F:28:AA:BB:01,Testco Shopnet,[WPA2-PSK][ESS],2026-09-01 10:00:00,6,-45,34.0530,-118.2460,100,5,WIFI
1E:2C:3D:AA:BB:02,RandomPhone,[OPEN][ESS],2026-09-01 10:01:00,1,-70,34.0531,-118.2461,100,5,WIFI
24:6F:28:AA:BB:03,DesertBox,[WPA2-PSK][ESS],2026-09-01 11:00:00,3,-55,33.0000,-117.0000,100,5,WIFI
"""


def _make_zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


@pytest.fixture()
def built_db(tmp_path):
    """A DuckDB with all pipelines built from synthetic data."""
    from spectrecon import build as build_mod
    from spectrecon.schema import ASR_TABLES, IBFS_TABLES

    raw = tmp_path / "raw"
    raw.mkdir()
    _make_zip(raw / "l_test.zip", ULS_FILES)
    _make_zip(raw / "r_tower.zip", ASR_FILES)
    _make_zip(raw / "IBFS.zip", IBFS_FILES)

    db = tmp_path / "test.db"
    counts = build_mod.normalize_zips([raw / "l_test.zip"], tmp_path / "stage")
    build_mod.load_duckdb(db, tmp_path / "stage", counts)
    counts = build_mod.normalize_zips([raw / "r_tower.zip"], tmp_path / "stage_asr",
                                      tables=ASR_TABLES, name_prefix="asr_")
    build_mod.load_duckdb(db, tmp_path / "stage_asr", counts, schema="asr",
                          tables=ASR_TABLES, name_prefix="asr_")
    counts = build_mod.normalize_zips([raw / "IBFS.zip"], tmp_path / "stage_ibfs",
                                      tables=IBFS_TABLES, name_prefix="ibfs_",
                                      caret_terminated=True)
    build_mod.load_duckdb(db, tmp_path / "stage_ibfs", counts, schema="ibfs",
                          tables=IBFS_TABLES, name_prefix="ibfs_")
    return db


@pytest.fixture()
def capture_db(built_db, tmp_path):
    """built_db plus the synthetic WiGLE capture imported."""
    from spectrecon import ingest as ingest_mod

    csv_path = tmp_path / "test.csv"
    csv_path.write_text(WIGLE_CSV)
    ingest_mod.import_capture(built_db, csv_path)
    return built_db
