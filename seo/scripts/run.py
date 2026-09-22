"""/seo-growth entrypoint. Runs every step in a fixed order and writes a snapshot + report.

  1. crawl.py + pagespeed.py  -> technical signals
  2. gsc_pull.py              -> keyword signals
  3. geo_check.py             -> AI-visibility signals
  4. snapshot.py              -> save + diff against the last run
  5. queues.py                -> advance one content item and one outreach item (draft only)
  6. report.py                -> write the dated report

A failing step is recorded and the run carries on, so one missing key never costs
the whole run. Nothing here sends outreach or deploys site changes: technical fixes
come out as a punch list, and queue items stop at drafted.

Usage: python seo/scripts/run.py [--skip crawl pagespeed gsc geo queues] [--geo-limit N] [--max-pages N]
"""
from __future__ import annotations

import argparse
import sys
import traceback
from urllib.parse import urlparse

import crawl
import geo_check
import gsc_pull
import pagespeed
import queues
import report
import snapshot
from common import step_result

STEPS = ["crawl", "pagespeed", "gsc", "geo", "queues"]


def safe(name: str, fn, *args, **kwargs) -> dict:
    print(f"-> {name} ...", flush=True)
    try:
        result = fn(*args, **kwargs)
    except Exception as e:
        traceback.print_exc()
        result = step_result("error", name, note=f"{type(e).__name__}: {str(e)[:300]}")
    print(f"   {name}: {result['status']} {result.get('note') or ''}", flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the /seo-growth cycle")
    ap.add_argument("--skip", nargs="*", default=[], choices=STEPS)
    ap.add_argument("--geo-limit", type=int, help="only run the first N GEO prompts")
    ap.add_argument("--max-pages", type=int, help="override crawl max pages")
    args = ap.parse_args(argv)
    skip = set(args.skip)

    def skipped(name):
        return step_result("skipped", name, note="skipped via --skip")

    steps: dict = {}
    steps["crawl"] = skipped("crawl") if "crawl" in skip else safe("crawl", crawl.crawl, args.max_pages)

    ps_urls = None
    if steps["crawl"]["status"] in ("ok", "partial"):
        ps_urls = template_urls(steps["crawl"]["data"]["page_urls"])
    steps["pagespeed"] = skipped("pagespeed") if "pagespeed" in skip else safe("pagespeed", pagespeed.run, ps_urls)
    steps["gsc"] = skipped("gsc") if "gsc" in skip else safe("gsc", gsc_pull.run)
    steps["geo"] = skipped("geo") if "geo" in skip else safe("geo", geo_check.run, args.geo_limit)

    snap = snapshot.build(steps)
    _, prev = snapshot.previous(snap["date"])
    d = snapshot.diff(snap, prev)

    activity = {}
    if "queues" not in skip:
        for kind in ("content", "outreach"):
            try:
                activity[kind] = queues.advance(kind)
            except Exception as e:
                traceback.print_exc()
                activity[kind] = None
                print(f"   queue {kind}: error {e}")
    snap["queue_activity"] = activity
    snap["diff"] = d
    snap_path = snapshot.save(snap)
    report_path = report.write(snap, d, activity)
    print(f"\nSnapshot: {snap_path}\nReport:   {report_path}")
    return 0 if any(s["status"] in ("ok", "partial") for s in steps.values()) else 1


def template_urls(urls: list[str]) -> list[str]:
    """One page per top-level path section, a cheap stand-in for "one per template"."""
    seen, out = set(), []
    for url in urls:
        section = urlparse(url).path.strip("/").split("/")[0]
        if section not in seen:
            seen.add(section)
            out.append(url)
    return out


if __name__ == "__main__":
    sys.exit(main())
