"""Integration tests: build pipelines from synthetic data, then query them."""

from spectrecon import queries


def test_uls_entity_and_geo(built_db):
    from spectrecon import queries as q
    import duckdb

    con = duckdb.connect(str(built_db), read_only=True)
    result = q.entity(con, "TESTCO")
    assert result["summary"]["license_count"] == 1
    assert result["summary"]["frns"] == ["0001234567"]

    sites = q.geo(con, 34.0527, -118.2458, 5.0)
    assert len(sites) == 1
    assert sites[0]["call_sign"] == "WTEST1"
    assert sites[0]["entity_name"] == "TESTCO INDUSTRIES LLC"
    con.close()


def test_lookup(built_db):
    import duckdb

    con = duckdb.connect(str(built_db), read_only=True)
    result = queries.lookup(con, "WTEST1")
    assert len(result["licenses"]) == 1
    assert result["frequencies"][0]["freq_mhz"] == 461.5625
    con.close()


def test_asr_towers(built_db):
    import duckdb

    con = duckdb.connect(str(built_db), read_only=True)
    rows = queries.towers(con, 34.0527, -118.2458, 5.0)
    assert len(rows) == 1
    assert rows[0]["registration_number"] == "A0999999"
    assert rows[0]["owner_name"] == "TESTCO INDUSTRIES LLC"
    assert rows[0]["height_overall_m"] == 35.0
    con.close()


def test_ibfs_satellite_and_entity(built_db):
    import duckdb

    con = duckdb.connect(str(built_db), read_only=True)
    sats = queries.satellite(con, "TESTSAT")
    assert len(sats) == 1
    assert sats[0]["callsign"] == "KS999"
    assert sats[0]["inactive_date"] is None  # 1900 sentinel nulled

    filings = queries.ibfs_filings(con, "TESTCO")
    assert len(filings) == 1
    assert filings[0]["callsign"] == "WTEST-SAT1"
    assert filings[0]["frn"] == "0001234567"

    # FRN joins ULS and IBFS
    rows = con.execute(
        "SELECT l.call_sign, f.callsign FROM uls.licenses l "
        "JOIN ibfs.filings f ON l.frn = f.frn"
    ).fetchall()
    assert rows == [("WTEST1", "WTEST-SAT1")]
    con.close()


def test_ibfs_frequency_chain(built_db):
    import duckdb

    con = duckdb.connect(str(built_db), read_only=True)
    rows = con.execute(
        "SELECT callsign, freq_low_mhz, freq_high_mhz, antenna_make "
        "FROM ibfs.frequencies"
    ).fetchall()
    assert rows == [("WTEST-SAT1", 3700.0, 4200.0, "TEST ANTENNA")]
    con.close()


def test_debrief(capture_db):
    from spectrecon import ingest as ingest_mod

    result = ingest_mod.debrief(capture_db)
    assert result["summary"]["unique_devices"] == 3
    # Testco Shopnet sits near the TESTCO licensed site -> attributed
    assert any(a["ssid"] == "Testco Shopnet" for a in result["attributions"])
    # DesertBox is far from anything licensed -> anomaly
    assert [a["ssid"] for a in result["anomalies"]] == ["DesertBox"]
    # OUI + randomized-MAC flags
    devices = {d["bssid"]: d for d in result["devices"]}
    assert devices["24:6F:28:AA:BB:01"]["vendor"] is None  # no ref.oui loaded
    assert devices["1E:2C:3D:AA:BB:02"]["randomized_mac"] is True
    assert devices["24:6F:28:AA:BB:03"]["randomized_mac"] is False


def test_gaps_exclude_covered(capture_db):
    import duckdb

    con = duckdb.connect(str(capture_db), read_only=True)
    # the TESTCO site at 34.0527,-118.2458 is within 250m of two observations
    result = queries.coverage_gaps(con, 34.0527, -118.2458, 5.0, 0.25)
    assert result["sites"] == []          # covered -> excluded
    assert len(result["towers"]) == 1     # tower sits 100+m away, uncovered
    con.close()
