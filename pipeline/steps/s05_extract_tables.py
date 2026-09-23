"""Step 5: extract the characteristic requirements table from each service's specification.

For every service this finds the table whose first header cell starts with "Characteristic" and
which has a requirement-style column (Requirement / Characteristic Qualifier / Mandatory / Optional /
Status), unless overrides name the table by caption or give explicit requirement columns (used for
the per-role tables in the Core Specification). Condition footnotes (C.n) are read from the
definition list that follows the table and from footnote rows inside the table.

Output: build/extracted.json
"""

import re

from bs4 import BeautifulSoup

from ..context import norm_text

FIRST_HEADER_RE = re.compile(r"^characteristics?\b", re.IGNORECASE)
REQ_HEADER_RE = re.compile(
    r"^(requirement|req\b|characteristic qualifier|qualifier|mandatory\s*/\s*optional|status|m\s*/\s*o)",
    re.IGNORECASE,
)
SECTION_REF_RE = re.compile(r"\(\s*(see\s+)?section[^)]*\)", re.IGNORECASE)
FOOTNOTE_MARK_RE = re.compile(r"\[[a-z]\]")
COND_DEF_RE = re.compile(
    r"\bC\.?\s?(\d+)\s*:\s*(.+?)(?=\s\bC\.?\s?\d+\s*:|$)", re.DOTALL
)
COND_START_RE = re.compile(r"^C\.?\s?\d+\s*:")
CODE_RE = re.compile(r"\bC\.?\s?(\d+)\b")
DESCRIPTOR_ROW_RE = re.compile(
    r"\bdescriptors?\s*$|\bdescriptor\s*:|client characteristic (configuration|descriptor)",
    re.IGNORECASE,
)
FOOTNOTE_ROW_RE = re.compile(r"^(C\.?\s?\d+\s*:|\*|\[[a-z]\]|notes?\b)", re.IGNORECASE)


def _text(el):
    s = (
        el.get_text(" ", strip=True)
        .replace("\xad", "")
        .replace("\u200b", "")
        .replace("\xa0", " ")
    )
    return re.sub(r"\s+", " ", s).strip()


def _block_text(el):
    """Like _text, but keeps list items apart ("a; b; c") so multi-item conditions stay readable."""
    items = el.find_all("li")
    if not items:
        return _text(el)
    intro = [_text(p) for p in el.find_all("p") if not p.find_parent("li")]
    return " ".join(intro + ["; ".join(_text(li) for li in items)]).strip()


def _rows(table):
    return [
        [_text(c) for c in tr.find_all(["td", "th"])] for tr in table.find_all("tr")
    ]


def _caption(table):
    wrap = table.find_parent("div", class_="table-responsive")
    title = wrap.find("div", class_="table-title") if wrap else None
    if title is None:
        title = table.find("caption")
    if title is None:
        # Core Specification layout: the title follows the table.
        nxt = table.find_next(class_=re.compile("title"))
        title = nxt if nxt and re.match(r"^Table\s", _text(nxt)) else None
    return _text(title) if title else ""


def _trailing_conditions(table):
    """C.n definitions in the dl/p elements between this table and the next section or table."""
    anchor = table.find_parent("div", class_="table-responsive") or table
    conds = {}
    for sib in anchor.find_next_siblings(limit=20):
        classes = sib.get("class") or []
        if (
            sib.name == "section"
            or re.match(r"^h[1-6]$", sib.name or "")
            or "table-responsive" in classes
        ):
            break
        for dt in sib.find_all("dt") if sib.name != "dt" else [sib]:
            m = re.match(r"^C\.?\s?(\d+)\s*:?$", _text(dt))
            dd = dt.find_next_sibling("dd")
            if m and dd:
                conds.setdefault(m.group(1), _block_text(dd))
        text = _text(sib)
        if COND_START_RE.match(text):
            for m in COND_DEF_RE.finditer(text):
                conds.setdefault(m.group(1), m.group(2).strip())
    return conds


