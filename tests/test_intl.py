"""Integration tests for the international loaders (synthetic CSV fixtures)."""

import zipfile

from spectrecon import intl as intl_mod
from spectrecon import queries

ISED_CSV = '"TX","461.5625","0001403864","3","D","B","","","","D","12.5","10K0F1D","OTHER-DIGITAL","","23.35","100","","","","","","* Test Manufacturer *","DBS5100A","7.99","","","0","B","9","","","TEST SITE BC","","XCG","6","AL","","","",BC,"49.2525","-117.6625","517","9","","","","010651301-002","8","800","S","G","2009-11-27","013080198800","TESTCO CANADA LTD","123 Test Rd,Ottawa","","","","",""\n'
# the DuckDB sniffer needs >1 row to lock the dialect on tiny fixtures
ISED_CSV += ISED_CSV

OFCOM_CSV = (
    "Licence Number,Frequency (Hz),Station Type,Licencee Company,Status,"
    "Latitude(Deg),Longitude(Deg),Antenna ERP,Emission Code\n"
    "0001/TEST,461312500,T,TESTCO UK LTD,Live,51.5074,-0.1278,10,10K0F1D\n"
)


def test_ised_loader(tmp_path):
    zip_path = tmp_path / "TAFL_LTAF.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("TAFL_LTAF.csv", ISED_CSV)
    db = tmp_path / "t.db"
    n = intl_mod.load_ised(db, zip_path)
    assert n == 2  # fixture rows are doubled so the DuckDB sniffer can lock
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute(
        "SELECT licensee_name, call_sign, freq_mhz_num, lat_num, lon_num "
        "FROM ised.assignments").fetchone()
    assert row == ("TESTCO CANADA LTD", "XCG", 461.5625, 49.2525, -117.6625)
    con.close()


def test_ofcom_loader(tmp_path):
    csv_path = tmp_path / "WTR.csv"
    csv_path.write_text(OFCOM_CSV)
    db = tmp_path / "t.db"
    n = intl_mod.load_ofcom(db, csv_path)
    assert n == 1
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute(
        "SELECT licensee, licence_number, lat_num, lon_num "
        "FROM ofcom.licences").fetchone()
    assert row == ("TESTCO UK LTD", "0001/TEST", 51.5074, -0.1278)
    con.close()


ACMA_SITE = 'SITE_ID,LATITUDE,LONGITUDE,NAME,STATE,POSTCODE,ELEVATION,HCIS_L2\n12345,-33.8688,151.2093,TEST SITE SYDNEY,NSW,2000,10,NT56\n'
ACMA_CLIENT = 'CLIENT_NO,LICENCEE,TRADING_NAME,ACN,ABN,POSTAL_STREET,POSTAL_SUBURB,POSTAL_STATE,POSTAL_POSTCODE,CAT_ID,CLIENT_TYPE_ID,FEE_STATUS_ID\n1310836,TESTCO AUSTRALIA LTD,Testco AU,123,456,1 Test St,Sydney,NSW,2000,1,1,1\n'


def test_acma_loader(tmp_path):
    zip_path = tmp_path / "spectra_rrl.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("site.csv", ACMA_SITE)
        zf.writestr("client.csv", ACMA_CLIENT)
    db = tmp_path / "t.db"
    counts = intl_mod.load_acma(db, zip_path)
    assert counts["site"] == 1
    assert counts["client"] == 1
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute("SELECT lat_num, lon_num FROM acma.sites").fetchone()
    assert row == (-33.8688, 151.2093)
    hits = queries.intl_entities(con, "TESTCO")
    assert hits["acma"][0]["licensee"] == "TESTCO AUSTRALIA LTD"
    con.close()
