"""Render reports/YYYY-MM-DD.md from a snapshot, its diff, and the queue activity.

Written to be read cold: what changed, what needs fixing, what was drafted and needs
review, and what couldn't be checked automatically. Every number carries its source.

Usage: python seo/scripts/report.py [--snapshot snapshots/YYYY-MM-DD.json]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import REPORT_DIR, load_config, load_json
import snapshot as snap_mod

MANUAL_CHECKS = [
    "Ahrefs Webmaster Tools: referring domains for wayout.design (no free API; log in monthly)",
    "Bing Webmaster Tools: backlinks report (no free API; log in monthly)",
    "Google Search Console Links report: eyeball new linking domains for spam patterns",
    "Competitor gap: web-search \"wilmingtondesignco\" -site:wilmingtondesignco.com for new linking domains",
]
STATUS_ICON = {"ok": "OK", "partial": "PARTIAL", "skipped": "SKIPPED", "error": "ERROR", "needs_human": "NEEDS SETUP"}


def pct(v):
    return "n/a" if v is None else f"{v * 100:.0f}%"


def signed(v):
    return "n/a" if v is None else (f"+{v}" if v > 0 else str(v))


def section_status(steps: dict) -> list[str]:
    out = ["## Run status", "", "| Step | Status | Source | When | Note |", "|---|---|---|---|---|"]
    for name, s in steps.items():
        out.append(f"| {name} | {STATUS_ICON.get(s['status'], s['status'])} | {s['source']} | {s['ts']} | "
                   f"{(s.get('note') or '').replace('|', '/')} |")
    return out + [""]


def section_changes(d: dict) -> list[str]:
    out = ["## What changed since last run", ""]
    if d.get("first_run"):
        return out + ["First run: no previous snapshot to compare against. This run is the new baseline.", ""]
    out.append(f"Compared with {d.get('previous_date')}.")
    out.append("")
    c = d.get("crawl")
    if c:
        for k, v in c["counts"].items():
            out.append(f"- {k.replace('_', ' ')}: {v['now']} (was {v['was']}, {signed(v['delta'])})")
        if c["new_broken_links"]:
            out.append(f"- New broken links: {len(c['new_broken_links'])}")
            out += [f"  - {b['link']} (on {b['page']})" for b in c["new_broken_links"][:15]]
        if c["fixed_broken_links"]:
            out.append(f"- Broken links fixed: {len(c['fixed_broken_links'])}")
        if c["new_pages_missing_alt"]:
            out.append(f"- Pages newly missing alt text: {', '.join(c['new_pages_missing_alt'][:10])}")
    pg = d.get("pagespeed")
    if pg:
        out.append("- PageSpeed averages: " + ", ".join(f"{k} {v['now']} ({signed(v['delta'])})" for k, v in pg.items()))
    g = d.get("gsc")
    if g:
        out.append(f"- GSC 30-day totals: {g['totals_30d']['now']} (was {g['totals_30d']['was']})")
        for m in g["rank_moves"][:10]:
            direction = "up" if m["change"] > 0 else "down"
            out.append(f"  - \"{m['query']}\" moved {direction} {abs(m['change'])} ({m['was']} -> {m['now']})")
        if g["new_queries"]:
            out.append(f"- New queries showing impressions: {len(g['new_queries'])} "
                       f"(e.g. {', '.join(g['new_queries'][:5])})")
    geo = d.get("geo")
    if geo:
        m = geo["mention_rate"]
        out.append(f"- AI mention rate: {pct(m['now'])} (was {pct(m['was'])})")
    if not any(d.get(k) for k in ("crawl", "pagespeed", "gsc", "geo")):
        out.append("- No step succeeded in both this run and the last, so nothing could be compared.")
    return out + [""]


def section_kpis(steps: dict, site: dict) -> list[str]:
    t = site["kpi_targets"]
    crawl = (steps.get("crawl") or {}).get("data") if steps.get("crawl", {}).get("status") in ("ok", "partial") else None
    geo = (steps.get("geo") or {}).get("data") if steps.get("geo", {}).get("status") in ("ok", "partial") else None
    out = ["## KPIs vs 90-day targets", "", "| KPI | Now | Baseline | Target | Source |", "|---|---|---|---|---|"]
    out.append(f"| Broken links | {crawl['broken_link_count'] if crawl else 'not measured'} | "
               f"{t['broken_links']['baseline']} | {t['broken_links']['target']} | crawler |")
    out.append(f"| Pages missing alt text | {crawl['pages_missing_alt_count'] if crawl else 'not measured'} | "
               f"{t['pages_missing_alt']['baseline']} | {t['pages_missing_alt']['target']} | crawler |")
    out.append(f"| AI mention rate | {pct(geo['mention_rate']) if geo else 'not measured'} | "
               f"{pct(t['ai_mention_rate']['baseline'])} (Opinly) | {pct(t['ai_mention_rate']['target'])} | "
               f"DIY prompt log ({geo['prompts'] if geo else 0} prompts) |")
    out.append(f"| Real referring domains | manual check | {t['real_referring_domains']['baseline']} | "
               f"{t['real_referring_domains']['target']} | Ahrefs/Bing Webmaster Tools (manual) |")
    out.append("")
    out.append("_DIY mention rate is not the same measurement as Opinly's score; read it as a trend over several "
               "runs, not against the Opinly baseline number._")
    return out + [""]


def section_punch_list(steps: dict, site: dict) -> list[str]:
    s = steps.get("crawl") or {}
    out = ["## Technical fix punch list", ""]
    if s.get("status") not in ("ok", "partial"):
        return out + ["Crawl didn't complete, so there's no punch list this run.", ""]
    c = s["data"]
    kb = site["known_backlog"]
    out.append(f"Crawler found {c['broken_link_count']} broken links, {c['pages_missing_alt_count']} pages missing alt "
               f"text, {c['duplicate_meta_description_pages']} pages with duplicate meta descriptions, "
               f"{c['duplicate_content_pages']} near-duplicate pages, {len(c['noindex_pages'])} noindex pages "
               f"across {c['pages_crawled']} pages.")
    out.append(f"Known backlog ({kb['source']}): {kb['broken_links']} broken links, {kb['pages_missing_alt']} of "
               f"{kb['pages_total']} pages missing alt text, {kb['duplicate_meta_description_pages']} duplicate meta, "
               f"{kb['duplicate_content_pages']} duplicate content, {kb['non_indexable_pages']} non-indexable.")
    out.append("")
    if c["broken_links"]:
        out.append("**Broken links**")
        out += [f"- [ ] {b['link']} returns {b['status']} (linked from {b['page']})" for b in c["broken_links"][:40]]
        out.append("")
    if c["pages_missing_alt"]:
        out.append("**Images missing alt text**")
        for p in c["pages_missing_alt"][:40]:
            out.append(f"- [ ] {p['url']}: {len(p['images'])} image(s), e.g. {p['images'][0]}")
        out.append("")
    if c["duplicate_meta_descriptions"]:
        out.append("**Duplicate meta descriptions**")
        for desc, urls in c["duplicate_meta_descriptions"].items():
            out.append(f"- [ ] \"{desc[:80]}\" used on: {', '.join(urls)}")
        out.append("")
    if c["duplicate_titles"]:
        out.append("**Duplicate titles**")
        for title, urls in c["duplicate_titles"].items():
            out.append(f"- [ ] \"{title[:80]}\" used on: {', '.join(urls)}")
        out.append("")
    if c["near_duplicate_pairs"]:
        out.append("**Near-duplicate content**")
        out += [f"- [ ] {p['a']} ~ {p['b']} ({p['similarity']:.0%} similar)" for p in c["near_duplicate_pairs"][:20]]
        out.append("")
    if c["noindex_pages"]:
        out.append("**Noindex pages (confirm intentional)**")
        out += [f"- [ ] {u}" for u in c["noindex_pages"]]
        out.append("")
    return out


def section_keywords(steps: dict) -> list[str]:
    s = steps.get("gsc") or {}
    if s.get("status") not in ("ok", "partial"):
        return []
    nm = s["data"]["windows"]["30d"]["near_misses"]
    out = ["## Near-miss keywords (positions 11-20, last 30 days)", ""]
    if not nm:
        return out + ["None this run.", ""]
    out += ["| Query | Position | Impressions | Clicks |", "|---|---|---|---|"]
    out += [f"| {r['query']} | {r['position']} | {r['impressions']} | {r['clicks']} |" for r in nm[:15]]
    return out + [""]


def section_geo(steps: dict) -> list[str]:
    s = steps.get("geo") or {}
    if s.get("status") not in ("ok", "partial"):
        return []
    g = s["data"]
    out = ["## AI visibility (GEO)", ""]
    out.append(f"- Mention rate: {pct(g['mention_rate'])} strict, {pct(g['loose_mention_rate'])} including bare \"Way Out\"")
    for name, rate in g["competitor_mention_rate"].items():
        out.append(f"- {name} mention rate: {pct(rate)}")
    out.append(f"- Answers that also named a collision brand (wayoutdesign.com / wayoutinc.com): {g['collision_hits']}")
    for name, p in g["by_provider"].items():
        out.append(f"- {name}: {pct(p['mention_rate'])} over {p['calls']} calls ({p['errors']} errors)")
    if g.get("exa_mention_rate") is not None:
        out.append(f"- Exa (supplementary, search index proxy): {pct(g['exa_mention_rate'])}")
    if g["missing_keys"]:
        out.append(f"- Not checked (no key): {', '.join(g['missing_keys'])}")
    misses = [r for r in g["results"] if "error" not in r and not r["mentioned"]]
    if misses:
        out.append("")
        out.append("Prompts where Way Out was not mentioned:")
        out += [f"- ({r['provider']}) {r['prompt']}" for r in misses[:10]]
    return out + [""]


def section_queues(queue_activity: dict) -> list[str]:
    out = ["## Drafted this run (needs your review)", ""]
    any_item = False
    for kind, it in queue_activity.items():
        if not it:
            out.append(f"- {kind}: queue empty")
            continue
        any_item = True
        state = "brief written, draft still needed" if it["status"] == "brief" else "drafted"
        out.append(f"- **{kind}**: {it['item']} ({state}) -> `seo/{it.get('draft')}`")
    if any_item:
        out.append("")
        out.append("Nothing has been sent or published. After you send or publish something, record it with "
                   "`python seo/scripts/queues.py mark <content|outreach> <id> <sent|published> --confirm \"...\"`.")
    return out + [""]


def render(snap: dict, d: dict, queue_activity: dict, new_backlog: list[str] | None = None) -> str:
    site = load_config("site")
    steps = snap["steps"]
    lines = [f"# SEO growth run: {snap['date']}", "",
             f"Generated {snap['generated_at']} for {site['domain']}. Review mode is "
             f"{'ON: fixes and outreach stop at drafts for your review.' if site.get('review_mode') else 'off.'}", ""]
    lines += section_status(steps)
    lines += section_changes(d)
    lines += section_kpis(steps, site)
    lines += section_queues(queue_activity)
    lines += section_punch_list(steps, site)
    lines += section_keywords(steps)
    lines += section_geo(steps)
    if new_backlog:
        lines += ["## New backlog items", ""] + [f"- {b}" for b in new_backlog] + [""]
    lines += ["## Manual checks (need a human login, not checked automatically)", ""]
    lines += [f"- [ ] {m}" for m in MANUAL_CHECKS]
    lines += [s for name, s in (("gsc", "- [ ] Finish GSC service-account setup (GSC_SERVICE_ACCOUNT_JSON)"),
                                ("geo", "- [ ] Add at least one GEO provider key (GEMINI_API_KEY is free)"))
              if steps.get(name, {}).get("status") == "needs_human"]
    return "\n".join(lines).rstrip() + "\n"


def write(snap: dict, d: dict, queue_activity: dict, new_backlog: list[str] | None = None) -> Path:
    path = REPORT_DIR / f"{snap['date']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(snap, d, queue_activity, new_backlog))
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", help="default: latest snapshot")
    args = ap.parse_args()
    if args.snapshot:
        path = Path(args.snapshot)
        snap = load_json(path)
    else:
        path, snap = snap_mod.previous("9999")
    if not snap:
        raise SystemExit("No snapshot found; run run.py first.")
    _, prev = snap_mod.previous(snap["date"])
    print(write(snap, snap_mod.diff(snap, prev), snap.get("queue_activity", {})))
