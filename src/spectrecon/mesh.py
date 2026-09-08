"""Public mesh-network map aggregators: Meshtastic, MeshCore, TTN, AREDN,
Reticulum.

Unlike the file-based pipelines these are live, keyless JSON APIs fetched by
`spectrecon mesh update` straight into the mesh.nodes table — nothing lands
in data/raw. Per-source failures warn and skip so one dead map never blocks
the others.

Source notes (all verified live 2026-09):
- Meshtastic  meshmap.net: one JSON object keyed by node id; latitude and
  longitude are signed ints x1e-7; seenBy maps MQTT topic -> unix ts.
- MeshCore    map.meshcore.io: JSON array; type 1=client/companion,
  2=repeater, 3=room server; radio params under params{freq,bw,cr,sf}.
- TTN         mapper.packetbroker.net: LoRaWAN gateways seen by Packet
  Broker; coordinates nested under location{latitude,longitude,altitude}.
- AREDN       worldmap.arednmesh.org: amateur-radio mesh; a JavaScript file
  (`const out = {...};`) wrapping ~1.7 MB of JSON with link_info topology.
- Reticulum   rmap.world: serves a broken certificate chain, so its fetch
  skips TLS verification (read-only public data; a warning is logged).
"""

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx

from . import __version__
from .queries import _has_table, _haversine_expr, _rows

logger = logging.getLogger(__name__)

USER_AGENT = f"spectrecon/{__version__} (mesh network map aggregator)"

MESHCORE_TYPES = {1: "client", 2: "repeater", 3: "room server"}


def _iso(ts) -> str | None:
    """Unix timestamp -> UTC ISO string; None/garbage -> None."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _detail(d: dict) -> str:
    return json.dumps({k: v for k, v in d.items() if v is not None},
                      default=str)


def parse_meshtastic(payload: dict) -> list[dict]:
    rows = []
    for node_id, n in payload.items():
        if n.get("latitude") is None or n.get("longitude") is None:
            continue
        seen_by = n.get("seenBy") or {}
        seen_ts = [t for t in seen_by.values() if isinstance(t, (int, float))]
        rows.append({
            "node_id": str(node_id),
            "name": n.get("longName") or n.get("shortName"),
            "node_type": n.get("role"),
            "hw_or_radio": n.get("hwModel"),
            "lat": n["latitude"] / 1e7,
            "lon": n["longitude"] / 1e7,
            "altitude": _f(n.get("altitude")),
            "first_seen": _iso(min(seen_ts)) if seen_ts else None,
            "last_seen": _iso(n.get("lastDeviceMetrics")
                              or (max(seen_ts) if seen_ts else None)),
            "detail": _detail({
                "shortName": n.get("shortName"),
                "batteryLevel": n.get("batteryLevel"),
                "precision": n.get("precision"),
                "region": n.get("region"),
                "modemPreset": n.get("modemPreset"),
                "seenBy": sorted(seen_by) or None,
            }),
        })
    return rows


def parse_meshcore(payload: list) -> list[dict]:
    rows = []
    for n in payload:
        lat, lon = _f(n.get("adv_lat")), _f(n.get("adv_lon"))
        if lat is None or lon is None:
            continue
        params = n.get("params") or {}
        radio = None
        if params.get("freq"):
            radio = f"{params['freq']}MHz"
            if params.get("sf"):
                radio += f" SF{params['sf']}"
            if params.get("bw"):
                radio += f" BW{params['bw']}kHz"
        rows.append({
            "node_id": n.get("public_key"),
            "name": n.get("adv_name"),
            "node_type": MESHCORE_TYPES.get(n.get("type"), str(n.get("type"))),
            "hw_or_radio": radio,
            "lat": lat,
            "lon": lon,
            "altitude": None,
            "first_seen": n.get("inserted_date"),
            "last_seen": n.get("last_advert") or n.get("updated_date"),
            "detail": _detail({"params": params or None,
                               "source": n.get("source")}),
        })
    return rows


def parse_ttn(payload: list) -> list[dict]:
    rows = []
    for g in payload:
        loc = g.get("location") or {}
        lat, lon = _f(loc.get("latitude")), _f(loc.get("longitude"))
        if lat is None or lon is None:
            continue
        node_id = g.get("id") or g.get("eui")
        rows.append({
            "node_id": node_id,
            "name": node_id,
            "node_type": "gateway",
            "hw_or_radio": g.get("antennaPlacement"),
            "lat": lat,
            "lon": lon,
            "altitude": _f(loc.get("altitude")),
            "first_seen": None,
            "last_seen": g.get("updatedAt"),
            "detail": _detail({
                "eui": g.get("eui"),
                "netID": g.get("netID"),
                "tenantID": g.get("tenantID"),
                "clusterID": g.get("clusterID"),
                "online": g.get("online"),
                "accuracy": loc.get("accuracy"),
            }),
        })
    return rows


def parse_aredn(text: str) -> list[dict]:
    """Strip the `const out = {...};` JS wrapper and parse the JSON inside."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise MeshError("AREDN out.js: no JSON object found in wrapper")
    payload = json.loads(text[start:end + 1])
    rows = []
    for entry in payload.get("nodeInfo", []):
        d = entry.get("data") or {}
        lat, lon = _f(d.get("lat")), _f(d.get("lon"))
        if lat is None or lon is None:
            continue
        details = d.get("node_details") or {}
        links = d.get("link_info") or {}
        rows.append({
            "node_id": d.get("node"),
            "name": d.get("node"),
            "node_type": ("supernode" if details.get("mesh_supernode")
                          else "node"),
            "hw_or_radio": details.get("hardware"),
            "lat": lat,
            "lon": lon,
            "altitude": None,
            "first_seen": None,
            "last_seen": _iso(d.get("lastseen")),
            "detail": _detail({
                "firmware": details.get("firmware_version"),
                "description": details.get("description"),
                "links": {l.get("hostname"): l.get("linkType")
                          for l in links.values() if isinstance(l, dict)}
                         or None,
            }),
        })
    return rows


