# spectrecon

RF licensing intelligence: entity, site, and frequency pivots over the FCC's
public Universal Licensing System (ULS) bulk data.

Give it a company, a callsign, an FRN, or a coordinate — it answers with the
full licensed RF footprint: every license, site, tower link, and frequency.

All data comes from the FCC's anonymous, keyless, weekly public-access
snapshots at `https://data.fcc.gov/download/pub/uls/complete/`. No scraping,
no API keys, no ToS exposure — the whole dataset is downloaded and queried
locally in DuckDB.

## Install

```sh
uv sync
```

## Usage

```sh
# see what radio services are available
uv run spectrecon services

# pull weekly snapshots (l_paging.zip is ~6 MB; l_LMpriv.zip is ~420 MB)
uv run spectrecon download paging microwave
uv run spectrecon download --all

# parse and load into data/spectrecon.db (DuckDB)
uv run spectrecon build

# pivot 1: company or FRN -> full RF footprint
uv run spectrecon entity "SPACEX"
uv run spectrecon entity 0023616904 --frn
uv run spectrecon entity "MOTOROLA" --json > moto.json

# pivot 2: coordinate -> every licensed site nearby
uv run spectrecon geo "34.0522,-118.2437" --radius-km 10

# pivot 2b: coordinate -> registered antenna structures (ASR towers)
uv run spectrecon towers "34.0522,-118.2437" --radius-km 5
uv run spectrecon towers "28.5623,-80.5774" --radius-km 30 --owner "SPACEX"

# callsign -> license + entity + sites + frequencies
uv run spectrecon lookup W1AW

# ad-hoc SQL when the pivots aren't enough
uv run spectrecon sql "select radio_service_code, count(*) from uls.HD group by 1"
```

## The watch pivot (change detection)

The FCC also publishes rolling per-weekday delta zips — every record in one
is, by definition, a license that changed that day. `watch` turns that into a
persistent, queryable change feed:

```sh
# ingest the rolling week of deltas for every service, record what's new
uv run spectrecon watch

# alert surfaces: entity watchlist or geofence
uv run spectrecon watch --entity "SPACEX"
uv run spectrecon watch --near "28.5623,-80.5774" --radius-km 50   # Cape Canaveral

# also ingest application deltas — filings are intent, weeks before grants
uv run spectrecon watch --apps --entity "SPACEX"

# full stored feed, machine-readable
uv run spectrecon watch --history --json
```

Events persist in the `watch_events` table (one row per changed license and
per changed site, with coordinates), deduplicated across runs. New grants
show up same-day — put `spectrecon watch --entity ...` on a cron and you have
an RF change-detection alarm. The weekly snapshot stays the source of truth;
deltas are the alerting layer, not an upsert.

Data lives in `./data` by default; set `SPECTRECON_DATA_DIR` to relocate
(e.g. an external drive — a full `--all` build stages several GB).

## The pivot graph

- **entity -> footprint**: `EN` (entity/FRN) join `HD` (license header/status/service)
- **entity -> sites**: `LO` (locations, DMS coordinates -> decimal degrees in `uls.sites`)
- **site -> RF detail**: `FR` (frequencies/power), `AN` (antennas/heights),
  `EM` (emission designators), `PA`/`SG` (microwave paths — who links to whom)
- **point -> towers**: `asr.towers` — ASR registration (RA) joined to
  coordinates (CO) and owner (EN), 197k structures with heights
- **callsign -> everything**: `uls.licenses` view (HD join EN)

Everything keys off `unique_system_identifier`; `EN.frn` is the cross-service
entity resolver.

## Database layout

All loaded record types become tables in the `uls` schema (`uls.HD`, `uls.EN`,
`uls.LO`, `uls.FR`, ...), with a `_service` provenance column. Two derived
views do the heavy lifting:

- `uls.licenses` — HD joined to EN with parsed dates
- `uls.sites` — LO with decimal `lat`/`lon` columns
- `asr.towers` — ASR registrations joined to coordinates and owners

ASR tower files reuse ULS record codes with different layouts, so they load
into their own `asr` schema from `ASR_TABLES` in `src/spectrecon/schema.py`
(FCC publishes no per-position doc for these; layout is verified against the
weekly file, with positional names where semantics are unconfirmed).

Column layouts follow the FCC's official Public Access Database Definitions
(v6.0.0). See `src/spectrecon/schema.py`.

## Roadmap

- **ELS**: experimental licenses/STAs (no bulk file; ELS public search)
- **IBFS/Part 25**: satellite earth stations and gateways
- **ASR applications**: `a_tower.zip` (pending registrations, layouts unverified)
- **international**: ISED (CA), Ofcom WTR (UK), ACMA RRL (AU)

## Data notes

- Snapshots update weekly (posted Sunday/Monday); anonymous HTTPS, no key.
- Files are pipe-delimited, headerless, latin-1-ish; `build` normalizes rows
  to the official column counts before loading.
- Full `download --all` is roughly 1 GB of zips (several GB staged).

## License

MIT. Record layouts adapted from the ISC-licensed
[uls2sqlite](https://git.sr.ht/~cg/uls2sqlite), generated from FCC (U.S.
government work) field definitions.
