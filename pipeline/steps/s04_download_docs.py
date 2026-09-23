"""Step 4: download each selected specification document (HTML edition).

Output: build/documents.json  ({doc_url: local cache path})
"""

import re


def _cache_name(url):
    """.../HTML/hids_v1-1_123/HIDS_v1.1/out/en/index-en.html -> hids_v1-1_123__HIDS_v1.1__index-en.html"""
    tail = url.split("/Specification/HTML/", 1)[-1]
    tail = tail.replace("/out/en/", "/")
    return re.sub(r"[^A-Za-z0-9._-]+", "__", tail)


def run(ctx):
    specs = ctx.read_json("service_specs.json")["services"]
    urls = sorted({s["doc_url"] for s in specs.values() if s})
    pairs = [(u, ctx.cache_dir / "spec_docs" / _cache_name(u)) for u in urls]
    fetched = ctx.fetch_many(pairs, min_bytes=5_000)

    documents, failures = {}, []
    for url, dest in pairs:
        result = fetched[url]
        if isinstance(result, Exception):
            failures.append(str(result))
        else:
            documents[url] = str(dest.relative_to(ctx.root))
    for f in failures:
        ctx.log(f"WARN {f}")
    ctx.write_json("documents.json", {"documents": documents, "failures": failures})
    return f"{len(documents)} documents available, {len(failures)} failed"