def parse_reticulum(payload: dict) -> list[dict]:
    rows = []
    for n in payload.get("nodes", []):
        lat, lon = _f(n.get("lat")), _f(n.get("lon"))
        if lat is None or lon is None:
            continue
        rows.append({
            "node_id": n.get("hash"),
            "name": n.get("display_name"),
            "node_type": n.get("node_type"),
            "hw_or_radio": n.get("iface_type"),
            "lat": lat,
            "lon": lon,
            "altitude": _f(n.get("altitude")),
            "first_seen": n.get("first_seen"),
            "last_seen": n.get("last_seen"),
            "detail": _detail({
                "frequency": n.get("frequency"),
                "bandwidth": n.get("bandwidth"),
                "spreading_factor": n.get("spreading_factor"),
                "coding_rate": n.get("coding_rate"),
                "transport_enabled": n.get("transport_enabled"),
                "announce_count": n.get("announce_count"),
            }),
        })
    return rows


# source name -> (description, URL, parser). Parsers take the decoded JSON
# (or raw text for AREDN's JS wrapper) and return normalized node dicts.
SOURCES: dict[str, tuple[str, str, object]] = {
    "meshtastic": ("Meshtastic node map (meshmap.net)",
                   "https://meshmap.net/nodes.json", parse_meshtastic),
    "meshcore": ("MeshCore node map (map.meshcore.io)",
                 "https://map.meshcore.io/api/v1/nodes?short=1",
                 parse_meshcore),
    "ttn_gateway": ("TTN/LoRaWAN gateways (Packet Broker mapper)",
                    "https://mapper.packetbroker.net/api/v2/gateways",
                    parse_ttn),
    "aredn": ("AREDN amateur radio mesh world map",
              "https://worldmap.arednmesh.org/data/out.js", parse_aredn),
    "reticulum": ("Reticulum network map (rmap.world)",
                  "https://rmap.world/?json=1", parse_reticulum),
}


class MeshError(Exception):
    pass


