"""Content and outreach queue helpers.

Each queue is a list of {id, item, status, notes, ...} in data/*.json.
Status flow: pending -> brief | drafted -> approved -> sent/published.
("brief" = no drafting provider was available, so only the brief was written;
the /seo-growth skill session writes the draft and marks it drafted.)

Named queues.py, not queue.py, so it doesn't shadow the stdlib queue module
that requests/urllib3 import.

Guardrails baked in here:
- The automated run can only move an item from pending to brief/drafted.
- approved / sent / published can only be set by a person through the CLI
  with --confirm, never by run.py.
- Nothing in this module sends email, submits forms, or deploys content.

Usage:
  python seo/scripts/queues.py list content|outreach [--status pending]
  python seo/scripts/queues.py mark outreach <id> sent --confirm "Jackson sent it 2026-09-24"
  python seo/scripts/queues.py add outreach "example.com - resource page" --notes "found via manual search"
"""
from __future__ import annotations

import argparse
import re

import llm
from common import DATA_DIR, DRAFT_DIR, load_json, now_iso, save_json, today

QUEUES = {"content": DATA_DIR / "content_queue.json", "outreach": DATA_DIR / "outreach_queue.json"}
AUTOMATED_STATUSES = {"pending", "brief", "drafted"}
HUMAN_ONLY_STATUSES = {"approved", "sent", "published", "skipped"}
ALL_STATUSES = AUTOMATED_STATUSES | HUMAN_ONLY_STATUSES


class GuardrailError(Exception):
    pass


def load(kind: str) -> list[dict]:
    return load_json(QUEUES[kind], [])


def save(kind: str, items: list[dict]) -> None:
    save_json(QUEUES[kind], items)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def next_pending(items: list[dict]) -> dict | None:
    pending = [i for i in items if i["status"] == "pending"]
    return min(pending, key=lambda i: i.get("priority", 99)) if pending else None


def set_status(kind: str, item_id: str, status: str, *, by_human: bool = False, note: str | None = None) -> dict:
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if status in HUMAN_ONLY_STATUSES and not by_human:
        raise GuardrailError(f"{status!r} can only be set by a person (queues.py mark ... --confirm)")
    items = load(kind)
    for it in items:
        if it["id"] == item_id:
            it["status"] = status
            it.setdefault("history", []).append({"status": status, "ts": now_iso(),
                                                 "by": "human" if by_human else "seo-growth", "note": note})
            save(kind, items)
            return it
    raise KeyError(item_id)


def add(kind: str, item: str, notes: str = "", **extra) -> dict:
    items = load(kind)
    new = {"id": slug(item), "item": item, "status": "pending", "notes": notes,
           "added": today(), **extra}
    if any(i["id"] == new["id"] for i in items):
        return next(i for i in items if i["id"] == new["id"])
    items.append(new)
    save(kind, items)
    return new


CONTENT_BRIEF = """You are drafting a web page for Way Out Custom Branding (wayout.design), a solo design studio in
Wilmington, NC that builds websites and brands for trades and home-service businesses in coastal NC.

Page to draft: {item}
Notes: {notes}

Requirements:
- Write in plain, confident, direct language. Outcomes (calls, booked jobs, trust) over aesthetics.
- Use the full name "Way Out Custom Branding" at least once (not just "Way Out").
- Open with a one-sentence direct answer to the page's core question so AI engines can quote it.
- Include an H1, 3-5 H2 sections, and a short FAQ (3 questions, answer first sentence).
- Do not invent statistics, client results, prices, or testimonials. Mark anything that needs a
  real number or quote as [JACKSON: ...].
- Output Markdown only."""

OUTREACH_BRIEF = """Draft a short, polite outreach email from Jackson at Way Out Custom Branding (wayout.design),
a solo design studio in Wilmington, NC that builds websites for trades and home-service businesses.

Target: {item}
Angle / notes: {notes}

Requirements:
- Under 150 words, subject line first ("Subject: ...").
- Specific to the target; no generic flattery; one clear, low-effort ask.
- Never offer to pay for a link. Offer something genuinely useful (in-kind design help,
  a resource, a sponsorship, a case study) where it fits the angle.
- Mark anything you'd need to confirm (names, contact person, details) as [JACKSON: ...].
- This is a DRAFT for Jackson to review and send himself."""


def draft(kind: str, it: dict) -> tuple[str, str]:
    """Return (markdown, how). Uses a 'draft'-role provider if a key is set, else a brief for Claude Code."""
    brief = (CONTENT_BRIEF if kind == "content" else OUTREACH_BRIEF).format(item=it["item"], notes=it.get("notes", ""))
    drafters = llm.providers("draft")
    if drafters:
        try:
            text, route = llm.call_with_fallback(drafters[0], brief, max_tokens=2000)
            if text.strip():
                return text, f"{drafters[0]['name']} ({route})"
        except Exception as e:
            brief += f"\n\n<!-- automatic draft failed: {type(e).__name__} -->"
    return ("<!-- NEEDS DRAFT: no drafting provider available. When run via the /seo-growth skill, "
            "Claude Code writes the draft here from the brief below. -->\n\n## Brief\n\n" + brief), "brief-only"


def advance(kind: str) -> dict | None:
    """Pick the next pending item, write its draft file, mark it drafted. Never sends or publishes."""
    items = load(kind)
    it = next_pending(items)
    if not it:
        return None
    body, how = draft(kind, it)
    path = DRAFT_DIR / kind / f"{today()}-{it['id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"<!-- queue: {kind} | id: {it['id']} | drafted: {now_iso()} | by: {how} -->\n"
              f"<!-- REVIEW REQUIRED: nothing here has been sent or published. -->\n\n")
    path.write_text(header + body.strip() + "\n")
    rel = str(path.relative_to(DRAFT_DIR.parent))
    for i in items:
        if i["id"] == it["id"]:
            i["status"] = "brief" if how == "brief-only" else "drafted"
            i["draft"] = rel
            i.setdefault("history", []).append({"status": i["status"], "ts": now_iso(), "by": "seo-growth", "note": how})
            it = i
    save(kind, items)
    return it


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Content/outreach queue helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("kind", choices=QUEUES); ls.add_argument("--status")
    mk = sub.add_parser("mark"); mk.add_argument("kind", choices=QUEUES); mk.add_argument("id")
    mk.add_argument("status", choices=sorted(ALL_STATUSES))
    mk.add_argument("--confirm", metavar="NOTE", help="required for approved/sent/published/skipped")
    ad = sub.add_parser("add"); ad.add_argument("kind", choices=QUEUES); ad.add_argument("item")
    ad.add_argument("--notes", default="")
    args = ap.parse_args()

    if args.cmd == "list":
        for i in load(args.kind):
            if not args.status or i["status"] == args.status:
                print(f"{i['status']:<10} {i['id']:<55} {i['item']}")
    elif args.cmd == "mark":
        human = args.status in HUMAN_ONLY_STATUSES
        if human and not args.confirm:
            raise SystemExit(f"Setting {args.status!r} needs --confirm \"<who/when>\"")
        print(set_status(args.kind, args.id, args.status, by_human=human, note=args.confirm))
    elif args.cmd == "add":
        print(add(args.kind, args.item, args.notes))
