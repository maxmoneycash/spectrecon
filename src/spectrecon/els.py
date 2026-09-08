"""ELS (FCC Experimental Licensing System) import.

ELS has no bulk download and apps.fcc.gov sits behind Akamai bot protection
that rejects non-browser clients, so the capture path is browser-driven:
scripts/els_export.py drives Arc (via playwriter) to run the Generic Search
and dump results JSON; this module loads it.

Re-importing the same query refreshes statuses by file_number — that diff
over time is the ELS watch story (STA pending -> granted/denied).
"""

import json
import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS els.applications(
    file_number VARCHAR PRIMARY KEY,
    call_sign VARCHAR,
    applicant VARCHAR,
    receipt_date VARCHAR,
    status VARCHAR,
    status_date VARCHAR,
    source_query VARCHAR,
    imported_at TIMESTAMP DEFAULT now()
)
"""


def load_els_json(db_path: Path, json_path: Path, source_query: str) -> int:
    """Upsert an els_export.py JSON dump into els.applications."""
    rows = json.loads(json_path.read_text())
    if not rows:
        return 0
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS els")
        con.execute(DDL)
        con.executemany(
            """
            INSERT INTO els.applications
                (file_number, call_sign, applicant, receipt_date, status,
                 status_date, source_query, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (file_number) DO UPDATE SET
                call_sign = excluded.call_sign,
                status = excluded.status,
                status_date = excluded.status_date,
                imported_at = now()
            """,
            [(r["file_number"], r.get("call_sign"), r.get("applicant"),
              r.get("receipt_date"), r.get("status"), r.get("status_date"),
              source_query) for r in rows],
        )
        n = con.execute("SELECT count(*) FROM els.applications").fetchone()[0]
    finally:
        con.close()
    logger.info("els.applications: %d total rows after import", n)
    return len(rows)
