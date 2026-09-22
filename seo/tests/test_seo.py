"""Offline tests: crawl against a local fixture site, GEO matching, snapshot diff, queue guardrails.

Run: python -m pytest seo/tests
"""
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import common  # noqa: E402
import crawl  # noqa: E402
import geo_check  # noqa: E402
import queues  # noqa: E402
import report  # noqa: E402
import snapshot  # noqa: E402

SITE = common.load_config("site")
LOREM = " ".join(f"word{i}" for i in range(120))
OTHER = " ".join(f"home{i * 7}" for i in range(120))

PAGES = {
    "/": f"""<html><head><title>Home</title><meta name="description" content="Shared desc"></head>
        <body><img src="/a.png" alt="logo"><img src="/b.png"><p>{OTHER}</p>
        <a href="/about">About</a><a href="/services">Services</a><a href="/missing">Dead</a>
        <a href="mailto:x@y.z">mail</a></body></html>""",
    "/about": f"""<html><head><title>About</title><meta name="description" content="Shared desc"></head>
        <body><img src="/c.png" alt=""><p>{LOREM} about</p><a href="/">Home</a></body></html>""",
    "/services": f"""<html><head><title>Services</title><meta name="description" content="Unique">
        <meta name="robots" content="noindex"></head>
        <body><p>{LOREM} about</p><a href="/">Home</a></body></html>""",
}


@pytest.fixture()
def fixture_site(monkeypatch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, head_only):
            port = self.server.server_address[1]
            if self.path == "/sitemap.xml":
                body = (f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        f'<url><loc>http://127.0.0.1:{port}/</loc></url></urlset>').encode()
                ctype = "application/xml"
            elif self.path in PAGES:
                body, ctype = PAGES[self.path].encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def do_GET(self):
            self._send(False)

        def do_HEAD(self):
            self._send(True)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    site = json.loads(json.dumps(SITE))
    site.update(domain="127.0.0.1", base_url=f"http://127.0.0.1:{port}",
                sitemap_url=f"http://127.0.0.1:{port}/sitemap.xml")
    site["crawl"]["duplicate_similarity"] = 0.9
    monkeypatch.setattr(crawl, "load_config", lambda name: site)
    yield port
    srv.shutdown()


def test_crawl_finds_known_issues(fixture_site):
    result = crawl.crawl()
    assert result["status"] == "ok"
    d = result["data"]
    assert d["pages_crawled"] == 3
    assert d["broken_link_count"] == 1 and d["broken_links"][0]["link"].endswith("/missing")
    assert d["broken_links"][0]["status"] == 404
    assert sorted(p["url"].rsplit("/", 1)[-1] for p in d["pages_missing_alt"]) == ["", "about"]
    assert d["duplicate_meta_description_pages"] == 2
    assert d["duplicate_content_pages"] == 2  # /about and /services share body text
    assert len(d["noindex_pages"]) == 1


@pytest.mark.parametrize("text,mentioned,loose,collisions", [
    ("Try Way Out Custom Branding (wayout.design) for contractors.", True, True, []),
    ("Check out wayout.design", True, True, []),
    ("Way Out is a small studio in Wilmington.", False, True, []),
    ("wayoutdesign.com is a design firm.", False, False, ["wayoutdesign.com"]),
    ("wayoutinc.com and Wilmington Design Co are options.", False, False, ["wayoutinc.com"]),
    ("Hire a local freelancer; there's no easy way out of bad websites.", False, True, []),
    ("Nothing relevant here.", False, False, []),
])
def test_geo_detect(text, mentioned, loose, collisions):
    r = geo_check.detect(text, SITE)
    assert r["mentioned"] is mentioned
    assert r["loose_mention"] is loose
    assert r["collision_brands"] == collisions


def test_geo_detect_competitor():
    assert geo_check.detect("Wilmington Design Co is good", SITE)["competitors"] == ["wilmingtondesignco.com"]


def _snap(date, broken, mention):
    crawl_data = {"broken_links": [{"page": "/", "link": l, "status": 404} for l in broken],
                  "pages_missing_alt": [], "pages_crawled": 3, "broken_link_count": len(broken),
                  "pages_missing_alt_count": 0, "duplicate_meta_description_pages": 0, "duplicate_content_pages": 0}
    return {"date": date, "generated_at": date, "steps": {
        "crawl": {"status": "ok", "source": "t", "ts": date, "data": crawl_data},
        "geo": {"status": "ok", "source": "t", "ts": date,
                "data": {"mention_rate": mention, "competitor_mention_rate": {}}},
        "gsc": {"status": "needs_human", "source": "t", "ts": date, "data": None}}}