def fetch_source(name: str) -> list[dict]:
    """Fetch one source's live map and parse it into normalized node dicts."""
    try:
        desc, url, parser = SOURCES[name]
    except KeyError:
        raise MeshError(f"Unknown mesh source {name!r}. "
                        f"Available: {', '.join(sorted(SOURCES))}")
    verify = True
    if name == "reticulum":
        logger.warning("rmap.world serves a broken certificate chain; "
                       "fetching %s with TLS verification DISABLED "
                       "(read-only public data)", url)
        verify = False
    logger.info("Fetching %s", desc)
    with httpx.Client(timeout=120, follow_redirects=True, verify=verify,
                      headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if name == "aredn":
            return parser(resp.text)
        return parser(resp.json())


def load_nodes(db_path: Path, source: str, rows: list[dict]) -> int:
    """Replace a source's rows in mesh.nodes. Returns rows loaded."""
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS mesh")
        con.execute("""
            CREATE TABLE IF NOT EXISTS mesh.nodes (
                source TEXT, node_id TEXT, name TEXT, node_type TEXT,
                hw_or_radio TEXT, lat DOUBLE, lon DOUBLE, altitude DOUBLE,
                first_seen TEXT, last_seen TEXT, detail TEXT,
                fetched_at TIMESTAMP DEFAULT now()
            )""")
        con.execute("DELETE FROM mesh.nodes WHERE source = ?", [source])
        con.executemany(
            "INSERT INTO mesh.nodes (source, node_id, name, node_type, "
            "hw_or_radio, lat, lon, altitude, first_seen, last_seen, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(source, r["node_id"], r["name"], r["node_type"],
              r["hw_or_radio"], r["lat"], r["lon"], r["altitude"],
              r["first_seen"], r["last_seen"], r["detail"]) for r in rows])
    finally:
        con.close()
    logger.info("mesh.nodes[%s]: %d rows", source, len(rows))
    return len(rows)


def update_source(db_path: Path, name: str) -> int:
    """Fetch + load one source. Raises on failure; callers warn and skip."""
    return load_nodes(db_path, name, fetch_source(name))


def stats(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Per-source node counts and latest fetch time."""
    if not _has_table(con, "mesh", "nodes"):
        return []
    return _rows(con, """
        SELECT source, count(*) AS nodes, max(fetched_at) AS last_fetch
        FROM mesh.nodes GROUP BY 1 ORDER BY 1
    """)


def near(con: duckdb.DuckDBPyConnection, lat: float, lon: float,
         radius_km: float, source: str | None = None) -> list[dict]:
    """Mesh nodes within radius_km of a coordinate, sorted by distance."""
    if not _has_table(con, "mesh", "nodes"):
        return []
    dist = _haversine_expr(str(lat), str(lon), "n")
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * abs(math.cos(math.radians(lat))))
    where = f"""
        n.lat BETWEEN {lat - dlat} AND {lat + dlat}
        AND n.lon BETWEEN {lon - dlon} AND {lon + dlon}
        AND {dist} <= {radius_km}
    """
    params: list = []
    if source:
        where += " AND n.source = ?"
        params.append(source)
    return _rows(con, f"""
        SELECT {dist} AS dist_km, n.*
        FROM mesh.nodes n WHERE {where} ORDER BY dist_km
    """, params)


def search(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    """Name/node-id substring search across every mesh source."""
    if not _has_table(con, "mesh", "nodes"):
        return []
    q = f"%{query.strip().upper()}%"
    return _rows(con, """
        SELECT * FROM mesh.nodes
        WHERE upper(name) LIKE ? OR upper(node_id) LIKE ?
        ORDER BY source, name
    """, [q, q])


def all_nodes(con: duckdb.DuckDBPyConnection,
              source: str | None = None) -> list[dict]:
    """Every geocoded node (for full-map renders)."""
    if not _has_table(con, "mesh", "nodes"):
        return []
    where = "lat IS NOT NULL AND lon IS NOT NULL"
    params: list = []
    if source:
        where += " AND source = ?"
        params.append(source)
    return _rows(con,
                 f"SELECT * FROM mesh.nodes WHERE {where} "
                 "ORDER BY source, name", params)
