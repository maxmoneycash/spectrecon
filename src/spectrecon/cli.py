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
    counts = build_mod.normalize_zips(zips, STAGE_DIR)
    for rt, n in sorted(counts.items()):
        console.print(f"staged {rt:3s} {n:>10,} rows")
    build_mod.load_duckdb(db, STAGE_DIR, counts)
    console.print(f"[green]built {db}[/green]")


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
    history: bool = typer.Option(False, "--history",
                                 help="Show the full stored feed, not just new events."),
    db: Path = typer.Option(DB_PATH, "--db"), as_json: bool = JsonOpt,
) -> None:
    """Watch pivot: ingest FCC daily deltas into a persistent change feed.

    Downloads the rolling week of per-day license delta files (a few KB each),
    records new/changed licenses into the watch_events table, and prints what
    is new since the last run. Re-running is idempotent; schedule it with cron.
    """
    targets = [s.lower() for s in service] if service else sorted(dl.DAILY_CODES)
    zips: list[Path] = []
    for s in targets:
        try:
            zips.extend(dl.download_daily(s, DAILY_DIR))
        except (dl.DownloadError, Exception) as e:  # keep other services on failure
            err.print(f"[yellow]{s}: {e}[/yellow]")
    if not zips:
        err.print("[red]no daily deltas downloaded[/red]")
        raise typer.Exit(1)

    stage = DATA_DIR / "daily_stage"
    counts = watch_mod.ingest_daily(db, zips, stage)
    err.print(f"[dim]daily schema: {sum(counts.values()):,} records across "
              f"{len(counts)} tables from {len(zips)} delta files[/dim]")

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

        new_count = watch_mod.record_events(con)

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
