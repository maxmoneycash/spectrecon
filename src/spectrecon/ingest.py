"""Wardriving capture ingest (WiGLE CSV) and drive debrief.

WiGLE CSV is the lingua franca of wardriving rigs (WiGLE app, Kismet exports,
Biscuit/Cerberus-class devices log it straight to SD). Format: two metadata
header lines (WigleWifi-1.4,...), then a CSV header row, then observations:

  MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,
  AltitudeMeters,AccuracyMeters,Type

The debrief joins each unique BSSID (at its strongest-RSSI position) against
the licensing graph (uls.sites + asr.towers) to attribute infrastructure and
flag emitters observed where nothing is licensed.
"""

import csv
import logging
import re
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

WIGLE_MAGIC = "WigleWifi"

OBS_DDL = """
CREATE TABLE IF NOT EXISTS capture.observations(
    capture_file VARCHAR,
    bssid VARCHAR,
    ssid VARCHAR,
    auth_mode VARCHAR,
    first_seen VARCHAR,
    channel INTEGER,
    rssi INTEGER,
    lat DOUBLE,
    lon DOUBLE,
    accuracy_m DOUBLE,
    obs_type VARCHAR
)
"""


def _haversine_sql(lat: float, lon: float, alias: str) -> str:
    return (
        "6371 * 2 * asin(sqrt("
        f"power(sin(radians({alias}.lat - {lat}) / 2), 2) + "
        f"cos(radians({lat})) * cos(radians({alias}.lat)) * "
        f"power(sin(radians({alias}.lon - ({lon})) / 2), 2)))"
    )


def parse_wigle_csv(path: Path) -> list[dict]:
    """Parse a WiGLE-format wardriving CSV. Tolerates Kismet/device variants."""
    rows: list[dict] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        header_seen = False
        reader = csv.reader(f)
        for record in reader:
            if not record:
                continue
            first = record[0].strip()
            if not header_seen:
                if first.startswith(WIGLE_MAGIC) or first.startswith("appRelease"):
                    continue  # metadata preamble
                if first.upper() == "MAC":
                    header_seen = True
                    continue
                continue  # skip anything before the real header
            if len(record) < 11:
                continue
            try:
                rows.append({
                    "bssid": record[0].strip().upper(),
                    "ssid": record[1].strip(),
                    "auth_mode": record[2].strip(),
                    "first_seen": record[3].strip(),
                    "channel": int(record[4]) if record[4].strip() else None,
                    "rssi": int(record[5]) if record[5].strip() else None,
                    "lat": float(record[6]),
                    "lon": float(record[7]),
                    "accuracy_m": float(record[9]) if record[9].strip() else None,
                    "obs_type": record[10].strip(),
                })
            except (ValueError, IndexError):
                continue  # malformed observation line
    return rows


def import_capture(db_path: Path, csv_path: Path) -> int:
    """Load a WiGLE CSV into capture.observations. Returns rows imported."""
    rows = parse_wigle_csv(csv_path)
    if not rows:
        return 0
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS capture")
        con.execute(OBS_DDL)
        # re-import of the same file replaces rather than duplicates
        con.execute("DELETE FROM capture.observations WHERE capture_file = ?",
                    [csv_path.name])
        con.executemany(
            "INSERT INTO capture.observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(csv_path.name, r["bssid"], r["ssid"], r["auth_mode"],
              r["first_seen"], r["channel"], r["rssi"], r["lat"], r["lon"],
              r["accuracy_m"], r["obs_type"]) for r in rows],
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_obs_bssid ON capture.observations (bssid)"
        )
    finally:
        con.close()
    return len(rows)


