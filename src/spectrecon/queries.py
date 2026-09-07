"""Query pivots over the built ULS database. No pandas — rows come back as dicts."""

import math
from pathlib import Path

import duckdb


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} not found - run `spectrecon build` first")
    return duckdb.connect(str(db_path), read_only=True)


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _haversine(lat: float, lon: float, alias: str = "s") -> str:
    return _haversine_expr(str(lat), str(lon), alias)


def _haversine_expr(lat_expr: str, lon_expr: str, alias: str) -> str:
    """Haversine km from {alias}.lat/lon to the given SQL expressions."""
    return (
        "6371 * 2 * asin(sqrt("
        f"power(sin(radians({alias}.lat - ({lat_expr})) / 2), 2) + "
        f"cos(radians({lat_expr})) * cos(radians({alias}.lat)) * "
        f"power(sin(radians({alias}.lon - ({lon_expr})) / 2), 2)))"
    )


def _has_table(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        ).fetchone()[0]
    )


def satellite(con: duckdb.DuckDBPyConnection, query: str):
    """Satellite registry search by US callsign or ITU name."""
    if not _has_table(con, "ibfs", "satellites"):
        return []
    return _rows(
        con,
        """
        SELECT * FROM ibfs.satellites
        WHERE upper(callsign) LIKE ? OR upper(name) LIKE ?
        ORDER BY name
        """,
        [f"%{query.strip().upper()}%"] * 2,
    )


def ibfs_filings(con: duckdb.DuckDBPyConnection, query: str,
                 exact_frn: bool = False):
    """IBFS (satellite/earth-station/214) filings for an entity name or FRN."""
    if not _has_table(con, "ibfs", "filings"):
        return []
    q = query.strip()
    if exact_frn:
        return _rows(con, "SELECT * FROM ibfs.filings WHERE frn = ?", [q])
    return _rows(
        con,
        "SELECT * FROM ibfs.filings WHERE upper(entity_name) LIKE ?",
        [f"%{q.upper()}%"],
    )


def lookup(con: duckdb.DuckDBPyConnection, callsign: str) -> dict:
    """Full picture for one callsign: license, entity, sites, frequencies."""
    cs = callsign.strip().upper()
    out = {
        "licenses": _rows(
            con,
            "SELECT * FROM uls.licenses WHERE upper(call_sign) = ? "
            "ORDER BY last_action_date DESC",
            [cs],
        ),
        "sites": _rows(con, "SELECT * FROM uls.sites WHERE upper(call_sign) = ?", [cs]),
        "frequencies": [],
    }
    has_fr = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='uls' AND table_name='FR'"
    ).fetchone()[0]
    if has_fr:
        out["frequencies"] = _rows(
            con,
            """
            SELECT call_sign, location_number, antenna_number, class_station_code,
                   try_cast(frequency_assigned AS DOUBLE) AS freq_mhz,
                   try_cast(frequency_upper_band AS DOUBLE) AS freq_upper_mhz,
                   try_cast(power_output AS DOUBLE) AS power_w,
                   try_cast(power_erp AS DOUBLE) AS erp_w,
                   transmitter_make, transmitter_model, _service
            FROM uls.FR WHERE upper(call_sign) = ?
            ORDER BY freq_mhz
            """,
            [cs],
        )
    return out


def entity(con: duckdb.DuckDBPyConnection, query: str, exact_frn: bool = False) -> dict:
    """Entity pivot: one company name or FRN -> full license footprint."""
    q = query.strip()
    where = "frn = ?" if exact_frn else "upper(entity_name) LIKE ?"
    param = q if exact_frn else f"%{q.upper()}%"
    licenses = _rows(con, f"SELECT * FROM uls.licenses WHERE {where}", [param])

    usis = [r["unique_system_identifier"] for r in licenses]
    sites = []
    if usis:
        placeholders = ", ".join("?" for _ in usis)
        sites = _rows(
            con,
            f"SELECT * FROM uls.sites WHERE unique_system_identifier IN ({placeholders})",
            usis,
        )

    def count_by(rows, key):
        out: dict[str, int] = {}
        for r in rows:
            v = r.get(key) or "(none)"
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    summary = {
        "license_count": len({r["unique_system_identifier"] for r in licenses}),
        "site_count": len(sites),
        "frns": sorted({r["frn"] for r in licenses if r.get("frn")}),
        "names": sorted({r["entity_name"] for r in licenses if r.get("entity_name")}),
        "by_service": count_by(licenses, "radio_service_code"),
        "by_status": count_by(licenses, "license_status"),
        "by_state": count_by(licenses, "state"),
    }
    return {"summary": summary, "licenses": licenses, "sites": sites}


