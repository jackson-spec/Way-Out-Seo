"""PageSpeed Insights pull: SEO / accessibility / performance / best-practices scores.

Free and official. No key needed at low volume; set PAGESPEED_API_KEY to raise the
rate limit. Pages come from the latest crawl (or --urls); capped by
site.json pagespeed.max_pages so a run stays well inside the free quota.

Usage: python seo/scripts/pagespeed.py [--urls URL ...] [--out FILE]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import requests

from common import env, load_config, save_json, step_result

SOURCE = "Google PageSpeed Insights API v5"
API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]


def score_url(url: str, strategy: str, key: str | None) -> dict:
    params = [("url", url), ("strategy", strategy)] + [("category", c) for c in CATEGORIES]
    if key:
        params.append(("key", key))
    r = requests.get(API, params=params, timeout=90)
    r.raise_for_status()
    cats = r.json().get("lighthouseResult", {}).get("categories", {})
    return {c: round(cats[c]["score"] * 100) if cats.get(c, {}).get("score") is not None else None
            for c in CATEGORIES}


def run(urls: list[str] | None = None) -> dict:
    site = load_config("site")
    cfg = site["pagespeed"]
    urls = (urls or [site["base_url"] + "/"])[: cfg["max_pages"]]
    key = env("PAGESPEED_API_KEY")
    results, errors = {}, {}
    for url in urls:
        try:
            results[url] = score_url(url, cfg["strategy"], key)
        except Exception as e:
            errors[url] = str(e)[:200]
    if not results:
        return step_result("error", SOURCE, {"errors": errors}, note="No PageSpeed results")
    averages = {}
    for c in CATEGORIES:
        vals = [r[c] for r in results.values() if r.get(c) is not None]
        averages[c] = round(sum(vals) / len(vals), 1) if vals else None
    status = "partial" if errors else "ok"
    return step_result(status, f"{SOURCE} ({cfg['strategy']})",
                       {"pages": results, "averages": averages, "errors": errors})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--urls", nargs="*")
    ap.add_argument("--out")
    args = ap.parse_args()
    result = run(args.urls)
    if args.out:
        save_json(Path(args.out), result)
    print(f"[{result['status']}] averages={(result['data'] or {}).get('averages')}")
