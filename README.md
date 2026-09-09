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
# from GitHub, as a tool
uv tool install git+https://github.com/maxmoneycash/spectrecon

# or from a clone, for development
uv sync
```

## What it looks like

```console
$ spectrecon entity "SPACEX"
ULS:    40 licenses, 79 sites, 14 FRNs (8 name variants resolved)
IBFS:   2,923 filings — incl. S3157: "SpaceX requests U.S. market access
        for its German-licensed direct-to-cell..."

$ spectrecon geo "28.5623,-80.5774" --radius-km 50    # Cape Canaveral
2,080 licensed sites in 3.4s — nearest are SpaceX's own WRVW506/WRVW742,
30 m from the pad

$ spectrecon sat "STARLINK"
Gen2 Starlink | NGSO | active

$ spectrecon watch --apps --entity "SPACEX"
new: application (YG, Hawthorne CA) filed 2026-09-01 — intent before grants

$ spectrecon debrief --html drive.html   # after a wardrive
SkyTel Ops -> attributed to SkyTel Spectrum LLC (KNKK953, 0.5 km)
DesertRose Repeater -> ANOMALY: no licensed infrastructure within 2 km
```

There's also an MCP server (`spectrecon mcp`) that exposes every pivot as
agent tools over stdio, and **Spectrecon Field** (`../spectrecon-field`) — an
iPhone wardriving companion that records GPS + BLE and exports WiGLE CSV for
`import` / `debrief`.

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
uv run spectrecon towers "34.0522,-118.2437" --radius-km 5 --applications

# pivot 2c: satellites (IBFS registry) and cross-system entity resolution
uv run spectrecon download ibfs && uv run spectrecon build --only ibfs
uv run spectrecon sat "STARLINK"
uv run spectrecon entity "SPACEX"   # ULS licenses AND IBFS satellite filings
uv run spectrecon entity "BOEING"   # ...plus ISED (Canada) and ACMA (Australia)

# pivot 2d: everything RF near a point, all registries at once
uv run spectrecon survey "43.6532,-79.3832" --radius-km 2   # Toronto
uv run spectrecon survey --at "-33.8688,151.2093"           # southern lat: use --at

# mesh networks: live node maps from five keyless JSON APIs
uv run spectrecon mesh update                               # all five sources
uv run spectrecon mesh update meshtastic aredn              # or just some
uv run spectrecon mesh stats
uv run spectrecon mesh near "34.0522,-118.2437" --radius-km 25
uv run spectrecon mesh near --at "-33.8688,151.2093" --source reticulum
uv run spectrecon mesh search "W1AW"
uv run spectrecon mesh map --html mesh.html --at "34.0522,-118.2437" --radius-km 50

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

For cron, one command does everything — freshness-checked downloads, rebuild
of every loaded pipeline, and the daily delta ingest (licenses + apps):

```sh
# weekly, e.g. Monday 07:12:  12 7 * * 1  cd /path/spectrecon && uv run spectrecon refresh
uv run spectrecon refresh                  # full: download + rebuild + watch + mesh
uv run spectrecon refresh --no-rebuild     # quick: just deltas + freshness checks
uv run spectrecon refresh --no-mesh        # skip live mesh-map fetches
```

## The debrief pivot (wardriving captures)

Wardriving rigs (WiGLE app, Kismet, Biscuit/Cerberus-class ESP32 devices)
all log the same formats. `import` + `debrief` join those captures to the
licensing graph — the "who did I actually hear?" layer WiGLE itself
doesn't have:

```sh
uv run spectrecon download oui        # one-time: IEEE vendor registry
uv run spectrecon import WIGLE005.CSV # WiGLE CSV...
uv run spectrecon import rig.kismet   # ...or Kismet SQLite logs
uv run spectrecon debrief
uv run spectrecon debrief --html drive.html      # Leaflet map to screenshot
uv run spectrecon debrief --geojson drive.geojson # or Earth/QGIS
```

For every unique BSSID (at its strongest-RSSI position):

- nearest licensed sites (callsign, entity, service) and nearest ASR tower
- **vendor**: OUI lookup (Espressif = ESP32 rigs, Ubiquiti, ...) plus
  randomized-MAC detection — split fixed infrastructure from transient phones
- **attribution**: SSID tokens matching a nearby licensee's entity name
  (`SkyTel Ops` heard 0.5 km from a SkyTel Spectrum LLC site -> attributed)
- **anomalies**: emitters with no licensed infrastructure within
  `--anomaly-km` (default 2) — rogue/interesting by construction

## The gaps pivot (pre-drive planner)

Compares your imported captures against the licensing graph and lists what
you have NOT sniffed yet:

```sh
uv run spectrecon gaps "34.0522,-118.2437" --radius-km 10 --html targets.html
```

Every licensed site and tower with no observation within `--coverage-m`
(default 250 m) becomes a target — orange markers are licensed emitters,
purple are ASR towers. Run it before the drive, debrief after.

## International registries

```sh
uv run spectrecon download ised ofcom acma
uv run spectrecon build --only ised    # or: ofcom, acma
```

- **ISED (Canada)** — SMS authorization extract (monthly): 900k assignments
  with licensee, callsign, frequency, ERP, and decimal WGS84 coordinates in
  `ised.assignments`. Open Government Licence – Canada.
- **Ofcom (UK)** — Wireless Telegraphy Register (nightly): 206k licences in
  `ofcom.licences`. Open Government Licence (UK).
- **ACMA (Australia)** — RRL daily relational dump: `acma.licence`,
  `acma.sites` (130k geocoded), `acma.client`, `acma.device_details` (2.2M
  frequency rows). **Custom licence**: prohibits redistributing
  natural-person licensee personal info and any spam use — local query only.
  See `LICENCE.TXT` inside the zip.

Data lives in `./data` by default; set `SPECTRECON_DATA_DIR` to relocate
(e.g. an external drive — a full `--all` build stages several GB).

## Mesh networks

`mesh update` aggregates five public mesh/LoRaWAN node maps into a common
`mesh.nodes` table (source, node_id, name, type, radio, position, first/last
seen, plus a JSON `detail` column for source-specific extras like radio
params, online status, and MQTT topics). All sources are anonymous and
keyless; unlike the FCC pipelines these are live JSON APIs fetched directly —
nothing is staged in `data/raw`. Per-source failures warn and skip, so one
dead map never blocks the rest.

| source | what | cadence |
|---|---|---|
| `meshtastic` | meshmap.net node map (MQTT-reported positions) | live |
| `meshcore` | map.meshcore.io nodes (clients, repeaters, room servers) | live |
| `ttn_gateway` | TTN/LoRaWAN gateways seen by Packet Broker's mapper | live |
| `aredn` | worldmap.arednmesh.org — **amateur radio** mesh (ham callsigns, link topology) | live |
| `reticulum` | rmap.world Reticulum network map | live |

Caveats: **Reticulum's rmap.world serves a broken TLS certificate chain**, so
its fetch disables verification (read-only public data; a warning is logged).
**AREDN publishes JavaScript, not JSON** (`const out = {...};`) — the loader
strips the wrapper. Mesh nodes also join the `survey` sweep (source `mesh`)
once loaded.

## The pivot graph

- **entity -> footprint**: `EN` (entity/FRN) join `HD` (license header/status/service)
- **entity -> satellites**: `entity` also searches `ibfs.filings` — satellite
  market-access, earth-station, and section-214 applications keyed by FRN
- **entity -> sites**: `LO` (locations, DMS coordinates -> decimal degrees in `uls.sites`)
- **site -> RF detail**: `FR` (frequencies/power), `AN` (antennas/heights),
  `EM` (emission designators), `PA`/`SG` (microwave paths — who links to whom)
- **point -> towers**: `asr.towers` — ASR registration (RA) joined to
  coordinates (CO) and owner (EN), 197k structures with heights
- **point -> earth stations**: `ibfs.sites` — 72k geocoded gateway/VSAT
  locations; `ibfs.frequencies` chains site -> antenna -> emission/MHz range
- **callsign -> everything**: `uls.licenses` view (HD join EN);
  `ibfs.satellites` for the 833-entry satellite registry (`sat` command)

Everything keys off `unique_system_identifier` in ULS; `EN.frn` /
`address.frn` resolve entities across ULS and IBFS.

## Database layout

All loaded record types become tables in the `uls` schema (`uls.HD`, `uls.EN`,
`uls.LO`, `uls.FR`, ...), with a `_service` provenance column. Two derived
views do the heavy lifting:

- `uls.licenses` — HD joined to EN with parsed dates
- `uls.sites` — LO with decimal `lat`/`lon` columns
- `asr.towers` — ASR registrations joined to coordinates and owners
- `ibfs.filings` / `ibfs.sites` / `ibfs.satellites` / `ibfs.frequencies` —
  the IBFS dump (MAIN/SITE/SPACE_STA/ADDRESS/FREQ/ANTEN), deduped and joined

ASR tower files reuse ULS record codes with different layouts, so they load
into their own `asr` schema from `ASR_TABLES` in `src/spectrecon/schema.py`
(FCC publishes no per-position doc for these; layout is verified against the
weekly file, with positional names where semantics are unconfirmed).

IBFS is a separate database: caret-terminated rows, Sybase dates, its own
table names (`IBFS_TABLES`). Layouts come from the FCC's CnvIbfs converter
source (SUSS project, via Wayback) cross-checked with the 1998 ibfs.txt DDL.
The ELS experimental search (apps.fcc.gov/oetcf/els) sits behind Akamai bot
protection that rejects non-browser clients outright, so ELS is deferred
until a browser-driven import is worth the fragility.

Column layouts follow the FCC's official Public Access Database Definitions
(v6.0.0). See `src/spectrecon/schema.py`.

## ELS (experimental licenses) — browser-captured

ELS has no bulk file and apps.fcc.gov sits behind Akamai bot protection that
rejects non-browser clients. The capture path drives your real browser:

```sh
# needs: Arc + the Playwriter extension (arc-browser / playwriter installed)
python scripts/els_export.py "Space Exploration" /tmp/els_spacex.json
uv run spectrecon els-import /tmp/els_spacex.json --query "Space Exploration"
uv run spectrecon sql "select * from els.applications where status='Pending'"
```

Results land in `els.applications`, upserted by file number — re-run the
same query later and status changes (pending -> granted/denied) update in
place, which is the ELS watch story. The results page caps at 100 rows per
query; narrow with the form's city/state/date fields for full coverage.

## Roadmap

- **IBFS watch**: the dump is labeled daily-updated, but the public mirror
  (transition.fcc.gov) has been stale since 2026-07-16; a working daily
  source (or fcc.report's mirror) would enable diff-based alerting
- **ELS pagination**: results cap at 100 rows/query; scripted narrowing
  (state × experiment-type sweeps) would give full-registry coverage

## Data notes

- Snapshots update weekly (posted Sunday/Monday); anonymous HTTPS, no key.
- Files are pipe-delimited, headerless, latin-1-ish; `build` normalizes rows
  to the official column counts before loading.
- Full `download --all` is roughly 1 GB of zips (several GB staged).

## License

MIT. Record layouts adapted from the ISC-licensed
[uls2sqlite](https://git.sr.ht/~cg/uls2sqlite), generated from FCC (U.S.
government work) field definitions.
