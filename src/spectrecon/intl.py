"""International regulator pipelines: ISED (Canada), Ofcom (UK), ACMA (AU).

Unlike the FCC .dat pipelines these are plain CSVs, so loading is direct
DuckDB read_csv; this module owns the column normalization and views.

Sources (all anonymous, keyless HTTPS):
- ISED  SMS Authorization Data Extract (monthly): headerless quoted CSV,
  layout per tafl_description_ltaf.pdf. Open Government Licence - Canada.
- Ofcom WTR register (nightly): CSV with headers, decimal lat/lon included.
- ACMA  RRL (daily): zip of relational CSVs with headers. Custom licence
  prohibits redistributing natural-person licensee personal info — we load
  it for local query only; see README.
"""

import logging
import zipfile
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

# ISED TAFL field positions: tafl_description_ltaf.pdf field N = file field
# N+1 (the files prepend a TX/RX direction column). Verified against the live
# 2026-09 extract; fN = positional hedge where content couldn't be confirmed.
ISED_COLUMNS = {
    1: "direction", 2: "freq_mhz", 3: "freq_record_id", 4: "regulatory_service",
    5: "comm_type", 6: "plan_conformity", 7: "allocation_name", 8: "channel",
    9: "f9", 10: "signal_type", 11: "bandwidth_khz", 12: "emission",
    13: "modulation", 14: "capacity", 15: "erp_dbw", 16: "tx_power_w",
    17: "loss_db", 18: "f18", 19: "f19", 20: "f20", 21: "f21",
    22: "antenna_make", 23: "antenna_model", 24: "antenna_gain_dbi",
    25: "antenna_pattern", 26: "beamwidth", 27: "front_to_back",
    28: "polarization", 29: "antenna_height_agl", 30: "azimuth",
    31: "antenna_elevation",
    32: "station_location", 33: "licensee_station_ref", 34: "call_sign",
    35: "station_type", 36: "itu_station_class", 37: "identical_stations",
    38: "reference_id", 39: "f39", 40: "province",
    41: "lat", 42: "lon", 43: "ground_elevation_m", 44: "structure_height_m",
    45: "f45", 46: "radius_km", 47: "f47",
    48: "authorization_number", 49: "service", 50: "subservice",
    51: "licence_type", 52: "authorization_status", 53: "in_service_date",
    54: "account_number", 55: "licensee_name", 56: "licensee_address",
    57: "operational_status", 58: "station_class", 59: "horizontal_power",
    60: "vertical_power", 61: "standby_tx",
}


def load_ised(db_path: Path, zip_path: Path) -> int:
    """Load the ISED SMS extract into ised.assignments. Returns row count."""
    with zipfile.ZipFile(zip_path) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        assert csvs, "no CSV inside TAFL zip"
        member = csvs[0]
        zf.extract(member, zip_path.parent)
        csv_path = zip_path.parent / member
    col_struct = ", ".join(f"'f{i}': 'VARCHAR'" for i in range(1, 62))
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS ised")
        # headerless, double-quoted, UTF-8 BOM (encoding handled by duckdb)
        con.execute(
            "CREATE OR REPLACE TABLE ised.raw AS "
            f"SELECT * FROM read_csv('{csv_path}', header=false, "
            "strict_mode=false, columns={" + col_struct + "})"
        )
        named = ", ".join(
            f"f{i} AS {name}" for i, name in sorted(ISED_COLUMNS.items())
        )
        con.execute(
            f"""
            CREATE OR REPLACE VIEW ised.assignments AS
            SELECT {named},
                   try_cast(f2 AS DOUBLE) AS freq_mhz_num,
                   try_cast(f41 AS DOUBLE) AS lat_num,
                   try_cast(f42 AS DOUBLE) AS lon_num
            FROM ised.raw
            """
        )
        n = con.execute("SELECT count(*) FROM ised.raw").fetchone()[0]
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ised_licensee "
            "ON ised.raw (f55)"
        )
    finally:
        con.close()
    logger.info("ised.assignments: %d rows", n)
    return n


def load_ofcom(db_path: Path, csv_path: Path) -> int:
    """Load the Ofcom WTR register into ofcom.licences (header-driven)."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS ofcom")
        con.execute(
            "CREATE OR REPLACE TABLE ofcom.raw AS "
            f"SELECT * FROM read_csv('{csv_path}', header=true, "
            "strict_mode=false, all_varchar=true)"
        )
        # normalize a few key columns with a defensive resolver: headers have
        # shifted across WTR releases, so match case-insensitively
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='ofcom' AND table_name='raw'").fetchall()]

        def pick(*cands: str) -> str | None:
            lower = {c.lower(): c for c in cols}
            for cand in cands:
                if cand.lower() in lower:
                    return f'"{lower[cand.lower()]}"'
            return None

        mapping = {
            "licence_number": pick("Licence number", "Licence Number"),
            "licensee": pick("Licencee Company", "Licensee", "Licensee Name",
                             "Licencee Surname"),
            "freq_hz": pick("Frequency(Hz)", "Frequency (Hz)", "Frequency"),
            "station_type": pick("Station Type", "Station type"),
            "status": pick("Status"),
            "lat": pick("Latitude(Deg)", "Latitude (Deg)", "Latitude"),
            "lon": pick("Longitude(Deg)", "Longitude (Deg)", "Longitude"),
            "erp": pick("ERP dBW", "Antenna ERP", "ERP(dBW)", "ERP"),
            "emission": pick("Emission Code"),
        }
        selects = [f"{src} AS {dst}" for dst, src in mapping.items() if src]
        selects += [f'try_cast({mapping["lat"]} AS DOUBLE) AS lat_num'
                    if mapping.get("lat") else "NULL AS lat_num",
                    f'try_cast({mapping["lon"]} AS DOUBLE) AS lon_num'
                    if mapping.get("lon") else "NULL AS lon_num"]
        con.execute(
            f"CREATE OR REPLACE VIEW ofcom.licences AS "
            f"SELECT *, {', '.join(selects)} FROM ofcom.raw"
        )
        n = con.execute("SELECT count(*) FROM ofcom.raw").fetchone()[0]
    finally:
        con.close()
    logger.info("ofcom.licences: %d rows", n)
    return n


ACMA_TABLES = ("licence", "site", "client", "device_details")


def load_acma(db_path: Path, zip_path: Path) -> dict[str, int]:
    """Load the ACMA RRL zip into acma.{licence,site,client,device_details}."""
    counts: dict[str, int] = {}
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS acma")
        with zipfile.ZipFile(zip_path) as zf:
            names = {Path(n).stem.lower(): n for n in zf.namelist()
                     if n.lower().endswith(".csv")}
            for table in ACMA_TABLES:
                member = names.get(table)
                if not member:
                    logger.warning("ACMA zip missing %s.csv", table)
                    continue
                zf.extract(member, zip_path.parent)
                csv_path = zip_path.parent / member
                con.execute(
                    f"CREATE OR REPLACE TABLE acma.{table} AS "
                    f"SELECT * FROM read_csv('{csv_path}', header=true, "
                    "strict_mode=false, all_varchar=true)"
                )
                counts[table] = con.execute(
                    f"SELECT count(*) FROM acma.{table}").fetchone()[0]
        if "site" in counts:
            con.execute(
                """
                CREATE OR REPLACE VIEW acma.sites AS
                SELECT *, try_cast("LATITUDE" AS DOUBLE) AS lat_num,
                       try_cast("LONGITUDE" AS DOUBLE) AS lon_num
                FROM acma.site
                """
            )
    finally:
        con.close()
    logger.info("acma: %s", counts)
    return counts
