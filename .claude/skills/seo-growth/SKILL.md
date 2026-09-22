---
name: seo-growth
description: Run the recurring wayout.design SEO & AI-visibility cycle (crawl, PageSpeed, Search Console, GEO prompt check, snapshot diff, one content draft, one outreach draft, dated report). Use when Jackson invokes /seo-growth, asks to run or check the SEO cycle, or asks what changed in wayout.design's SEO / AI visibility since the last run.
---

# /seo-growth

Everything lives under `seo/` in this repo. The plan and build guide are in Jackson's doc
"Wayout.design SEO & AI Visibility Growth Plan".

## 1. Run the cycle

```bash
pip install -r seo/requirements.txt   # first time in a fresh environment
python seo/scripts/run.py             # --skip geo, --geo-limit 3, --max-pages 50 for quick runs
```

It writes `seo/snapshots/YYYY-MM-DD.json` and `seo/reports/YYYY-MM-DD.md`, and moves one
content item and one outreach item from `pending` to `drafted` (or to `brief` when no
drafting API key is set). The same script runs Mon/Wed/Fri in `.github/workflows/seo-growth.yml`.

## 2. Finish any drafts left at "brief"

For each item the report lists as "brief written, draft still needed", open the file under
`seo/drafts/`, write the draft from the brief in the file (use the `wayout-voice` skill for
Way Out copy), replace the brief with the draft, then:

```bash
python seo/scripts/queues.py mark <content|outreach> <id> drafted
```

## 3. Technical fix pass

Runs 1-2 until the known backlog clears, then only on regressions. The report's
**Technical fix punch list** is the work list.
- If the wayout.design source repo is in this session, make the fixes on a branch and
  open a PR for Jackson. Never push straight to the live site.
- Otherwise (no-code builder, or not source-controlled) the punch list is the deliverable.

## 4. Summarize for Jackson

Read the report as if seeing it cold, then give Jackson: what changed, what's broken, what
was drafted and needs his review (with file paths), and any setup items marked NEEDS SETUP.

## Guardrails (hard rules)

- Never send outreach, submit forms, or email anyone. Outreach stops at `drafted`. Only
  Jackson marks `approved` / `sent` / `published`, via `queues.py mark ... --confirm "..."`.
- Never auto-publish or deploy content to the live site. `review_mode` in
  `seo/config/site.json` stays `true` until Jackson has approved several runs' drafts.
- Never fabricate or round up metrics. Report `needs_human` / `error` steps as not checked.
  Trend over several runs instead of reacting to one.
- Never buy links, use link farms, or submit to low-quality directories.
- Never open a second account on a provider already in `providers.json` for more free
  quota. Add a genuinely different provider there instead.
- Keep the GEO prompt list around 10 and run it once per cycle.
- Flag anything that needs a human login (Ahrefs / Bing Webmaster Tools, GSC setup)
  instead of claiming it was checked.
