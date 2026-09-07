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
# Only r_tower.zip (registrations) has verified layouts so far.
TOWER_FILES = {"r_tower.zip"}

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
            path = dl.download_service(s, RAW_DIR, force=force)
            console.print(f"[green]{s}[/green] -> {path}")
        except dl.DownloadError as e:
            err.print(f"[red]{s}: {e}[/red]")
            raise typer.Exit(1)


@app.command()
def build(db: Path = typer.Option(DB_PATH, "--db")) -> None:
    """Normalize data/raw/*.zip and load them into DuckDB."""
    zips = sorted(RAW_DIR.glob("*.zip"))
    if not zips:
        err.print(f"[red]no zips in {RAW_DIR} - run `spectrecon download` first[/red]")
        raise typer.Exit(1)
    # ASR tower files reuse ULS record codes with different layouts — they get
    # their own staging namespace, layout set, and schema.
    tower_zips = [z for z in zips if z.name in TOWER_FILES]
    uls_zips = [z for z in zips if z.name not in TOWER_FILES]
    if uls_zips:
        counts = build_mod.normalize_zips(uls_zips, STAGE_DIR)
        for rt, n in sorted(counts.items()):
            console.print(f"staged {rt:3s} {n:>10,} rows")
        build_mod.load_duckdb(db, STAGE_DIR, counts)
    if tower_zips:
        from .schema import ASR_TABLES
        asr_stage = DATA_DIR / "stage_asr"
        counts = build_mod.normalize_zips(tower_zips, asr_stage,
                                          tables=ASR_TABLES, name_prefix="asr_")
        for rt, n in sorted(counts.items()):
            console.print(f"staged asr {rt:3s} {n:>10,} rows")
        build_mod.load_duckdb(db, asr_stage, counts, schema="asr",
                              tables=ASR_TABLES, name_prefix="asr_")
    console.print(f"[green]built {db}[/green]")


@app.command()
def towers(coords: str = typer.Argument(..., help="Center point as 'lat,lon'."),
           radius: float = typer.Option(25.0, "--radius-km"),
           owner: Optional[str] = typer.Option(None, "--owner",
                                               help="Filter by owner name."),
           db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt) -> None:
    """Tower pivot: registered antenna structures near a coordinate (ASR)."""
    try:
        lat_s, lon_s = coords.replace(" ", "").split(",")
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        raise typer.BadParameter("coords must be 'lat,lon', e.g. 34.0522,-118.2437")
    with queries.connect(db) as con:
        rows = queries.towers(con, lat, lon, radius, owner=owner)
    _emit(rows, as_json,
          ["dist_km", "registration_number", "owner_name", "structure_type",
           "height_overall_m", "city", "state", "status_code", "lat", "lon"],
          title=f"towers within {radius} km of {lat},{lon}")


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
def entity(query: str, frn: bool = typer.Option(False, "--frn", help="Exact FRN match."),
           db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt) -> None:
    """Entity pivot: company name or FRN -> full license footprint."""
    with queries.connect(db) as con:
        result = queries.entity(con, query, exact_frn=frn)
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    console.print_json(json.dumps(result["summary"], default=str))
    _emit(result["licenses"], False,
          ["call_sign", "license_status", "radio_service_code", "entity_name",
           "grant_date", "expired_date", "state"], title="licenses")


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