def _requirement_columns(header, wanted):
    if FIRST_HEADER_RE.match(header[0] if header else "") is None:
        return None
    if re.search(r"descriptor", header[0], re.IGNORECASE) and not re.search(
        r"/\s*descriptor", header[0], re.IGNORECASE
    ):
        return None
    if wanted:
        idx = []
        for w in wanted:
            hits = [
                i for i, h in enumerate(header) if norm_text(h).startswith(norm_text(w))
            ]
            if not hits:
                return None
            idx.append(hits[0])
        return idx
    idx = [i for i, h in enumerate(header) if i > 0 and REQ_HEADER_RE.match(h)]
    return idx[:1] or None


def _clean_name(raw):
    name = SECTION_REF_RE.sub("", raw)
    name = FOOTNOTE_MARK_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip(" *")


def _parse_req(value):
    v = value.strip()
    codes = CODE_RE.findall(v)
    if codes:
        return "C", codes
    if re.match(r"^(M\b|mandatory)", v, re.IGNORECASE):
        return "M", []
    if re.match(r"^(O\b|optional)", v, re.IGNORECASE):
        return "O", []
    if re.match(r"^(E\b|X\b|excluded)", v, re.IGNORECASE):
        return "E", []
    return "?", []


def _describe(codes, conds):
    parts = [conds.get(c) or f"(text for C.{c} not found)" for c in codes]
    if len(parts) == 1:
        return parts[0]
    return " ".join(f"C.{c}: {p}" for c, p in zip(codes, parts))


def _combine_roles(roles, conds):
    """Per-role requirement columns (Core GAP/GATT tables) -> one requirement plus a condition."""
    kinds = [k for _, (k, _) in roles]
    if all(k == "E" for k in kinds):
        return None, None
    if all(k == "M" for k in kinds):
        req = "M"
    elif any(k in ("M", "C") for k in kinds):
        req = "C"
    else:
        req = "O"
    if len({(k, tuple(c)) for _, (k, c) in roles}) == 1:
        k, codes = roles[0][1]
        return req, (_describe(codes, conds) if k == "C" else None)
    words = {"M": "Mandatory", "O": "Optional", "E": "Excluded", "?": "Unknown"}
    cond = "; ".join(
        f"{role}: {_describe(codes, conds) if k == 'C' else words[k]}"
        for role, (k, codes) in roles
    )
    return req, cond


def _extract(table, rows, req_idx, known_names):
    header = rows[0]
    conds = _trailing_conditions(table)
    parsed, skipped, seen = [], [], set()
    for r in rows[1:]:
        if not any(r):
            continue
        # Row with a leading spacer cell (e.g. Cookware Service descriptor row).
        if len(r) > len(header) and r[0] == "":
            r = r[1:]
        joined = " ".join(c for c in r if c)
        if (
            len({c for c in r if c}) == 1 and COND_DEF_RE.search(joined)
        ) or FOOTNOTE_ROW_RE.match(r[0]):
            for m in COND_DEF_RE.finditer(joined):
                conds.setdefault(m.group(1), m.group(2).strip())
            continue
        name = _clean_name(r[0])
        if not name:
            continue
        if DESCRIPTOR_ROW_RE.search(name) and norm_text(name) not in known_names:
            skipped.append({"name": name, "reason": "descriptor"})
            continue
        sub = re.match(r"^(.+?):\s+\S", name)
        if sub and norm_text(sub.group(1)) in seen:
            skipped.append({"name": name, "reason": f'sub-type of "{sub.group(1)}"'})
            continue
        seen.add(norm_text(name))
        values = [r[i] if i < len(r) else "" for i in req_idx]
        parsed.append(
            {
                "spec_name": name,
                "raw_requirement": values,
                "roles": [header[i] for i in req_idx],
            }
        )

    for row in parsed:
        roles = [
            (role, _parse_req(v))
            for role, v in zip(row["roles"], row["raw_requirement"])
        ]
        if len(roles) == 1:
            kind, codes = roles[0][1]
            row["requirement"] = kind
            row["codes"] = codes
            row["condition"] = _describe(codes, conds) if kind == "C" else None
        else:
            row["requirement"], row["condition"] = _combine_roles(roles, conds)
            row["codes"] = sorted({c for _, (_, cs) in roles for c in cs})
        if len(roles) == 1:
            del row["roles"]
    dropped = [r for r in parsed if r["requirement"] is None]
    skipped += [
        {"name": r["spec_name"], "reason": "excluded for all roles"} for r in dropped
    ]
    return [r for r in parsed if r["requirement"] is not None], skipped, conds


