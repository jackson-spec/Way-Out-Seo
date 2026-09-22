"""Google Search Console pull: queries, pages, positions, CTR for the last 7 and 30 days.

Auth: a service account added as a user on the wayout.design GSC property.
Set GSC_SERVICE_ACCOUNT_JSON to either a path to the key file or the raw JSON.
Set GSC_SITE_URL to override site.json's gsc_site_url ("sc-domain:wayout.design"
for a domain property, "https://wayout.design/" for a URL-prefix property).

Queries sitting at average position 11-20 are flagged as near-miss opportunities
(Workstream 3). Without credentials this step reports needs_human and stops,
instead of pretending it checked.

Usage: python seo/scripts/gsc_pull.py [--out FILE]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from common import env, load_config, save_json, step_result

SOURCE = "Google Search Console API (searchanalytics.query)"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
# GSC data lags ~2-3 days; end the window 3 days back so both windows are complete.
LAG_DAYS = 3


def load_credentials():
    raw = env("GSC_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    from google.oauth2 import service_account

    info = json.loads(raw) if raw.lstrip().startswith("{") else json.loads(Path(raw).read_text())
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def query(service, site_url: str, days: int, dimensions: list[str], row_limit: int = 1000) -> list[dict]:
    end = dt.date.today() - dt.timedelta(days=LAG_DAYS)
    start = end - dt.timedelta(days=days - 1)
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": dimensions, "rowLimit": row_limit}
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = []
    for r in resp.get("rows", []):
        row = dict(zip(dimensions, r["keys"]))
        row.update(clicks=r["clicks"], impressions=r["impressions"],
                   ctr=round(r["ctr"], 4), position=round(r["position"], 1))
        rows.append(row)
    return rows


def near_misses(rows: list[dict], lo: float = 11, hi: float = 20) -> list[dict]:
    hits = [r for r in rows if lo <= r["position"] <= hi]
    return sorted(hits, key=lambda r: -r["impressions"])


def totals(rows: list[dict]) -> dict:
    clicks = sum(r["clicks"] for r in rows)
    impressions = sum(r["impressions"] for r in rows)
    return {"clicks": clicks, "impressions": impressions,
            "ctr": round(clicks / impressions, 4) if impressions else None}


def run() -> dict:
    site = load_config("site")
    site_url = env("GSC_SITE_URL") or site["gsc_site_url"]
    try:
        creds = load_credentials()
    except Exception as e:
        return step_result("error", SOURCE, note=f"Could not load GSC service account: {e}")
    if creds is None:
        return step_result("needs_human", SOURCE,
                           note="GSC_SERVICE_ACCOUNT_JSON not set. One-time setup: Google Cloud project -> "
                                "enable Search Console API -> service account -> add it as a user on the "
                                "wayout.design GSC property.")
    from googleapiclient.discovery import build

    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    data = {"site_url": site_url, "windows": {}}
    try:
        for days in (7, 30):
            by_query_page = query(service, site_url, days, ["query", "page"])
            by_query = query(service, site_url, days, ["query"])
            data["windows"][f"{days}d"] = {
                "totals": totals(by_query),
                "queries": by_query,
                "query_page": by_query_page,
                "near_misses": near_misses(by_query),
            }
    except Exception as e:
        return step_result("error", SOURCE, data, note=f"GSC query failed: {str(e)[:300]}")
    return step_result("ok", SOURCE, data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    args = ap.parse_args()
    result = run()
    if args.out:
        save_json(Path(args.out), result)
    w = ((result.get("data") or {}).get("windows") or {}).get("30d", {})
    print(f"[{result['status']}] 30d totals={w.get('totals')} near_misses={len(w.get('near_misses', []))} "
          f"{result.get('note', '')}")