def geo(con: duckdb.DuckDBPyConnection, lat: float, lon: float, radius_km: float):
    """Point pivot: every licensed site within radius_km of a coordinate.

    Bounding-box prefilter keeps the haversine off most of the table.
    """
    dist = _haversine(lat, lon)
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * abs(math.cos(math.radians(lat))))
    return _rows(
        con,
        f"""
        SELECT {dist} AS dist_km, s.call_sign, s.lat, s.lon,
               s.location_city, s.location_state, s.location_name, s.structure_type,
               s.radius_km AS licensed_radius_km,
               l.entity_name, l.license_status, l.radio_service_code, l.frn
        FROM uls.sites s
        JOIN uls.licenses l USING (unique_system_identifier)
        WHERE s.lat BETWEEN {lat - dlat} AND {lat + dlat}
          AND s.lon BETWEEN {lon - dlon} AND {lon + dlon}
          AND {dist} <= {radius_km}
        ORDER BY dist_km
        """,
    )


def towers(
    con: duckdb.DuckDBPyConnection,
    lat: float,
    lon: float,
    radius_km: float,
    owner: str | None = None,
    view: str = "asr.towers",
):
    """Tower pivot: ASR-registered structures (or pending applications) near
    a coordinate."""
    dist = _haversine(lat, lon, alias="t")
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * abs(math.cos(math.radians(lat))))
    where = f"""
        t.lat BETWEEN {lat - dlat} AND {lat + dlat}
        AND t.lon BETWEEN {lon - dlon} AND {lon + dlon}
        AND {dist} <= {radius_km}
    """
    params: list = []
    if owner:
        where += " AND upper(t.owner_name) LIKE ?"
        params.append(f"%{owner.strip().upper()}%")
    return _rows(
        con,
        f"""
        SELECT {dist} AS dist_km, t.registration_number, t.owner_name,
               t.structure_type, t.height_overall_m, t.height_structure_m,
               t.ground_elevation_m, t.city, t.state, t.status_code,
               t.application_purpose, t.date_constructed, t.lat, t.lon
        FROM {view} t
        WHERE {where}
        ORDER BY dist_km
        """,
        params,
    )


def gaps(
    con: duckdb.DuckDBPyConnection,
    lat: float,
    lon: float,
    radius_km: float,
    coverage_km: float = 0.25,
    alias: str = "s",
    table: str = "uls.sites",
) -> list:
    """Rows of `table` near a point with no capture observation nearby."""
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * abs(math.cos(math.radians(lat))))
    has_obs = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='capture' AND table_name='observations'"
    ).fetchone()[0]
    uncovered = ""
    if has_obs:
        cov_deg = coverage_km / 111.0
        uncovered = f"""
            AND NOT EXISTS (
                SELECT 1 FROM capture.observations o
                WHERE o.lat BETWEEN {alias}.lat - {cov_deg}
                      AND {alias}.lat + {cov_deg}
                  AND o.lon BETWEEN {alias}.lon - {cov_deg}
                      AND {alias}.lon + {cov_deg}
                  AND {_haversine_expr('o.lat', 'o.lon', alias)} <= {coverage_km}
            )
        """
    return uncovered, (
        f"{alias}.lat BETWEEN {lat - dlat} AND {lat + dlat} "
        f"AND {alias}.lon BETWEEN {lon - dlon} AND {lon + dlon} "
        f"AND {_haversine_expr(str(lat), str(lon), alias)} <= {radius_km}"
    )


def coverage_gaps(
    con: duckdb.DuckDBPyConnection,
    lat: float,
    lon: float,
    radius_km: float,
    coverage_km: float = 0.25,
) -> dict:
    """Pre-drive planner: licensed sites/towers in a region with NO captured
    observation within coverage_km — the places worth driving next."""
    s_unc, s_region = gaps(con, lat, lon, radius_km, coverage_km, alias="s")
    sites = _rows(
        con,
        f"""
        SELECT {_haversine(lat, lon)} AS dist_km, s.call_sign, s.lat, s.lon,
               s.location_city, s.location_state, s.location_name,
               l.entity_name, l.radio_service_code
        FROM uls.sites s
        JOIN uls.licenses l USING (unique_system_identifier)
        WHERE {s_region} {s_unc}
        ORDER BY dist_km
        """,
    )
    towers: list = []
    has_towers = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='asr' AND table_name='towers'"
    ).fetchone()[0]
    if has_towers:
        t_unc, t_region = gaps(con, lat, lon, radius_km, coverage_km, alias="t")
        towers = _rows(
            con,
            f"""
            SELECT {_haversine(lat, lon, alias='t')} AS dist_km,
                   t.registration_number, t.owner_name, t.structure_type,
                   t.height_overall_m, t.city, t.state, t.lat, t.lon
            FROM asr.towers t
            WHERE {t_region} {t_unc}
            ORDER BY dist_km
            """,
        )
    return {"sites": sites, "towers": towers}


def stats(con: duckdb.DuckDBPyConnection):
    tables = _rows(
        con,
        """
        SELECT table_schema AS schema_name, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('uls', 'main')
        ORDER BY table_schema, table_name
        """,
    )
    out = []
    for t in tables:
        name = f"{t['schema_name']}.{t['table_name']}"
        qname = f'"{t["schema_name"]}"."{t["table_name"]}"'
        n = con.execute(f"SELECT count(*) FROM {qname}").fetchone()[0]
        out.append({"table": name, "rows": n})
    return out
