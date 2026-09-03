"""Download FCC ULS weekly public-access snapshots.

Source directory: https://data.fcc.gov/download/pub/uls/complete/
(l_*.zip = license records, a_*.zip = pending applications).
Daily deltas live under .../daily/ as {a|l}_{svc}_{mon..sun}.zip — see README roadmap.
"""

import logging
import time
from pathlib import Path

import httpx

from . import __version__

logger = logging.getLogger(__name__)

BASE_URL = "https://data.fcc.gov/download/pub/uls/complete"

# service name -> (description, weekly license zip filename)
SERVICES: dict[str, tuple[str, str]] = {
    "amateur": ("Amateur Radio", "l_amat.zip"),
    "gmrs": ("GMRS", "l_gmrs.zip"),
    "coast": ("Maritime Coast", "l_coast.zip"),
    "ship": ("Ship Station", "l_ship.zip"),
    "aircraft": ("Aircraft Station", "l_aircr.zip"),
    "cellular": ("Cellular (Part 22)", "l_cell.zip"),
    "paging": ("Paging (Part 22)", "l_paging.zip"),
    "microwave": ("Microwave (Parts 74/101, 3650-3700 MHz)", "l_micro.zip"),
    "land_mobile_private": ("Land Mobile Private (Part 90)", "l_LMpriv.zip"),
    "land_mobile_commercial": ("Land Mobile Commercial", "l_LMcomm.zip"),
    "land_mobile_broadcast": ("Land Mobile Broadcast Auxiliary", "l_LMbcast.zip"),
    "fixed_radio": ("Commercial Operators / Restricted Radiotelephone", "l_frc.zip"),
    "mds_itfs": ("BRS/EBS (formerly MDS/ITFS)", "l_mdsitfs.zip"),
}

USER_AGENT = f"spectrecon/{__version__} (FCC ULS public access data tool)"
CHUNK = 1 << 16

# Daily rolling deltas: https://data.fcc.gov/download/pub/uls/daily/l_{code}_{dow}.zip
# Codes verified against HD.radio_service_code values in the delta files.
DAILY_CODES: dict[str, str] = {
    "amateur": "am",
    "gmrs": "gm",
    "coast": "cg",
    "ship": "sh",
    "aircraft": "ac",
    "cellular": "cl",
    "paging": "pg",
    "microwave": "mw",
    "land_mobile_private": "lp",
    "land_mobile_commercial": "lc",
    "land_mobile_broadcast": "lb",
    "fixed_radio": "fc",
    "mds_itfs": "mi",
}
DAILY_BASE_URL = "https://data.fcc.gov/download/pub/uls/daily"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def download_daily(service: str, dest_dir: Path) -> list[Path]:
    """Download the rolling week of daily license deltas for a service.

    Files are tiny (KBs to a few hundred KB) and each weekday's file is
    overwritten weekly, so fetching all seven gives a rolling week of changes.
    """
    try:
        code = DAILY_CODES[service]
    except KeyError:
        raise DownloadError(f"No daily delta known for {service!r}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for dow in WEEKDAYS:
            filename = f"l_{code}_{dow}.zip"
            url = f"{DAILY_BASE_URL}/{filename}"
            dest = dest_dir / filename
            try:
                size = remote_size(client, url)
            except httpx.HTTPError as e:
                logger.warning("HEAD failed for %s: %s", url, e)
                size = None
            if dest.exists() and size is not None and dest.stat().st_size == size:
                out.append(dest)
                continue
            with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_bytes(CHUNK):
                        f.write(chunk)
            out.append(dest)
    return out


class DownloadError(Exception):
    pass


def service_url(service: str) -> str:
    try:
        _, filename = SERVICES[service]
    except KeyError:
        raise DownloadError(
            f"Unknown service {service!r}. Available: {', '.join(sorted(SERVICES))}"
        )
    return f"{BASE_URL}/{filename}"


def remote_size(client: httpx.Client, url: str) -> int | None:
    resp = client.head(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    resp.raise_for_status()
    length = resp.headers.get("content-length")
    return int(length) if length and length.isdigit() else None


def download_service(service: str, dest_dir: Path, force: bool = False) -> Path:
    """Download one service's weekly zip, skipping if local size matches remote."""
    url = service_url(service)
    filename = url.rsplit("/", 1)[1]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    partial = dest.with_suffix(dest.suffix + ".partial")

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        try:
            size = remote_size(client, url)
        except httpx.HTTPError as e:
            logger.warning("HEAD failed for %s: %s", url, e)
            size = None

        if dest.exists() and not force and size is not None and dest.stat().st_size == size:
            logger.info("Up to date: %s", filename)
            return dest

        logger.info("Downloading %s (%s)", url, _fmt_size(size))
        with client.stream("GET", url, headers={"User-Agent": USER_AGENT}) as resp:
            resp.raise_for_status()
            downloaded = 0
            last_report = time.monotonic()
            with open(partial, "wb") as f:
                for chunk in resp.iter_bytes(CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if time.monotonic() - last_report > 5:
                        logger.info("  %s: %s", filename, _fmt_size(downloaded))
                        last_report = time.monotonic()
        partial.rename(dest)
        logger.info("Saved %s (%s)", dest, _fmt_size(downloaded))
        return dest


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "unknown size"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024:
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"
