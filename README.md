# Way Out SEO

The recurring `/seo-growth` skill for wayout.design. It grows link authority, search rankings
and AI-answer visibility, using free or near-free tooling. It is built from the "Build Guide
for Claude Code" tab of the *Wayout.design SEO & AI Visibility Growth Plan* doc.

## What a run does

`python seo/scripts/run.py` does these steps, in order:

| # | Script | Output |
|---|---|---|
| 1 | `crawl.py` + `pagespeed.py` | Broken links, missing alt text, duplicate titles/meta, near-duplicate content, noindex pages, Lighthouse scores |
| 2 | `gsc_pull.py` | Search Console queries, pages, positions and CTR for 7 and 30 days; near-miss queries (positions 11-20) |
| 3 | `geo_check.py` | Every prompt in `config/prompts.json` against every configured LLM; mention rate, competitor rate, brand-collision hits |
| 4 | `snapshot.py` | `snapshots/YYYY-MM-DD.json` plus a diff against the previous run |
| 5 | `queues.py` | Drafts one content item and one outreach item into `drafts/` (never sends or publishes) |
| 6 | `report.py` | `reports/YYYY-MM-DD.md` |

If a step fails or is missing its credentials, the report shows it as `ERROR` or `NEEDS SETUP`
and the rest of the run continues. Every metric carries its source and a timestamp.

## Setup

```bash
pip install -r seo/requirements.txt
python -m pytest seo/tests          # offline tests, no keys needed
```

Environment variables (all optional; a step whose variable is missing is marked "needs setup"):

| Variable | Used by | Get it |
|---|---|---|
| `GSC_SERVICE_ACCOUNT_JSON` | gsc_pull | Google Cloud project -> enable Search Console API -> service account -> add it as a user on the GSC property. Set this to the key file's path or its raw JSON |
| `GSC_SITE_URL` | gsc_pull | Only if the property isn't `sc-domain:wayout.design` (e.g. `https://wayout.design/`) |
| `GEMINI_API_KEY` | geo_check | Free at aistudio.google.com |
| `OPENROUTER_API_KEY` | geo_check, fallback | openrouter.ai (`:free` models) |
| `ANTHROPIC_API_KEY` | geo_check, drafting | console.anthropic.com (paid, cents per run) |
| `EXA_API_KEY` | geo_check (supplementary) | exa.ai free tier |
| `PAGESPEED_API_KEY` | pagespeed | Optional; raises the rate limit |

For the scheduled GitHub Action (`.github/workflows/seo-growth.yml`, Mon/Wed/Fri), add these
as repository secrets. The Action commits the snapshot, report, drafts and queue state back to
the repo. It never touches the live site.

## Working the queues

```bash
python seo/scripts/queues.py list outreach --status drafted
python seo/scripts/queues.py add outreach "example.org - resource page" --notes "found via manual search"
python seo/scripts/queues.py mark outreach <id> sent --confirm "Jackson sent 2026-09-24"
```

The automated run can only move items `pending -> brief/drafted`. `approved`, `sent`,
`published` and `skipped` require `--confirm`, and `run.py` never sets them.

## Config

- `seo/config/site.json`: domain, sitemap, brand/collision/competitor patterns, crawl limits,
  the known Opinly backlog, KPI targets, and `review_mode`.
- `seo/config/providers.json`: the only place LLM providers and models are named. Check the
  model IDs are still current before the first run.
- `seo/config/prompts.json`: the fixed list of about 10 buyer-intent GEO prompts.
- `seo/config/targets.json`: backlink/citation targets from the plan.

## Deviations from the build guide

- The crawler uses `requests` + `BeautifulSoup` rather than `advertools`, which the plan lists
  as an equally valid option. This avoids a Scrapy dependency.
- `queue.py` is named `queues.py`, because a module called `queue` shadows Python's stdlib
  `queue` module, which `requests`/`urllib3` import.
- Items get a `brief` status when no drafting key is available, so an item is only marked
  `drafted` once a draft actually exists.
