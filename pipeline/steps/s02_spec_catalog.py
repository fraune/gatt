"""Step 2: catalog every specification page on bluetooth.com (title, version, status, document links).

Output: build/spec_catalog.json
"""

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

INDEX_URL = "https://www.bluetooth.com/specifications/specs/"
HTML_BASE = "https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/"
SLUG_RE = re.compile(r'https://www\.bluetooth\.com/specifications/specs/([^/"?#]+)/')
NOT_SPECS = {"html", "feed"}


def _href(el):
    """The element's href as a string, or None (bs4 types attributes as str | list)."""
    href = el.get("href") if isinstance(el, Tag) else None
    return href if isinstance(href, str) else None


def _html_doc_url(href):
    """Viewer links look like .../specs/html/?src=HRS_v1.0/out/en/index-en.html; others link the doc directly."""
    parsed = urlparse(href)
    if parsed.path.rstrip("/").endswith("/specs/html"):
        src = parse_qs(parsed.query).get("src", [None])[0]
        return HTML_BASE + src if src else None
    if "/Specification/HTML/" in href:
        return href
    return None


def _parse_page(slug, html):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    lines = [l for l in main.get_text("\n", strip=True).split("\n") if l.strip()]
    title = version = status = None
    if "Specifications" in lines:
        i = lines.index("Specifications")
        head = (
            re.split(r"\t+|\s{2,}", lines[i + 1].strip()) if i + 1 < len(lines) else []
        )
        title = head[0].replace("\u200b", "").strip() if head else None
        version = head[-1].strip() if len(head) > 1 else None
        status = lines[i + 2].strip() if i + 2 < len(lines) else None
    html_url = pdf_url = None
    # Identify the HTML edition by its URL; most link texts end in "(HTML)" but not all
    # (e.g. Next DST Change Service), so the text is only used to prefer one link over another.
    html_links = [
        (a, _html_doc_url(urljoin(INDEX_URL, href)))
        for a in main.find_all("a", href=True)
        if (href := _href(a))
    ]
    html_links = [(a, u) for a, u in html_links if u]
    html_links.sort(
        key=lambda au: not au[0].get_text(" ", strip=True).endswith("(HTML)")
    )
    if html_links:
        a, html_url = html_links[0]
        row = a.find_parent("tr")
        if row:
            pdf = row.find("a", string=re.compile(r"^\s*PDF\s*$"))
            pdf_url = _href(pdf)
    if pdf_url is None:
        docman = main.find(
            "a", href=re.compile(r"docman/handlers/downloaddoc", re.IGNORECASE)
        )
        pdf_url = _href(docman)
    return {
        "slug": slug,
        "page_url": f"{INDEX_URL}{slug}/",
        "title": title,
        "version": version,
        "status": status,
        "html_url": html_url,
        "pdf_url": pdf_url,
    }


def run(ctx):
    index = ctx.fetch(
        INDEX_URL, ctx.cache_dir / "spec_pages" / "_index.html", min_bytes=10_000
    )
    slugs = sorted(set(SLUG_RE.findall(index.read_text(errors="ignore"))) - NOT_SPECS)
    pairs = [
        (f"{INDEX_URL}{s}/", ctx.cache_dir / "spec_pages" / f"{s}.html") for s in slugs
    ]
    fetched = ctx.fetch_many(pairs, min_bytes=10_000)

    catalog, failures = [], []
    for slug, (url, _) in zip(slugs, pairs):
        result = fetched[url]
        if isinstance(result, Exception):
            failures.append(f"{slug}: {result}")
            continue
        catalog.append(_parse_page(slug, result.read_text(errors="ignore")))
    for f in failures:
        ctx.log(f"WARN {f}")
    ctx.write_json(
        "spec_catalog.json",
        {"index_url": INDEX_URL, "specs": catalog, "failures": failures},
    )
    with_html = sum(1 for c in catalog if c["html_url"])
    return f"{len(catalog)} spec pages ({with_html} with an HTML edition), {len(failures)} failed"
