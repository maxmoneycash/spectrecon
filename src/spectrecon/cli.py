"""spectrecon command line."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

import duckdb
import typer
from rich.console import Console
from rich.table import Table

from . import build as build_mod
from . import download as dl
from . import ingest as ingest_mod
from . import queries
from . import watch as watch_mod

app = typer.Typer(
    name="spectrecon",
    help="RF licensing intelligence over FCC ULS public access data.",
    no_args_is_help=True,
)
console = Console()
err = Console(stderr=True)

DATA_DIR = Path(os.environ.get("SPECTRECON_DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"
STAGE_DIR = DATA_DIR / "stage"
DAILY_DIR = DATA_DIR / "daily"
DB_PATH = DATA_DIR / "spectrecon.db"

# ASR bulk files handled by the separate asr pipeline (see build command).
TOWER_FILES = {"r_tower.zip"}
# Pending ASR applications — same layouts, separate asrapp schema.
TOWER_APP_FILES = {"a_tower.zip"}
# IBFS full-database dump (satellites, earth stations, section 214).
IBFS_FILES = {"IBFS.zip"}

JsonOpt = typer.Option(False, "--json", help="Machine-readable JSON output.")


def _emit(rows: list[dict], as_json: bool, columns: list[str] | None = None,
          title: str | None = None) -> None:
    if as_json:
        console.print_json(json.dumps(rows, default=str))
        return
    if not rows:
        console.print("[dim]no rows[/dim]")
        return
    columns = columns or list(rows[0].keys())
    table = Table(title=title)
    for c in columns:
        table.add_column(c, overflow="fold")
    for r in rows:
        table.add_row(*("" if r.get(c) is None else str(r.get(c)) for c in columns))
    console.print(table)
    console.print(f"[dim]{len(rows)} rows[/dim]")


@app.command()
def services() -> None:
    """List the FCC radio services that can be downloaded."""
    table = Table("service", "description", "weekly file")
    for name, (desc, filename) in sorted(dl.SERVICES.items()):
        table.add_row(name, desc, filename)
    console.print(table)


@app.command()
def download(
    service: Optional[list[str]] = typer.Argument(None),
    all_services: bool = typer.Option(False, "--all", help="Download every service."),
    force: bool = typer.Option(False, "--force", help="Re-download even if sizes match."),
) -> None:
    """Download weekly ULS snapshots from data.fcc.gov into data/raw/."""
    if all_services:
        targets = sorted(dl.SERVICES)
    elif service:
        targets = [s.lower() for s in service]
    else:
        raise typer.BadParameter("name services or pass --all")
    for s in targets:
        try:
            if s in dl.EXTRA_DOWNLOADS:
                path = dl.download_extra(s, RAW_DIR, force=force)
                if s == "oui":
                    n = ingest_mod.load_oui(DB_PATH, path)
                    console.print(f"[green]{s}[/green] -> {path} ({n:,} vendors)")
                else:
                    console.print(f"[green]{s}[/green] -> {path}")
                continue
            path = dl.download_service(s, RAW_DIR, force=force)
            console.print(f"[green]{s}[/green] -> {path}")
        except dl.DownloadError as e:
            err.print(f"[red]{s}: {e}[/red]")
            raise typer.Exit(1)


@app.command()
def build(db: Path = typer.Option(DB_PATH, "--db"),
          only: Optional[str] = typer.Option(None, "--only",
                                             help="Build just one pipeline: "
                                                  "uls, asr, or ibfs.")) -> None:
    """Normalize data/raw/*.zip and load them into DuckDB."""
    zips = sorted(RAW_DIR.glob("*.zip")) + sorted(RAW_DIR.glob("*.csv"))
    if not zips:
        err.print(f"[red]no zips in {RAW_DIR} - run `spectrecon download` first[/red]")
        raise typer.Exit(1)
    if only and only not in ("uls", "asr", "asrapp", "ibfs",
                             "ised", "ofcom", "acma"):
        raise typer.BadParameter("--only must be uls, asr, asrapp, ibfs, "
                                 "ised, ofcom, or acma")
    # ASR tower files reuse ULS record codes with different layouts; IBFS is
    # a separate database entirely (caret-terminated rows, own table names);
    # international registries are plain CSVs. Each gets its own pipeline.
    tower_zips = [z for z in zips if z.name in TOWER_FILES]
    tower_app_zips = [z for z in zips if z.name in TOWER_APP_FILES]
    ibfs_zips = [z for z in zips if z.name in IBFS_FILES]
    intl = {"ised": [z for z in zips if z.name == "TAFL_LTAF.zip"],
            "ofcom": [z for z in zips if z.name == "WTR.csv"],
            "acma": [z for z in zips if z.name == "spectra_rrl.zip"]}
    uls_zips = [z for z in zips if z.name not in TOWER_FILES
                and z.name not in TOWER_APP_FILES and z.name not in IBFS_FILES
                and all(z not in v for v in intl.values())]
    if uls_zips and only in (None, "uls"):
        counts = build_mod.normalize_zips(uls_zips, STAGE_DIR)
        for rt, n in sorted(counts.items()):
            console.print(f"staged {rt:3s} {n:>10,} rows")
        build_mod.load_duckdb(db, STAGE_DIR, counts)
    if tower_zips and only in (None, "asr"):
        from .schema import ASR_TABLES
        asr_stage = DATA_DIR / "stage_asr"
        counts = build_mod.normalize_zips(tower_zips, asr_stage,
                                          tables=ASR_TABLES, name_prefix="asr_")
        for rt, n in sorted(counts.items()):
            console.print(f"staged asr {rt:3s} {n:>10,} rows")
        build_mod.load_duckdb(db, asr_stage, counts, schema="asr",
                              tables=ASR_TABLES, name_prefix="asr_")
    if tower_app_zips and only in (None, "asrapp"):
        from .schema import ASR_TABLES
        app_stage = DATA_DIR / "stage_asrapp"
        counts = build_mod.normalize_zips(tower_app_zips, app_stage,
                                          tables=ASR_TABLES,
                                          name_prefix="asrapp_")
        for rt, n in sorted(counts.items()):
            console.print(f"staged asrapp {rt:3s} {n:>10,} rows")
        build_mod.load_duckdb(db, app_stage, counts, schema="asrapp",
                              tables=ASR_TABLES, name_prefix="asrapp_")
    if ibfs_zips and only in (None, "ibfs"):
        from .schema import IBFS_TABLES
        ibfs_stage = DATA_DIR / "stage_ibfs"
        counts = build_mod.normalize_zips(ibfs_zips, ibfs_stage,
                                          tables=IBFS_TABLES,
                                          name_prefix="ibfs_",
                                          caret_terminated=True)
        for rt, n in sorted(counts.items()):
            console.print(f"staged ibfs {rt:12s} {n:>10,} rows")
        build_mod.load_duckdb(db, ibfs_stage, counts, schema="ibfs",
                              tables=IBFS_TABLES, name_prefix="ibfs_")
    from . import intl as intl_mod
    loaders = {"ised": intl_mod.load_ised, "ofcom": intl_mod.load_ofcom,
               "acma": intl_mod.load_acma}
    for name, files in intl.items():
        if files and only in (None, name):
            n = loaders[name](db, files[0])
            console.print(f"loaded {name}: {n}")
    console.print(f"[green]built {db}[/green]")


@app.command()
def towers(coords: str = typer.Argument(..., help="Center point as 'lat,lon'."),
           radius: float = typer.Option(25.0, "--radius-km"),
           owner: Optional[str] = typer.Option(None, "--owner",
                                               help="Filter by owner name."),
           applications: bool = typer.Option(False, "--applications",
                                             help="Search pending applications "
                                                  "instead of registrations."),
           db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt) -> None:
    """Tower pivot: registered antenna structures near a coordinate (ASR)."""
    try:
        lat_s, lon_s = coords.replace(" ", "").split(",")
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        raise typer.BadParameter("coords must be 'lat,lon', e.g. 34.0522,-118.2437")
    view = "asrapp.applications" if applications else "asr.towers"
    with queries.connect(db) as con:
        rows = queries.towers(con, lat, lon, radius, owner=owner, view=view)
    what = "tower applications" if applications else "towers"
    _emit(rows, as_json,
          ["dist_km", "registration_number", "owner_name", "structure_type",
           "height_overall_m", "city", "state", "status_code", "lat", "lon"],
          title=f"{what} within {radius} km of {lat},{lon}")


@app.command()
def lookup(callsign: str, db: Path = typer.Option(DB_PATH, "--db"),
           as_json: bool = JsonOpt) -> None:
    """Full record for one callsign: license, entity, sites, frequencies."""
    with queries.connect(db) as con:
        result = queries.lookup(con, callsign)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    _emit(result["licenses"], False,
          ["call_sign", "license_status", "radio_service_code", "entity_name", "frn",
           "grant_date", "expired_date", "state"], title="licenses")
    _emit(result["sites"], False,
          ["location_number", "location_name", "location_city", "location_state",
           "lat", "lon", "structure_type", "tower_registration_number"], title="sites")
    _emit(result["frequencies"], False,
          ["freq_mhz", "freq_upper_mhz", "class_station_code", "power_w", "erp_w",
           "transmitter_make", "transmitter_model"], title="frequencies")


@app.command()
def sat(query: str, db: Path = typer.Option(DB_PATH, "--db"),
        as_json: bool = JsonOpt) -> None:
    """Satellite registry search (IBFS): US callsign or ITU name."""
    with queries.connect(db) as con:
        rows = queries.satellite(con, query)
    _emit(rows, as_json,
          ["callsign", "name", "orbit_location", "inactive_date"],
          title=f"satellites matching '{query}'")


@app.command()
def entity(query: str, frn: bool = typer.Option(False, "--frn", help="Exact FRN match."),
           db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt) -> None:
    """Entity pivot: company name or FRN -> full license footprint.

    Searches ULS licenses and, when the IBFS dump is loaded, satellite /
    earth-station / section-214 filings too (FRN is the cross-system key).
    """
    with queries.connect(db) as con:
        result = queries.entity(con, query, exact_frn=frn)
        filings = queries.ibfs_filings(con, query, exact_frn=frn)
    if as_json:
        result["ibfs_filings"] = filings
        console.print_json(json.dumps(result, default=str))
        return
    console.print_json(json.dumps(result["summary"], default=str))
    _emit(result["licenses"], False,
          ["call_sign", "license_status", "radio_service_code", "entity_name",
           "grant_date", "expired_date", "state"], title="licenses")
    if filings:
        _emit(filings, False,
              ["callsign", "file_number", "subsystem_code", "status_code",
               "entity_name", "date_filed", "date_grant", "country"],
              title="ibfs filings (satellite / earth station / 214)")


@app.command()
def geo(coords: str = typer.Argument(..., help="Center point as 'lat,lon'."),
        radius: float = typer.Option(25.0, "--radius-km"),
        db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt) -> None:
    """Point pivot: every licensed site within a radius of a coordinate."""
    try:
        lat_s, lon_s = coords.replace(" ", "").split(",")
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        raise typer.BadParameter("coords must be 'lat,lon', e.g. 34.0522,-118.2437")
    with queries.connect(db) as con:
        rows = queries.geo(con, lat, lon, radius)
    _emit(rows, as_json,
          ["dist_km", "call_sign", "entity_name", "radio_service_code",
           "location_city", "location_state", "structure_type", "lat", "lon"],
          title=f"sites within {radius} km of {lat},{lon}")


@app.command()
def watch(
    service: Optional[list[str]] = typer.Option(None, "--service",
                                                help="Limit to these services."),
    entity: Optional[str] = typer.Option(None, "--entity",
                                         help="Filter events by entity name."),
    frn: Optional[str] = typer.Option(None, "--frn", help="Filter by exact FRN."),
    near: Optional[str] = typer.Option(None, "--near",
                                       help="Geofence center as 'lat,lon'."),
    radius: float = typer.Option(25.0, "--radius-km"),
    apps: bool = typer.Option(False, "--apps",
                              help="Also ingest application deltas (intent before grants)."),
    history: bool = typer.Option(False, "--history",
                                 help="Show the full stored feed, not just new events."),
    db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt,
) -> None:
    """Watch pivot: ingest FCC daily deltas into a persistent change feed.

    Downloads the rolling week of per-day license delta files (a few KB each),
    records new/changed licenses into the watch_events table, and prints what
    is new since the last run. Re-running is idempotent; schedule it with cron.
    With --apps, also ingests application deltas as 'application' events.
    """
    targets = [s.lower() for s in service] if service else sorted(dl.DAILY_CODES)
    zips: list[Path] = []
    app_zips: list[Path] = []
    for s in targets:
        try:
            zips.extend(dl.download_daily(s, DAILY_DIR))
            if apps:
                app_zips.extend(dl.download_daily(s, DAILY_DIR, prefix="a"))
        except Exception as e:  # keep other services on failure
            err.print(f"[yellow]{s}: {e}[/yellow]")
    if not zips and not app_zips:
        err.print("[red]no daily deltas downloaded[/red]")
        raise typer.Exit(1)

    schemas = []
    if zips:
        counts = watch_mod.ingest_daily(db, zips, DATA_DIR / "daily_stage", schema="daily")
        schemas.append("daily")
        err.print(f"[dim]daily schema: {sum(counts.values()):,} records across "
                  f"{len(counts)} tables from {len(zips)} delta files[/dim]")
    if app_zips:
        counts = watch_mod.ingest_daily(db, app_zips, DATA_DIR / "apps_stage", schema="apps")
        schemas.append("apps")
        err.print(f"[dim]apps schema: {sum(counts.values()):,} records across "
                  f"{len(counts)} tables from {len(app_zips)} delta files[/dim]")

    con = duckdb.connect(str(db))
    try:
        # watermark before ingest: anything newer after record_events is new
        events_exists = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = 'watch_events'"
        ).fetchone()[0]
        since = None
        if events_exists:
            since = con.execute("SELECT max(first_seen) FROM watch_events").fetchone()[0]

        new_count = watch_mod.record_events(con, tuple(schemas))

        near_pt = None
        if near:
            try:
                a, b = near.replace(" ", "").split(",")
                near_pt = (float(a), float(b))
            except ValueError:
                raise typer.BadParameter("--near must be 'lat,lon'")
        rows = watch_mod.query_events(
            con, entity=entity, frn=frn, near=near_pt, radius_km=radius,
            since=None if history else since,
        )
    finally:
        con.close()
    err.print(f"[green]{new_count} new events recorded[/green] "
              f"(showing {len(rows)})")
    _emit(rows, as_json,
          ["delta_file", "kind", "call_sign", "entity_name", "radio_service_code",
           "license_status", "grant_date", "city", "state", "lat", "lon"],
          title="watch feed")


@app.command()
def gaps(
    coords: str = typer.Argument(..., help="Center point as 'lat,lon'."),
    radius: float = typer.Option(25.0, "--radius-km"),
    coverage_m: float = typer.Option(250.0, "--coverage-m",
                                   help="A site counts as covered if any capture "
                                        "observation is within this distance."),
    html: Optional[Path] = typer.Option(None, "--html",
                                        help="Write a target map as Leaflet HTML."),
    db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt,
) -> None:
    """Pre-drive planner: licensed sites/towers with no capture coverage.

    Compares the licensing graph against every imported wardriving capture
    and lists what you have NOT sniffed yet — the next drive's target list.
    """
    try:
        lat_s, lon_s = coords.replace(" ", "").split(",")
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        raise typer.BadParameter("coords must be 'lat,lon', e.g. 34.0522,-118.2437")
    with queries.connect(db) as con:
        result = queries.coverage_gaps(con, lat, lon, radius, coverage_m / 1000.0)
    if html:
        from . import maps as maps_mod
        maps_mod.gaps_map((lat, lon), result, html)
        err.print(f"[dim]wrote {html}[/dim]")
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    console.print(f"[bold]{len(result['sites'])} uncovered licensed sites, "
                  f"{len(result['towers'])} uncovered towers[/bold] "
                  f"within {radius} km (coverage radius {coverage_m} m)")
    _emit(result["sites"][:40], False,
          ["dist_km", "call_sign", "entity_name", "radio_service_code",
           "location_city", "location_state"],
          title="uncovered licensed sites (nearest 40)")
    _emit(result["towers"][:40], False,
          ["dist_km", "registration_number", "owner_name", "structure_type",
           "height_overall_m", "city", "state"],
          title="uncovered towers (nearest 40)")


@app.command("mcp")
def mcp_server(db: Path = typer.Option(DB_PATH, "--db")) -> None:
    """Run the MCP server (stdio) exposing every pivot as agent tools."""
    os.environ["SPECTRECON_DB"] = str(db)
    from . import mcp_server as srv
    srv.DB_PATH = db
    srv.main()


@app.command("els-import")
def els_import(
    json_file: Path = typer.Argument(...,
                                     help="JSON dump from scripts/els_export.py."),
    query: str = typer.Option("", "--query",
                              help="The ELS search that produced the file."),
    db: Path = typer.Option(DB_PATH, "--db"),
) -> None:
    """Import FCC ELS experimental-license search results (browser export).

    ELS has no bulk file and blocks non-browser clients; capture results with
    scripts/els_export.py (drives your Arc browser), then load them here.
    Re-importing the same query refreshes statuses by file number.
    """
    from . import els as els_mod
    if not json_file.exists():
        err.print(f"[red]{json_file} not found[/red]")
        raise typer.Exit(1)
    n = els_mod.load_els_json(db, json_file, query)
    console.print(f"[green]{n} ELS applications upserted[/green]")


@app.command("import")
def import_capture(
    csv_file: Path = typer.Argument(..., help="WiGLE-format wardriving CSV."),
    db: Path = typer.Option(DB_PATH, "--db"),
) -> None:
    """Import a wardriving capture (WiGLE CSV) into the capture schema."""
    if not csv_file.exists():
        err.print(f"[red]{csv_file} not found[/red]")
        raise typer.Exit(1)
    n = ingest_mod.import_capture(db, csv_file)
    if n == 0:
        err.print("[yellow]no observations parsed - is this a WiGLE-format CSV?[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]{n} observations imported from {csv_file.name}[/green]")


@app.command()
def debrief(
    capture_file: Optional[str] = typer.Option(None, "--file",
                                               help="Limit to one imported capture."),
    anomaly_km: float = typer.Option(2.0, "--anomaly-km",
                                     help="Distance from licensed infrastructure "
                                          "that flags an emitter as anomalous."),
    geojson: Optional[Path] = typer.Option(None, "--geojson",
                                           help="Write devices+flags to GeoJSON."),
    html: Optional[Path] = typer.Option(None, "--html",
                                        help="Write a self-contained Leaflet map."),
    db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt,
) -> None:
    """Drive debrief: enrich a capture against the licensing graph.

    Every unique BSSID gets nearest licensed sites/towers; SSIDs matching a
    nearby licensee's entity name are attributed; emitters with no licensed
    infrastructure within --anomaly-km are flagged as anomalous.
    """
    result = ingest_mod.debrief(db, capture_file=capture_file,
                                anomaly_km=anomaly_km)
    if geojson:
        geojson.write_text(json.dumps(ingest_mod.to_geojson(result)))
        err.print(f"[dim]wrote {geojson}[/dim]")
    if html:
        from . import maps as maps_mod
        maps_mod.debrief_map(result, html)
        err.print(f"[dim]wrote {html}[/dim]")
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    s = result["summary"]
    console.print(f"[bold]{s['unique_devices']} devices[/bold] "
                  f"({s['observations']} observations, "
                  f"{s['unique_ssids']} SSIDs) "
                  f"{s['first_obs']} -> {s['last_obs']}")
    console.print_json(json.dumps(s["devices_by_type"]))
    if result["attributions"]:
        _emit(result["attributions"], False,
              ["ssid", "entity_name", "call_sign", "radio_service_code", "dist_km"],
              title="attributed (SSID matches nearby licensee)")
    _emit(result["anomalies"], False,
          ["bssid", "ssid", "rssi", "obs_type", "sightings", "lat", "lon"],
          title=f"anomalies (no licensed infrastructure within {anomaly_km} km)")


@app.command()
def sql(query: str, db: Path = typer.Option(DB_PATH, "--db"),
        as_json: bool = JsonOpt) -> None:
    """Run ad-hoc SQL against the database (e.g. `sql 'select ... from uls.EN'`)."""
    with queries.connect(db) as con:
        cur = con.execute(query)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchall()] if cols else []
    _emit(rows, as_json)


@app.command()
def stats(db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt) -> None:
    """Table inventory and row counts."""
    with queries.connect(db) as con:
        _emit(queries.stats(con), as_json, title="tables")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app()


if __name__ == "__main__":
    main()
