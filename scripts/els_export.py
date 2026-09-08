#!/usr/bin/env python3
"""Export FCC ELS Generic Search results via the user's Arc browser.

ELS (apps.fcc.gov/oetcf/els) sits behind Akamai bot protection that rejects
non-browser clients. This drives Arc through the installed Playwriter
extension — no headless browsers, no other Chrome variants.

Usage:
    python scripts/els_export.py "Space Exploration" /tmp/els_spacex.json

Then load into the database:
    uv run spectrecon els-import /tmp/els_spacex.json --query "Space Exploration"

Note: the results page caps at show_records rows (default here 100); re-run
with narrower queries (city/state/date ranges on the form) for full coverage.
"""

import base64
import json
import re
import subprocess
import sys

PLAYWRITER = "/usr/local/bin/playwriter"
SEARCH_URL = "https://apps.fcc.gov/oetcf/els/reports/GenericSearch.cfm"
RESULT_TABLE_INDEX = 17  # the results grid (tables 0-16 are page chrome)

CHUNK = 3000


def pw(session: str, code: str) -> str:
    out = subprocess.run(
        [PLAYWRITER, "-s", session, "-e", code],
        capture_output=True, text=True, timeout=180,
    )
    if out.returncode != 0:
        raise RuntimeError(f"playwriter failed: {out.stderr.strip()[:300]}")
    return out.stdout


def logs(stdout: str) -> list[str]:
    return [m.group(1) for line in stdout.splitlines()
            if (m := re.match(r"^\[log\] (.*)$", line))]


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    query, out_path = sys.argv[1], sys.argv[2]

    # arc-browser connect prints the new session id on its last line
    connect = subprocess.run(
        ["arc-browser", "connect", SEARCH_URL],
        capture_output=True, text=True, timeout=120,
    )
    m = re.search(r"Session (\d+) is ready", connect.stdout)
    if not m:
        raise RuntimeError(f"arc-browser connect failed: {connect.stdout}")
    session = m.group(1)

    pw(session, f"""
        await page.goto('{SEARCH_URL}', {{waitUntil: 'domcontentloaded',
                                           timeout: 60000}});
        await page.locator('input[name=name_licensee]').fill({json.dumps(query)});
        await page.locator('input[name=show_records]').fill('100');
        await page.locator('input[type=submit][value="Start Search"]').click();
        await page.waitForURL(/GenericSearchResult/, {{timeout: 60000}});
        await page.waitForLoadState('domcontentloaded');
    """)

    pw(session, f"""
        state.b64 = await page.evaluate(() => {{
          const t = document.querySelectorAll('table')[{RESULT_TABLE_INDEX}];
          const rows = [...t.querySelectorAll('tr')].slice(1).map(r => {{
            const c = [...r.querySelectorAll('td')]
                          .map(td => td.innerText.trim());
            return c.length >= 12 && c[6] ? {{
              file_number: c[6], call_sign: c[7], applicant: c[8],
              receipt_date: c[9], status: c[10], status_date: c[11],
            }} : null;
          }}).filter(Boolean);
          return btoa(unescape(encodeURIComponent(JSON.stringify(rows))));
        }});
    """)

    n = int(logs(pw(session, "console.log('LEN' + state.b64.length)"))[0][3:])
    parts = []
    for start in range(0, n, CHUNK):
        line = logs(pw(session, f"console.log('C:' + state.b64.slice({start}, {start + CHUNK}))"))[0]
        parts.append(line[2:])
    rows = json.loads(base64.b64decode("".join(parts)))
    with open(out_path, "w") as f:
        json.dump(rows, f, indent=1)
    print(f"{len(rows)} rows -> {out_path}")
    print(f"next: uv run spectrecon els-import {out_path} --query {json.dumps(query)}")


if __name__ == "__main__":
    main()
