"""GEO / AI-visibility check.

Runs every prompt in config/prompts.json against every configured provider
(aggregate across providers, not rotate-on-failure) and logs
{provider, prompt, mentioned, response_snippet} per call.

"mentioned" = wayout.design or "Way Out Custom Branding" appears. A bare "Way Out"
counts only as loose_mention, and never when the only hit is a collision brand
(wayoutdesign.com, wayoutinc.com), which is tracked separately.
Exa results are a supplementary signal and are kept out of the headline mention rate.

Usage: python seo/scripts/geo_check.py [--limit N] [--out FILE]
       python seo/scripts/geo_check.py --test-text "some answer text"   # sanity-check matching
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import llm
from common import load_config, save_json, step_result

SOURCE = "DIY prompt log across configured LLM APIs"
SNIPPET_CHARS = 400


def detect(text: str, site: dict | None = None) -> dict:
    site = site or load_config("site")
    lower = text.lower()
    collisions = [d for d in site["collision_domains"] if d in lower]
    # Strip collision brands before matching, so "wayoutdesign.com" never counts as us.
    cleaned = lower
    for d in site["collision_domains"]:
        cleaned = cleaned.replace(d, " ")
    mentioned = any(re.search(p, cleaned) for p in site["brand_patterns"])
    loose = mentioned or bool(re.search(site["brand_loose_pattern"], cleaned))
    competitors = [name for name, pat in site["competitor_patterns"].items()
                   if name in lower or re.search(pat, lower)]
    return {"mentioned": mentioned, "loose_mention": loose,
            "collision_brands": collisions, "competitors": competitors}


def snippet(text: str, site: dict) -> str:
    lower = text.lower()
    for p in site["brand_patterns"] + [site["brand_loose_pattern"]]:
        m = re.search(p, lower)
        if m:
            start = max(0, m.start() - SNIPPET_CHARS // 2)
            return text[start:start + SNIPPET_CHARS].strip()
    return text[:SNIPPET_CHARS].strip()


def rate(rows: list[dict], key: str = "mentioned") -> float | None:
    ok = [r for r in rows if "error" not in r]
    return round(sum(r[key] for r in ok) / len(ok), 3) if ok else None


def run(limit: int | None = None) -> dict:
    site = load_config("site")
    prompts = load_config("prompts")[:limit] if limit else load_config("prompts")
    llm_providers = llm.providers("geo")
    exa_providers = llm.providers("geo_supplementary")
    missing = llm.missing_providers()
    if not llm_providers and not exa_providers:
        return step_result("needs_human", SOURCE, {"missing_keys": missing},
                           note="No GEO provider keys set (GEMINI_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY).")

    rows, supplementary = [], []
    for prompt in prompts:
        for p in llm_providers + exa_providers:
            row = {"provider": p["name"], "model": p.get("model"), "prompt": prompt}
            try:
                text, route = llm.call_with_fallback(p, prompt)
                row.update(detect(text, site), route=route, response_snippet=snippet(text, site))
            except Exception as e:
                row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            (supplementary if p in exa_providers else rows).append(row)

    by_provider = {}
    for name in sorted({r["provider"] for r in rows}):
        pr = [r for r in rows if r["provider"] == name]
        by_provider[name] = {"mention_rate": rate(pr), "calls": len(pr),
                             "errors": sum(1 for r in pr if "error" in r)}
    ok_rows = [r for r in rows if "error" not in r]
    competitor_rate = {}
    for name in site["competitor_patterns"]:
        competitor_rate[name] = (round(sum(name in r["competitors"] for r in ok_rows) / len(ok_rows), 3)
                                 if ok_rows else None)
    data = {
        "mention_rate": rate(rows),
        "loose_mention_rate": rate(rows, "loose_mention"),
        "competitor_mention_rate": competitor_rate,
        "collision_hits": sum(1 for r in ok_rows if r["collision_brands"]),
        "by_provider": by_provider,
        "exa_mention_rate": rate(supplementary),
        "prompts": len(prompts),
        "missing_keys": missing,
        "results": rows,
        "supplementary_results": supplementary,
    }
    errors = sum(1 for r in rows + supplementary if "error" in r)
    status = "ok" if not errors else ("partial" if ok_rows else "error")
    return step_result(status, SOURCE + f" ({', '.join(p['name'] for p in llm_providers + exa_providers)})", data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="only run the first N prompts")
    ap.add_argument("--out")
    ap.add_argument("--test-text", help="print match result for a piece of text and exit")
    args = ap.parse_args()
    if args.test_text is not None:
        print(detect(args.test_text))
        raise SystemExit
    result = run(args.limit)
    if args.out:
        save_json(Path(args.out), result)
    d = result.get("data") or {}
    print(f"[{result['status']}] mention_rate={d.get('mention_rate')} by_provider={d.get('by_provider')} "
          f"{result.get('note', '')}")
