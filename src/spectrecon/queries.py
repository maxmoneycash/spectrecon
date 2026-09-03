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
    return (
        "6371 * 2 * asin(sqrt("
        f"power(sin(radians({alias}.lat - {lat}) / 2), 2) + "
        f"cos(radians({lat})) * cos(radians({alias}.lat)) * "
        f"power(sin(radians({alias}.lon - ({lon})) / 2), 2)))"
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
