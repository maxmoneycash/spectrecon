"""Load FCC ULS zip archives into DuckDB.

Pipeline: zip members -> normalized pipe files (exact column counts) ->
DuckDB tables in the `uls` schema -> derived views (`uls.sites`,
`uls.licenses`) used by the query commands.
"""

import logging
import re
import zipfile
from pathlib import Path

import duckdb

from .schema import ASR_TABLES, get_columns

logger = logging.getLogger(__name__)

# Tables to index on the join key after loading.
INDEX_TABLES = ("HD", "EN", "LO", "FR", "AN", "EM")


def normalize_zips(
    zip_paths: list[Path],
    stage_dir: Path,
    tables: dict | None = None,
    name_prefix: str = "",
) -> dict[str, int]:
    """Stream .dat members out of the zips into per-record-type staged files.

    FCC quirks handled here: latin-1-ish encoding, no header row, rows with
    missing or extra trailing fields, and blank/garbage lines. Returns a
    record-type -> row count map. `tables` overrides the record layout set
    (ASR reuses ULS record codes with different layouts), and `name_prefix`
    namespaces the staged files so the two never collide.
    """
    stage_dir.mkdir(parents=True, exist_ok=True)
    writers: dict[str, "object"] = {}
    counts: dict[str, int] = {}
    skipped: dict[str, int] = {}

    def columns_for(rt: str):
        if tables is not None:
            return tables.get(rt)
        return get_columns(rt)

    def writer_for(rt: str):
        if rt not in writers:
            writers[rt] = open(stage_dir / f"{name_prefix}{rt}.dat", "w",
                               encoding="utf-8", newline="\n")
            counts[rt] = 0
        return writers[rt]

    try:
        for zip_path in zip_paths:
            logger.info("Staging %s", zip_path.name)
            service = re.sub(r"\.zip$", "", zip_path.name)
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith(".dat"):
                        continue
                    rt = Path(name).stem.upper()
                    columns = columns_for(rt)
                    if columns is None:
                        skipped[rt] = skipped.get(rt, 0) + 1
                        continue
                    expected = len(columns)
                    w = writer_for(rt)
                    with zf.open(name) as raw:
                        for bline in raw:
                            # Byte iteration splits only on \n; free-text fields can
                            # carry lone \r, which DuckDB reads as a line break.
                            line = (
                                bline.decode("latin-1", errors="replace")
                                .rstrip("\r\n")
                                .replace("\r", " ")
                            )
                            if len(line) < 4:
                                continue
                            fields = [f.strip() for f in line.split("|")]
                            if len(fields) < 3:
                                continue  # continuation/garbage line
                            if len(fields) > expected:
                                fields = fields[:expected]
                            elif len(fields) < expected:
                                fields.extend([""] * (expected - len(fields)))
                            # pipe chars inside free-text fields would corrupt the
                            # row on reload; replace them (rare, only in comments)
                            w.write("|".join(f.replace("|", " ") for f in fields))
                            w.write("|")
                            w.write(service)
                            w.write("\n")
                            counts[rt] += 1
    finally:
        for w in writers.values():
            w.close()

    for rt, n in sorted(skipped.items()):
        logger.info("Skipped unknown record type %s (%d files)", rt, n)
    return counts


