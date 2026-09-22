"""Site crawl: broken links, missing alt text, duplicate title/meta, near-duplicate content.

Walks the sitemap plus every internal link it finds. Uses requests + BeautifulSoup
(the plan's lighter alternative to advertools; no Scrapy dependency). Indexability
is cross-checked against gsc_pull.py coverage data rather than re-implemented here.

Usage: python seo/scripts/crawl.py [--max-pages N] [--out FILE]
"""
from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from common import load_config, save_json, step_result

SOURCE = "self-hosted crawler (requests + BeautifulSoup)"
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "sms:", "#")


def normalize(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def is_internal(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


def parse_sitemap(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls)."""
    root = ET.fromstring(xml_text)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    pages = [e.text.strip() for e in root.findall(".//s:url/s:loc", ns) if e.text]
    children = [e.text.strip() for e in root.findall(".//s:sitemap/s:loc", ns) if e.text]
    return pages, children


def fetch_sitemap_urls(session: requests.Session, sitemap_url: str, timeout: int) -> list[str]:
    urls, queue, seen = [], [sitemap_url], set()
    while queue:
        sm = queue.pop()
        if sm in seen:
            continue
        seen.add(sm)
        try:
            r = session.get(sm, timeout=timeout)
            r.raise_for_status()
            pages, children = parse_sitemap(r.text)
        except Exception:
            continue
        urls.extend(pages)
        queue.extend(children)
    return urls


def body_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def analyze_page(url: str, html: str) -> dict:
    """Pull the on-page facts the audit needs from one HTML document."""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = (meta.get("content") or "").strip() if meta else ""
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = bool(robots and "noindex" in (robots.get("content") or "").lower())
    imgs_missing_alt = [
        img.get("src") or img.get("data-src") or "(no src)"
        for img in soup.find_all("img")
        if not (img.get("alt") or "").strip() and img.get("role") != "presentation"
    ]
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(SKIP_SCHEMES):
            continue
        links.append(urljoin(url, href))
    return {
        "url": url,
        "title": title,
        "meta_description": description,
        "noindex": noindex,
        "images_missing_alt": imgs_missing_alt,
        "links": links,
        "text": body_text(soup),
    }


def check_link(session: requests.Session, url: str, timeout: int, cache: dict) -> int | str:
    if url in cache:
        return cache[url]
    try:
        r = session.head(url, allow_redirects=True, timeout=timeout)
        if r.status_code in (405, 403, 501) or r.status_code >= 500:
            r = session.get(url, allow_redirects=True, timeout=timeout, stream=True)
            r.close()
        cache[url] = r.status_code
    except requests.RequestException as e:
        cache[url] = f"error: {type(e).__name__}"
    return cache[url]


def find_duplicates(pages: list[dict], field: str) -> dict[str, list[str]]:
    groups = defaultdict(list)
    for p in pages:
        val = p.get(field, "")
        if val:
            groups[val].append(p["url"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def find_near_duplicates(pages: list[dict], threshold: float) -> list[dict]:
    pairs = []
    texts = [(p["url"], p["text"][:5000]) for p in pages if len(p.get("text", "")) > 200]
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            sm = SequenceMatcher(None, texts[i][1], texts[j][1], autojunk=False)
            if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                continue
            ratio = sm.ratio()
            if ratio >= threshold:
                pairs.append({"a": texts[i][0], "b": texts[j][0], "similarity": round(ratio, 3)})
    return pairs


def summarize(pages: list[dict], link_status: dict, threshold: float) -> dict:
    broken = []
    for p in pages:
        for link in sorted(set(p["links"])):
            status = link_status.get(normalize(link))
            if status is not None and not (isinstance(status, int) and 200 <= status < 400):
                broken.append({"page": p["url"], "link": link, "status": status})
    dup_titles = find_duplicates(pages, "title")
    dup_meta = find_duplicates(pages, "meta_description")
    near_dups = find_near_duplicates(pages, threshold)
    near_dup_pages = sorted({u for pr in near_dups for u in (pr["a"], pr["b"])})
    return {
        "pages_crawled": len(pages),
        "page_urls": [p["url"] for p in pages],
        "broken_links": broken,
        "broken_link_count": len(broken),
        "pages_missing_alt": [
            {"url": p["url"], "images": p["images_missing_alt"]} for p in pages if p["images_missing_alt"]
        ],
        "pages_missing_alt_count": sum(1 for p in pages if p["images_missing_alt"]),
        "duplicate_titles": dup_titles,
        "duplicate_meta_descriptions": dup_meta,
        "duplicate_meta_description_pages": sum(len(v) for v in dup_meta.values()),
        "missing_meta_description": [p["url"] for p in pages if not p["meta_description"]],
        "near_duplicate_pairs": near_dups,
        "duplicate_content_pages": len(near_dup_pages),
        "noindex_pages": [p["url"] for p in pages if p["noindex"]],
    }


def crawl(max_pages: int | None = None) -> dict:
    site = load_config("site")
    cfg = site["crawl"]
    domain = site["domain"]
    timeout = cfg["timeout_seconds"]
    max_pages = max_pages or cfg["max_pages"]

    session = requests.Session()
    session.headers["User-Agent"] = cfg["user_agent"]

    seeds = fetch_sitemap_urls(session, site["sitemap_url"], timeout) or [site["base_url"] + "/"]
    queue = [normalize(u) for u in seeds]
    seen: set[str] = set()
    pages: list[dict] = []
    link_status: dict = {}
    fetch_errors = []

    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = session.get(url, timeout=timeout)
        except requests.RequestException as e:
            link_status[url] = f"error: {type(e).__name__}"
            fetch_errors.append({"url": url, "error": str(e)[:200]})
            continue
        link_status[url] = r.status_code
        if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", ""):
            continue
        page = analyze_page(r.url, r.text)
        page["url"] = url
        pages.append(page)
        for link in page["links"]:
            n = normalize(link)
            if is_internal(n, domain) and n not in seen:
                queue.append(n)

    all_links = {normalize(l) for p in pages for l in p["links"]}
    for link in all_links:
        check_link(session, link, timeout, link_status)

    if not pages:
        return step_result("error", SOURCE, {"fetch_errors": fetch_errors[:20]},
                           note=f"Could not fetch any pages from {site['base_url']}")
    data = summarize(pages, link_status, cfg["duplicate_similarity"])
    data["fetch_errors"] = fetch_errors[:20]
    status = "partial" if queue else "ok"
    note = f"Stopped at max_pages={max_pages}; {len(queue)} URLs not crawled" if queue else None
    return step_result(status, SOURCE, data, note=note)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--out")
    args = ap.parse_args()
    result = crawl(args.max_pages)
    if args.out:
        save_json(Path(args.out), result)
    d = result["data"] or {}
    print(f"[{result['status']}] pages={d.get('pages_crawled')} broken={d.get('broken_link_count')} "
          f"missing_alt_pages={d.get('pages_missing_alt_count')} dup_meta_pages={d.get('duplicate_meta_description_pages')} "
          f"dup_content_pages={d.get('duplicate_content_pages')} {result.get('note', '')}")
