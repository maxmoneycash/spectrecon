"""Watch pivot: ingest FCC daily license deltas and keep a persistent change feed.

The FCC publishes rolling per-weekday delta zips (.../daily/l_{svc}_{dow}.zip)
containing only licenses touched that day. Every record in a delta is by
definition a change — so detection is just: ingest, persist unseen
(delta_file, usi, location_number) events, and filter by entity or geofence.

The weekly snapshot (uls schema) remains the source of truth; daily events
are the alerting layer, not an upsert.
"""

import logging
import math
from pathlib import Path

import duckdb

from .build import load_duckdb, normalize_zips

logger = logging.getLogger(__name__)

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS watch_events(
    delta_file VARCHAR,
    kind VARCHAR,  -- 'license' | 'site'
    usi VARCHAR,
    location_number VARCHAR,
    call_sign VARCHAR,
    entity_name VARCHAR,
    frn VARCHAR,
    radio_service_code VARCHAR,
    license_status VARCHAR,
    grant_date VARCHAR,
    last_action_date VARCHAR,
    lat DOUBLE,
    lon DOUBLE,
    city VARCHAR,
    state VARCHAR,
    first_seen TIMESTAMP DEFAULT now(),
    PRIMARY KEY (delta_file, usi, location_number)
)
"""


def ingest_daily(db_path: Path, zip_paths: list[Path], stage_dir: Path) -> dict[str, int]:
    """Normalize daily delta zips and load them into the `daily` schema."""
    counts = normalize_zips(zip_paths, stage_dir)
    load_duckdb(db_path, stage_dir, counts, schema="daily")
    return counts


def record_events(con: duckdb.DuckDBPyConnection) -> int:
    """Persist unseen delta records into watch_events. Returns new-event count."""
    con.execute(EVENTS_DDL)
    prev_max = con.execute("SELECT max(first_seen) FROM watch_events").fetchone()[0]

    def has_view(name: str) -> bool:
        return bool(
            con.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'daily' AND table_name = ?",
                [name],
            ).fetchone()[0]
        )

    if has_view("licenses"):
        # license-level events (one per changed license per delta file)
        con.execute(
            """
            INSERT OR IGNORE INTO watch_events
                (delta_file, kind, usi, location_number, call_sign, entity_name, frn,
                 radio_service_code, license_status, grant_date, last_action_date,
                 lat, lon, city, state)
            SELECT _service, 'license', unique_system_identifier, '', call_sign,
                   entity_name, frn, radio_service_code, license_status,
                   grant_date::VARCHAR, last_action_date::VARCHAR,
                   NULL, NULL, city, state
            FROM daily.licenses
            """
        )
    if has_view("licenses") and has_view("sites"):
        # site-level events (one per location of a changed license, with coords)
        con.execute(
            """
            INSERT OR IGNORE INTO watch_events
                (delta_file, kind, usi, location_number, call_sign, entity_name, frn,
                 radio_service_code, license_status, grant_date, last_action_date,
                 lat, lon, city, state)
            SELECT s._service, 'site', s.unique_system_identifier, s.location_number,
                   s.call_sign, l.entity_name, l.frn, l.radio_service_code,
                   l.license_status, l.grant_date::VARCHAR, l.last_action_date::VARCHAR,
                   s.lat, s.lon, s.location_city, s.location_state
            FROM daily.sites s
            JOIN daily.licenses l USING (unique_system_identifier)
            """
        )

    if prev_max is None:
        return con.execute("SELECT count(*) FROM watch_events").fetchone()[0]
    return con.execute(
        "SELECT count(*) FROM watch_events WHERE first_seen > ?", [prev_max]
    ).fetchone()[0]


def query_events(
    con: duckdb.DuckDBPyConnection,
    entity: str | None = None,
    frn: str | None = None,
    near: tuple[float, float] | None = None,
    radius_km: float = 25.0,
    since=None,
) -> list[dict]:
    """Read the change feed with optional entity/FRN/geofence filters."""
    where, params = ["1=1"], []
    if entity:
        where.append("upper(entity_name) LIKE ?")
        params.append(f"%{entity.strip().upper()}%")
    if frn:
        where.append("frn = ?")
        params.append(frn.strip())
    if since is not None:
        where.append("first_seen > ?")
        params.append(since)
    if near:
        lat, lon = near
        where.append("lat IS NOT NULL")
        dlat = radius_km / 111.0
        dlon = radius_km / max(1.0, 111.0 * abs(math.cos(math.radians(lat))))
        where.append(f"lat BETWEEN {lat - dlat} AND {lat + dlat}")
        where.append(f"lon BETWEEN {lon - dlon} AND {lon + dlon}")
        where.append(
            "6371 * 2 * asin(sqrt("
            f"power(sin(radians(lat - {lat}) / 2), 2) + "
            f"cos(radians({lat})) * cos(radians(lat)) * "
            f"power(sin(radians(lon - ({lon})) / 2), 2))) <= {radius_km}"
        )
    cur = con.execute(
        f"""
        SELECT delta_file, kind, call_sign, entity_name, frn, radio_service_code,
               license_status, grant_date, last_action_date, lat, lon, city, state,
               first_seen
        FROM watch_events
        WHERE {' AND '.join(where)}
        ORDER BY first_seen DESC, entity_name
        """,
        params,
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
