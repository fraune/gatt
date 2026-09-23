"""Step 3: choose the latest adopted specification document for each service.

Default rule: the spec page titled "<service name> Service" with the highest version.
Overrides (services.<uuid>.spec) can supply other titles, a sub-document path, a version pin,
and a label template.

Output: build/service_specs.json
"""

from ..context import norm_text, version_key


def _label(spec_override, entry):
    version = (entry["version"] or "").lstrip("vV")
    template = spec_override.get("label", "{title} v{version}")
    return template.format(title=entry["title"], version=version)


def run(ctx):
    assigned = ctx.read_json("assigned_numbers.json")
    catalog = ctx.read_json("spec_catalog.json")["specs"]
    overrides = ctx.load_overrides()["services"]

    by_title = {}
    for entry in catalog:
        if (
            entry["title"]
            and entry["html_url"]
            and norm_text(entry["status"] or "") == "adopted"
        ):
            by_title.setdefault(norm_text(entry["title"]), []).append(entry)

    selected, missing = {}, []
    for svc in assigned["services"]:
        uuid = svc["uuid"]
        spec_ov = overrides.get(uuid, {}).get("spec", {})
        titles = spec_ov.get("titles") or [f"{svc['name']} Service"]
        candidates = [e for t in titles for e in by_title.get(norm_text(t), [])]
        if spec_ov.get("version"):
            candidates = [
                e
                for e in candidates
                if version_key(e["version"]) == version_key(spec_ov["version"])
            ]
        if not candidates:
            missing.append(f"{uuid} {svc['name']}: no adopted spec titled {titles}")
            selected[uuid] = None
            continue
        best = max(candidates, key=lambda e: version_key(e["version"]))
        doc_url = best["html_url"]
        if spec_ov.get("doc_path"):
            doc_url = doc_url.rsplit("/", 1)[0] + "/" + spec_ov["doc_path"]
        selected[uuid] = {
            "title": best["title"],
            "version": best["version"],
            "label": _label(spec_ov, best),
            "slug": best["slug"],
            "page_url": best["page_url"],
            "doc_url": doc_url,
            "pdf_url": best["pdf_url"],
            "other_versions": sorted(
                {e["version"] for e in candidates} - {best["version"]}, key=version_key
            ),
        }
    for m in missing:
        ctx.log(f"WARN {m}")
    ctx.write_json("service_specs.json", {"services": selected, "missing": missing})
    return (
        f"{len(selected) - len(missing)} of {len(selected)} services matched to a spec"
    )