def load_duckdb(
    db_path: Path,
    stage_dir: Path,
    counts: dict[str, int],
    schema: str = "uls",
    tables: dict | None = None,
    name_prefix: str = "",
) -> None:
    """Bulk-load staged files into DuckDB and build derived views."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for rt, n in sorted(counts.items()):
            stage_file = stage_dir / f"{name_prefix}{rt}.dat"
            columns = tables.get(rt) if tables is not None else get_columns(rt)
            assert columns is not None
            # every staged file has a trailing service provenance column
            col_struct = ", ".join(f"'{c}': 'VARCHAR'" for c in columns)
            col_struct += ", '_service': 'VARCHAR'"
            sql = (
                f"CREATE OR REPLACE TABLE {schema}.{rt} AS "
                f"SELECT * FROM read_csv('{stage_file}', "
                "delim='|', quote='', escape='', header=false, nullstr='', "
                "strict_mode=false, "
                "columns={" + col_struct + "})"
            )
            con.execute(sql)
            logger.info("Loaded %s.%-3s %9d rows", schema, rt, n)

        # All-VARCHAR loads keep ingestion bulletproof; cast on read in views.
        for rt in counts:
            if rt in INDEX_TABLES:
                con.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{schema}_{rt.lower()}_usi "
                    f"ON {schema}.{rt} (unique_system_identifier)"
                )
        if schema == "asr":
            _build_asr_views(con, counts)
            con.execute(
                "CREATE OR REPLACE TABLE _build_info_asr AS SELECT now() AS built_at"
            )
            return
        if "EN" in counts:
            con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{schema}_en_callsign "
                f"ON {schema}.EN (call_sign)"
            )
            con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{schema}_en_name "
                f"ON {schema}.EN (entity_name)"
            )
            con.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{schema}_en_frn "
                f"ON {schema}.EN (frn)"
            )

        if "LO" in counts:
            con.execute(
                f"""
                CREATE OR REPLACE VIEW {schema}.sites AS
                SELECT
                    unique_system_identifier, call_sign, location_number,
                    location_type_code, location_class_code,
                    location_address, location_city, location_county, location_state,
                    location_name, structure_type, tower_registration_number,
                    try_cast(ground_elevation AS DOUBLE) AS ground_elevation_m,
                    try_cast(radius_of_operation AS DOUBLE) AS radius_km,
                    (try_cast(lat_degrees AS DOUBLE)
                        + try_cast(lat_minutes AS DOUBLE) / 60
                        + try_cast(lat_seconds AS DOUBLE) / 3600)
                        * CASE WHEN lat_direction = 'S' THEN -1 ELSE 1 END AS lat,
                    (try_cast(long_degrees AS DOUBLE)
                        + try_cast(long_minutes AS DOUBLE) / 60
                        + try_cast(long_seconds AS DOUBLE) / 3600)
                        * CASE WHEN long_direction = 'W' THEN -1 ELSE 1 END AS lon,
                    _service
                FROM {schema}.LO
                WHERE lat_degrees IS NOT NULL AND long_degrees IS NOT NULL
                """
            )

        if "HD" in counts and "EN" in counts:
            con.execute(
                f"""
                CREATE OR REPLACE VIEW {schema}.licenses AS
                SELECT
                    h.unique_system_identifier, h.call_sign,
                    h.license_status, h.radio_service_code,
                    try_strptime(h.grant_date, '%m/%d/%Y')::DATE AS grant_date,
                    try_strptime(h.expired_date, '%m/%d/%Y')::DATE AS expired_date,
                    try_strptime(h.cancellation_date, '%m/%d/%Y')::DATE AS cancellation_date,
                    try_strptime(h.effective_date, '%m/%d/%Y')::DATE AS effective_date,
                    try_strptime(h.last_action_date, '%m/%d/%Y')::DATE AS last_action_date,
                    e.entity_type, e.entity_name, e.frn,
                    e.street_address, e.city, e.state, e.zip_code, e.email, e.phone,
                    h._service
                FROM {schema}.HD h
                -- EN carries several rows per license; 'L' is the licensee record
                JOIN (SELECT * FROM {schema}.EN WHERE entity_type = 'L') e
                  USING (unique_system_identifier)
                """
            )

        if schema == "uls":
            con.execute(
                """
                CREATE OR REPLACE TABLE _build_info AS
                SELECT now() AS built_at
                """
            )
    finally:
        con.close()


def _build_asr_views(con: "duckdb.DuckDBPyConnection", counts: dict[str, int]) -> None:
    """ASR-specific indexes and the asr.towers view (RA + CO coords + EN owner)."""
    if "RA" not in counts:
        return
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_asr_ra_reg ON asr.RA (registration_number)"
    )
    if "CO" in counts:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_asr_co_reg ON asr.CO (registration_number)"
        )
    if "EN" in counts:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_asr_en_reg ON asr.EN (registration_number)"
        )
    co_join = (
        """LEFT JOIN (
               SELECT * FROM asr.CO
               QUALIFY row_number() OVER (
                   PARTITION BY registration_number
                   ORDER BY (coord_type = 'T') DESC) = 1
           ) c USING (registration_number)"""
        if "CO" in counts
        else "LEFT JOIN (SELECT NULL AS registration_number, NULL AS coord_type, "
        "NULL AS lat_degrees, NULL AS lat_minutes, NULL AS lat_seconds, "
        "NULL AS lat_direction, NULL AS long_degrees, NULL AS long_minutes, "
        "NULL AS long_seconds, NULL AS long_direction WHERE 1=0) c "
        "USING (registration_number)"
    )
    en_join = (
        "LEFT JOIN (SELECT * FROM asr.EN WHERE entity_type = 'O') e "
        "USING (registration_number)"
        if "EN" in counts
        else "LEFT JOIN (SELECT NULL AS registration_number, NULL AS entity_name, "
        "NULL AS phone, NULL AS email WHERE 1=0) e USING (registration_number)"
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW asr.towers AS
        SELECT
            r.registration_number, r.unique_system_identifier, r.file_number,
            r.application_purpose, r.status_code, r.structure_type,
            r.date_received, r.date_granted, r.date_constructed, r.last_action_date,
            try_cast(r.height_structure_m AS DOUBLE) AS height_structure_m,
            try_cast(r.ground_elevation_m AS DOUBLE) AS ground_elevation_m,
            try_cast(r.height_overall_m AS DOUBLE) AS height_overall_m,
            r.street_address, r.city, r.state, r.county_fips, r.zip_code,
            (try_cast(c.lat_degrees AS DOUBLE)
                + try_cast(c.lat_minutes AS DOUBLE) / 60
                + try_cast(c.lat_seconds AS DOUBLE) / 3600)
                * CASE WHEN c.lat_direction = 'S' THEN -1 ELSE 1 END AS lat,
            (try_cast(c.long_degrees AS DOUBLE)
                + try_cast(c.long_minutes AS DOUBLE) / 60
                + try_cast(c.long_seconds AS DOUBLE) / 3600)
                * CASE WHEN c.long_direction = 'W' THEN -1 ELSE 1 END AS lon,
            e.entity_name AS owner_name, e.phone AS owner_phone,
            e.email AS owner_email,
            r._service
        FROM asr.RA r
        {co_join}
        {en_join}
        """
    )