def test_snapshot_diff():
    old, new = _snap("2026-09-19", ["/a", "/b"], 0.5), _snap("2026-09-22", ["/b", "/c"], 0.6)
    d = snapshot.diff(new, old)
    assert [b["link"] for b in d["crawl"]["new_broken_links"]] == ["/c"]
    assert [b["link"] for b in d["crawl"]["fixed_broken_links"]] == ["/a"]
    assert d["geo"]["mention_rate"]["delta"] == 0.1
    assert "gsc" not in d  # skipped steps are not compared
    assert snapshot.diff(new, None) == {"first_run": True}


def test_report_renders(tmp_path, monkeypatch):
    new = _snap("2026-09-22", ["/c"], 0.6)
    new["steps"]["crawl"]["data"].update(duplicate_meta_descriptions={}, duplicate_titles={},
                                         near_duplicate_pairs=[], noindex_pages=[], page_urls=["/"])
    new["steps"]["geo"]["status"] = "needs_human"
    md = report.render(new, {"first_run": True}, {"content": None, "outreach": None})
    assert "First run" in md and "/c returns 404" in md and "GEO provider key" in md


@pytest.fixture()
def temp_queues(tmp_path, monkeypatch):
    monkeypatch.setattr(queues, "QUEUES", {"content": tmp_path / "c.json", "outreach": tmp_path / "o.json"})
    monkeypatch.setattr(queues, "DRAFT_DIR", tmp_path / "drafts")
    monkeypatch.setattr(queues.llm, "providers", lambda role=None: [])
    common.save_json(tmp_path / "o.json", [
        {"id": "b", "item": "B", "status": "pending", "notes": "", "priority": 2},
        {"id": "a", "item": "A", "status": "pending", "notes": "", "priority": 1},
    ])
    return tmp_path


def test_queue_advance_and_guardrails(temp_queues):
    it = queues.advance("outreach")
    assert it["id"] == "a" and it["status"] == "brief"  # no drafter -> brief, not "drafted"
    assert (temp_queues / it["draft"]).exists()
    with pytest.raises(queues.GuardrailError):
        queues.set_status("outreach", "a", "sent")
    with pytest.raises(queues.GuardrailError):
        queues.set_status("outreach", "a", "published")
    assert queues.set_status("outreach", "a", "sent", by_human=True, note="test")["status"] == "sent"
    assert queues.advance("outreach")["id"] == "b"
    assert queues.advance("outreach") is None


def test_no_sending_code():
    """Guardrail: no script imports a mail/form-submission library."""
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    banned = ("smtplib", "sendgrid", "mailgun", "postmark", "resend", "gmail", "selenium")
    for f in scripts.glob("*.py"):
        text = f.read_text().lower()
        for b in banned:
            assert f"import {b}" not in text and f"from {b}" not in text, f"{f.name} imports {b}"


def test_geo_run_aggregates_and_falls_back(monkeypatch):
    import llm

    providers = [{"name": "gemini", "kind": "gemini", "key_env": "G", "model": "m", "roles": ["geo"],
                  "openrouter_fallback_model": "google/x"},
                 {"name": "anthropic", "kind": "anthropic", "key_env": "A", "model": "m", "roles": ["geo"]}]
    orp = {"name": "openrouter", "kind": "openai_compatible", "key_env": "O",
           "base_url": "https://openrouter.ai/api/v1", "model": "free", "roles": ["fallback"]}
    monkeypatch.setattr(llm, "providers", lambda role=None: providers if role == "geo" else [])
    monkeypatch.setattr(llm, "missing_providers", lambda: [])
    monkeypatch.setattr(llm, "openrouter", lambda: orp)
    monkeypatch.setattr(llm, "env", lambda k: "key")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def fake_call(p, prompt, max_tokens=800):
        if p["name"] == "gemini":
            raise llm.RateLimited("429")
        return "Way Out Custom Branding at wayout.design"

    monkeypatch.setattr(llm, "call", fake_call)
    monkeypatch.setattr(llm, "_openai_compatible", lambda *a, **k: "Try Wilmington Design Co.")
    r = geo_check.run(limit=2)
    d = r["data"]
    assert r["status"] == "ok" and len(d["results"]) == 4
    assert d["by_provider"]["anthropic"]["mention_rate"] == 1.0
    assert d["by_provider"]["gemini"]["mention_rate"] == 0.0
    assert all(x["route"] == "openrouter:google/x" for x in d["results"] if x["provider"] == "gemini")
    assert d["mention_rate"] == 0.5 and d["competitor_mention_rate"]["wilmingtondesignco.com"] == 0.5
