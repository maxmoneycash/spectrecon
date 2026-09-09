"""MCP server: expose the pivots as tools any agent can call.

Run:  spectrecon mcp          (stdio transport)
Env:  SPECTRECON_DB to point at a non-default database.

All tools are read-only. `sql` is restricted to SELECT statements on the
read-only connection.
"""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import queries

DEFAULT_DB = Path(os.environ.get("SPECTRECON_DATA_DIR", "data")) / "spectrecon.db"
DB_PATH = Path(os.environ.get("SPECTRECON_DB", str(DEFAULT_DB)))

mcp = FastMCP("spectrecon")


def _run(fn, *args, **kwargs) -> str:
    with queries.connect(DB_PATH) as con:
        return json.dumps(fn(con, *args, **kwargs), default=str, indent=1)


@mcp.tool()
def entity(query: str, frn: bool = False) -> str:
    """Entity pivot: company name (or exact FRN with frn=true) -> full RF
    footprint across ULS licenses and IBFS satellite/earth-station filings."""
    def go(con):
        result = queries.entity(con, query, exact_frn=frn)
        result["ibfs_filings"] = queries.ibfs_filings(con, query, exact_frn=frn)
        return result
    return _run(go)


@mcp.tool()
def geo(lat: float, lon: float, radius_km: float = 25.0) -> str:
    """Point pivot: every licensed emitter site within radius_km of a
    coordinate, with owner, service, and distance."""
    return _run(queries.geo, lat, lon, radius_km)


@mcp.tool()
def towers(lat: float, lon: float, radius_km: float = 25.0,
           owner: str | None = None, applications: bool = False) -> str:
    """Tower pivot: ASR-registered structures (or pending applications with
    applications=true) near a coordinate, with owner and height."""
    view = "asrapp.applications" if applications else "asr.towers"
    return _run(queries.towers, lat, lon, radius_km, owner=owner, view=view)


@mcp.tool()
def lookup(callsign: str) -> str:
    """Full record for one callsign: license, licensee, sites, frequencies."""
    return _run(queries.lookup, callsign)


@mcp.tool()
def satellite(query: str) -> str:
    """Satellite registry search (IBFS) by US callsign or ITU name."""
    return _run(queries.satellite, query)


@mcp.tool()
def coverage_gaps(lat: float, lon: float, radius_km: float = 25.0,
                  coverage_m: float = 250.0) -> str:
    """Pre-drive planner: licensed sites/towers with no imported wardriving
    capture observation within coverage_m."""
    def go(con):
        return queries.coverage_gaps(con, lat, lon, radius_km,
                                     coverage_m / 1000.0)
    return _run(go)


@mcp.tool()
def watch_feed(entity: str | None = None, frn: str | None = None,
               near: str | None = None, radius_km: float = 25.0,
               hours: float | None = None) -> str:
    """The change feed: licenses/applications that changed per FCC daily
    deltas. Optionally filter by entity, FRN, or geofence ('lat,lon'), or
    only events first seen in the last `hours`."""
    from . import watch as watch_mod
    near_pt = None
    if near:
        a, b = near.replace(" ", "").split(",")
        near_pt = (float(a), float(b))
    since = None
    if hours is not None:
        from datetime import datetime, timedelta
        since = datetime.now() - timedelta(hours=hours)
    with queries.connect(DB_PATH) as con:
        rows = watch_mod.query_events(con, entity=entity, frn=frn,
                                      near=near_pt, radius_km=radius_km,
                                      since=since)
    return json.dumps(rows, default=str, indent=1)


@mcp.tool()
def survey(lat: float, lon: float, radius_km: float = 5.0) -> str:
    """Everything RF near a point across every loaded registry (ULS, ASR,
    IBFS, ISED, Ofcom, ACMA, mesh) — one list with a source column."""
    return _run(queries.survey, lat, lon, radius_km)


@mcp.tool()
def mesh_near(lat: float, lon: float, radius_km: float = 25.0,
              source: str | None = None) -> str:
    """Geofenced mesh nodes near a coordinate, sorted by distance.
    Optionally filter to one source (meshtastic, meshcore, ttn_gateway,
    aredn, reticulum)."""
    from . import mesh as mesh_mod
    return _run(mesh_mod.near, lat, lon, radius_km, source=source)


@mcp.tool()
def mesh_search(query: str) -> str:
    """Name/node-id substring search across every mesh source."""
    from . import mesh as mesh_mod
    return _run(mesh_mod.search, query)


@mcp.tool()
def mesh_stats() -> str:
    """Per-source mesh node counts and latest fetch time."""
    from . import mesh as mesh_mod
    return _run(mesh_mod.stats)


@mcp.tool()
def lora_heard(capture_file: str | None = None) -> str:
    """Unique Meshtastic transmitters in imported Lilyshark .lscap frames,
    joined to mesh.nodes when a map dump is loaded."""
    from . import lscap as lscap_mod
    with queries.connect(DB_PATH) as con:
        return json.dumps(lscap_mod.heard(con, capture_file), default=str,
                          indent=1)


@mcp.tool()
def identify_rf(ssid: str = "", name: str = "", bssid: str = "",
                auth_mode: str = "") -> str:
    """Identify a Wi-Fi/BLE sighting against the RF gadget catalog
    (Flipper Zero, WiFi Pineapple, ESP32 Marauder, Meshtastic, …).
    Returns null if nothing matches."""
    from . import gadgets as gadgets_mod
    hit = gadgets_mod.identify(ssid=ssid, name=name, bssid=bssid,
                               auth_mode=auth_mode)
    if hit is None:
        return json.dumps(None)
    return json.dumps({"id": hit.id, "label": hit.label,
                       "family": hit.family, "via": hit.via})


@mcp.tool()
def sql(query: str) -> str:
    """Ad-hoc read-only SQL against the database (SELECT only).
    Schemas: uls (licenses), asr (towers), asrapp (tower applications),
    ibfs (satellites/earth stations), capture (wardriving imports),
    ref (OUI vendors), main (watch_events), mesh (mesh-network nodes)."""
    if not query.strip().lower().startswith("select"):
        return json.dumps({"error": "SELECT only"})
    with queries.connect(DB_PATH) as con:
        cur = con.execute(query)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return json.dumps(rows, default=str, indent=1)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