def _included_sections(soup):
    """Text of any 'Included services' section, for cross-checking the overrides."""
    found = []
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        if re.search(r"included services?\s*$", _text(h), re.IGNORECASE):
            sec = h.find_parent("section")
            if sec:
                paras = [
                    _text(p)
                    for p in sec.find_all("p")
                    if p.find_parent("section") is sec
                ]
                found.append(" ".join(paras)[:1500])
    return found


def _text_checks(doc_text, phrases):
    return {p: norm_text(p) in doc_text for p in phrases}


def run(ctx):
    specs = ctx.read_json("service_specs.json")["services"]
    documents = ctx.read_json("documents.json")["documents"]
    overrides = ctx.load_overrides()["services"]
    # A row named like a descriptor (e.g. "Physical Activity Session Descriptor") is kept when it is
    # an assigned characteristic name.
    known_names = {
        norm_text(c["name"])
        for c in ctx.read_json("assigned_numbers.json")["characteristics"]
    }
    soups, doc_texts, results = {}, {}, {}

    for uuid, spec in specs.items():
        ov = overrides.get(uuid, {})
        if not spec or spec["doc_url"] not in documents:
            results[uuid] = {
                "status": "error",
                "error": "no specification document available",
            }
            continue
        path = documents[spec["doc_url"]]
        if path not in soups:
            soups[path] = BeautifulSoup(
                (ctx.root / path).read_text(errors="ignore"), "html.parser"
            )
            doc_texts[path] = norm_text(soups[path].get_text(" "))
        soup = soups[path]

        phrases = [ov["expect_text"]] if ov.get("expect_text") else []
        phrases += [
            i["expect_text"] for i in ov.get("included", []) if i.get("expect_text")
        ]
        included = (
            [] if ov.get("ignore_included_sections") else _included_sections(soup)
        )
        result = {
            "doc": path,
            "included_sections": included,
            "text_checks": _text_checks(doc_texts[path], phrases),
        }

        if ov.get("extract") is False:
            result["status"] = "skipped"
            results[uuid] = result
            continue

        table_ov = ov.get("table", {})
        candidates = []
        for i, table in enumerate(soup.find_all("table")):
            rows = _rows(table)
            if not rows:
                continue
            req_idx = _requirement_columns(rows[0], table_ov.get("requirement_columns"))
            if req_idx is not None:
                candidates.append((i, table, rows, req_idx, _caption(table)))
        if table_ov.get("caption"):
            candidates = [
                c
                for c in candidates
                if re.search(table_ov["caption"], c[4], re.IGNORECASE)
            ]
        if len(candidates) > 1:
            preferred = [
                c
                for c in candidates
                if "characteristic" in c[4].lower() and "descriptor" not in c[4].lower()
            ]
            candidates = preferred or candidates
        if len(candidates) != 1:
            result.update(
                status="error",
                error=f"{len(candidates)} candidate tables; set services.{uuid}.table.caption",
                candidates=[c[4] for c in candidates],
            )
            results[uuid] = result
            continue

        index, table, rows, req_idx, caption = candidates[0]
        chars, skipped, conds = _extract(table, rows, req_idx, known_names)
        result.update(
            status="ok",
            table={"index": index, "caption": caption, "header": rows[0]},
            rows=chars,
            skipped_rows=skipped,
            conditions=conds,
        )
        if not chars:
            result.update(
                status="error", error="table matched but no characteristic rows parsed"
            )
        results[uuid] = result

    ctx.write_json("extracted.json", results)
    counts = {}
    for r in results.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for uuid, r in results.items():
        if r["status"] == "error":
            ctx.log(f"WARN {uuid}: {r['error']} {r.get('candidates', '')}")
    return ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
