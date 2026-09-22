"""Save a dated snapshot combining crawl, PageSpeed, GSC and GEO, and diff it against the last one.

The diff is what makes each run additive instead of a fresh audit every time.

Usage: python seo/scripts/snapshot.py --diff A.json B.json   # diff two saved snapshots
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import SNAPSHOT_DIR, load_json, now_iso, save_json, today

RANK_MOVE_THRESHOLD = 3  # positions


def build(steps: dict) -> dict:
    return {"date": today(), "generated_at": now_iso(), "steps": steps}


def previous(before: str | None = None) -> tuple[Path | None, dict | None]:
    """Latest snapshot dated strictly before `before` (default: today)."""
    before = before or today()
    files = sorted(p for p in SNAPSHOT_DIR.glob("*.json") if p.stem < before)
    if not files:
        return None, None
    return files[-1], load_json(files[-1])


def save(snap: dict) -> Path:
    path = SNAPSHOT_DIR / f"{snap['date']}.json"
    save_json(path, snap)
    return path


def _data(snap: dict | None, step: str) -> dict | None:
    s = (snap or {}).get("steps", {}).get(step) or {}
    return s.get("data") if s.get("status") in ("ok", "partial") else None


def _delta(new, old):
    if new is None or old is None:
        return None
    return round(new - old, 3)


def diff(new: dict, old: dict | None) -> dict:
    if not old:
        return {"first_run": True}
    out: dict = {"first_run": False, "previous_date": old.get("date")}

    nc, oc = _data(new, "crawl"), _data(old, "crawl")
    if nc and oc:
        new_links = {(b["page"], b["link"]) for b in nc["broken_links"]}
        old_links = {(b["page"], b["link"]) for b in oc["broken_links"]}
        new_alt = {p["url"] for p in nc["pages_missing_alt"]}
        old_alt = {p["url"] for p in oc["pages_missing_alt"]}
        out["crawl"] = {
            "new_broken_links": [{"page": p, "link": l} for p, l in sorted(new_links - old_links)],
            "fixed_broken_links": [{"page": p, "link": l} for p, l in sorted(old_links - new_links)],
            "new_pages_missing_alt": sorted(new_alt - old_alt),
            "fixed_pages_missing_alt": sorted(old_alt - new_alt),
            "counts": {k: {"now": nc.get(k), "was": oc.get(k), "delta": _delta(nc.get(k), oc.get(k))}
                       for k in ("pages_crawled", "broken_link_count", "pages_missing_alt_count",
                                 "duplicate_meta_description_pages", "duplicate_content_pages")},
        }

    npg, opg = _data(new, "pagespeed"), _data(old, "pagespeed")
    if npg and opg:
        out["pagespeed"] = {c: {"now": v, "was": opg["averages"].get(c), "delta": _delta(v, opg["averages"].get(c))}
                            for c, v in npg["averages"].items()}

    ng, og = _data(new, "gsc"), _data(old, "gsc")
    if ng and og:
        nq = {r["query"]: r for r in ng["windows"]["30d"]["queries"]}
        oq = {r["query"]: r for r in og["windows"]["30d"]["queries"]}
        moves = []
        for q in nq.keys() & oq.keys():
            d = round(oq[q]["position"] - nq[q]["position"], 1)  # positive = moved up
            if abs(d) >= RANK_MOVE_THRESHOLD:
                moves.append({"query": q, "was": oq[q]["position"], "now": nq[q]["position"], "change": d})
        out["gsc"] = {
            "rank_moves": sorted(moves, key=lambda m: -abs(m["change"])),
            "new_queries": sorted(nq.keys() - oq.keys()),
            "lost_queries": sorted(oq.keys() - nq.keys()),
            "totals_30d": {"now": ng["windows"]["30d"]["totals"], "was": og["windows"]["30d"]["totals"]},
        }

    ngeo, ogeo = _data(new, "geo"), _data(old, "geo")
    if ngeo and ogeo:
        out["geo"] = {"mention_rate": {"now": ngeo["mention_rate"], "was": ogeo["mention_rate"],
                                       "delta": _delta(ngeo["mention_rate"], ogeo["mention_rate"])},
                      "competitor_mention_rate": {"now": ngeo["competitor_mention_rate"],
                                                  "was": ogeo["competitor_mention_rate"]}}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--diff", nargs=2, metavar=("NEW", "OLD"))
    args = ap.parse_args()
    if args.diff:
        print(json.dumps(diff(load_json(Path(args.diff[0])), load_json(Path(args.diff[1]))), indent=2))
    else:
        path, snap = previous("9999")
        print(f"latest snapshot: {path}")