def debrief(
    db_path: Path,
    capture_file: str | None = None,
    anomaly_km: float = 2.0,
) -> dict:
    """Enrich a capture against the licensing graph.

    Returns summary stats, per-BSSID best positions with nearest licensed
    site/tower context, SSID->entity attributions, and the anomaly list
    (emitters with no licensed infrastructure within anomaly_km).
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        where = "WHERE capture_file = ?" if capture_file else ""
        params = [capture_file] if capture_file else []

        summary = con.execute(
            f"""
            SELECT count(*) AS observations,
                   count(DISTINCT bssid) AS unique_devices,
                   count(DISTINCT ssid) AS unique_ssids,
                   min(first_seen) AS first_obs, max(first_seen) AS last_obs
            FROM capture.observations {where}
            """,
            params,
        ).fetchone()
        cols = ["observations", "unique_devices", "unique_ssids",
                "first_obs", "last_obs"]
        summary_d = dict(zip(cols, summary))

        by_type = con.execute(
            f"SELECT obs_type, count(DISTINCT bssid) FROM capture.observations "
            f"{where} GROUP BY 1 ORDER BY 2 DESC",
            params,
        ).fetchall()
        summary_d["devices_by_type"] = {t or "?": n for t, n in by_type}

        # best (strongest) position per BSSID
        best = con.execute(
            f"""
            SELECT bssid, any_value(ssid) AS ssid, arg_max(rssi, rssi) AS rssi,
                   arg_max(lat, rssi) AS lat, arg_max(lon, rssi) AS lon,
                   any_value(obs_type) AS obs_type, count(*) AS sightings
            FROM capture.observations {where}
            GROUP BY bssid
            """,
            params,
        ).fetchall()
        best_cols = ["bssid", "ssid", "rssi", "lat", "lon", "obs_type", "sightings"]
        devices = [dict(zip(best_cols, r)) for r in best]

        # nearest licensed site + nearest tower per device (bounding-box limited)
        has_sites = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='uls' AND table_name='sites'"
        ).fetchone()[0]
        has_towers = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='asr' AND table_name='towers'"
        ).fetchone()[0]

        for d in devices:
            lat, lon = d["lat"], d["lon"]
            d["licensed_nearby"] = []
            if has_sites:
                dist = _haversine_sql(lat, lon, "s")
                d["licensed_nearby"] = con.execute(
                    f"""
                    SELECT {dist} AS dist_km, s.call_sign, l.entity_name,
                           l.radio_service_code
                    FROM uls.sites s JOIN uls.licenses l
                      USING (unique_system_identifier)
                    WHERE s.lat BETWEEN {lat - 0.02} AND {lat + 0.02}
                      AND s.lon BETWEEN {lon - 0.025} AND {lon + 0.025}
                    ORDER BY dist_km LIMIT 3
                    """
                ).fetchall()
            d["nearest_tower_km"] = None
            d["nearest_tower_owner"] = None
            if has_towers:
                dist = _haversine_sql(lat, lon, "t")
                row = con.execute(
                    f"""
                    SELECT {dist} AS dist_km, t.owner_name
                    FROM asr.towers t
                    WHERE t.lat BETWEEN {lat - 0.05} AND {lat + 0.05}
                      AND t.lon BETWEEN {lon - 0.06} AND {lon + 0.06}
                    ORDER BY dist_km LIMIT 1
                    """
                ).fetchone()
                if row:
                    d["nearest_tower_km"] = round(row[0], 3)
                    d["nearest_tower_owner"] = row[1]

        anomalies = [
            d for d in devices
            if (not d["licensed_nearby"]
                or d["licensed_nearby"][0][0] > anomaly_km)
            and (d["nearest_tower_km"] is None
                 or d["nearest_tower_km"] > anomaly_km)
        ]
        anomalies.sort(key=lambda d: d["bssid"])

        attributions = attribute_ssids(con, devices) if has_sites else []
        return {
            "summary": summary_d,
            "devices": devices,
            "attributions": attributions,
            "anomalies": anomalies,
        }
    finally:
        con.close()


def attribute_ssids(con: duckdb.DuckDBPyConnection, devices: list[dict],
                    match_km: float = 0.5) -> list[dict]:
    """SSID tokens that match a licensee entity name within match_km.

    e.g. SSID 'Starbucks WiFi' observed 80m from a site licensed to
    STARBUCKS COFFEE COMPANY -> attributed.
    """
    out = []
    for d in devices:
        ssid = d["ssid"] or ""
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", ssid) if len(t) >= 5]
        if not tokens:
            continue
        lat, lon = d["lat"], d["lon"]
        dist = _haversine_sql(lat, lon, "s")
        for tok in tokens[:3]:
            rows = con.execute(
                f"""
                SELECT {dist} AS dist_km, l.entity_name, l.call_sign,
                       l.radio_service_code
                FROM uls.sites s JOIN uls.licenses l
                  USING (unique_system_identifier)
                WHERE s.lat BETWEEN {lat - 0.01} AND {lat + 0.01}
                  AND s.lon BETWEEN {lon - 0.012} AND {lon + 0.012}
                  AND upper(l.entity_name) LIKE ?
                ORDER BY dist_km LIMIT 1
                """,
                [f"%{tok.upper()}%"],
            ).fetchone()
            if rows and rows[0] <= match_km:
                out.append({
                    "bssid": d["bssid"], "ssid": ssid,
                    "entity_name": rows[1], "call_sign": rows[2],
                    "radio_service_code": rows[3],
                    "dist_km": round(rows[0], 3),
                })
                break
    return out


def to_geojson(debrief_result: dict) -> dict:
    """Devices + anomalies as a GeoJSON FeatureCollection for Earth/QGIS."""

    def point(d, props):
        return {
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [d["lon"], d["lat"]]},
            "properties": props,
        }

    anomaly_bssids = {a["bssid"] for a in debrief_result["anomalies"]}
    attributed = {a["bssid"]: a for a in debrief_result["attributions"]}
    features = []
    for d in debrief_result["devices"]:
        props = {
            "bssid": d["bssid"], "ssid": d["ssid"], "rssi": d["rssi"],
            "type": d["obs_type"],
            "anomaly": d["bssid"] in anomaly_bssids,
        }
        if d["bssid"] in attributed:
            props["attributed_to"] = attributed[d["bssid"]]["entity_name"]
        if d.get("nearest_tower_owner"):
            props["nearest_tower_km"] = d["nearest_tower_km"]
            props["nearest_tower_owner"] = d["nearest_tower_owner"]
        features.append(point(d, props))
    return {"type": "FeatureCollection", "features": features}
