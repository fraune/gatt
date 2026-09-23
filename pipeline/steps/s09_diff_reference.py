"""Step 9: compare output/gatt_services.json against a reference file.

Default reference: build/previous_gatt_services.json, the output of the previous run (snapshotted by
the validate step). Use --reference to compare against something else, e.g. a file exported from git.
Both files may be schema 1 (services[].characteristics) or schema 2.0 (registries +
serviceDefinitions); each is normalized to the same shape before comparing.

Differences in characteristic membership, requirement, included services, or registry contents are
"substantive"; condition wording, spec labels, and verification notes are informational.

Output: build/diff_report.md
"""

import json


def _normalize(data):
    """-> (services {uuid: {...}}, characteristic registry {uuid: name} or None)."""
    if "serviceDefinitions" in data:  # schema 2.0
        char_names = {c["uuid"]: c["name"] for c in data["characteristics"]}
        svc_names = {s["uuid"]: s["name"] for s in data["services"]}
        services = {}
        for d in data["serviceDefinitions"]:
            services[d["uuid"]] = {
                "name": svc_names.get(d["uuid"], "?"),
                "status": d["verificationStatus"],
                "specification": d["specification"],
                "refs": {
                    r["uuid"]: {
                        "name": char_names.get(r["uuid"], "?"),
                        "requirement": r["requirement"],
                        "condition": r.get("condition"),
                    }
                    for r in d["characteristicRefs"]
                },
                "included": {
                    i["uuid"]: i["requirement"] for i in d["includedServices"]
                },
            }
        return services, char_names
    services = {}  # schema 1
    for s in data["services"]:
        services[s["uuid"]] = {
            "name": s["name"],
            "status": s.get("verification"),
            "specification": s.get("specification"),
            "refs": {
                c["uuid"]: {
                    "name": c["name"],
                    "requirement": c["requirement"],
                    "condition": c.get("condition"),
                }
                for c in s["characteristics"]
            },
            "included": {
                i["uuid"]: i.get("requirement") for i in s.get("includedServices", [])
            },
        }
    return services, None


def _diff_service(ref, new):
    sub, info, reworded = [], [], []
    rc, nc = ref["refs"], new["refs"]
    for u in sorted(rc.keys() - nc.keys()):
        sub.append(f"missing {u} {rc[u]['name']} (reference: {rc[u]['requirement']})")
    for u in sorted(nc.keys() - rc.keys()):
        sub.append(f"extra {u} {nc[u]['name']} ({nc[u]['requirement']})")
    for u in sorted(rc.keys() & nc.keys()):
        a, b = rc[u], nc[u]
        if a["requirement"] != b["requirement"]:
            sub.append(
                f"{u} {b['name']}: requirement {a['requirement']} -> {b['requirement']}"
            )
        if a["condition"] and b["condition"] and a["condition"] != b["condition"]:
            reworded.append(b["name"])
    if reworded:
        info.append(
            f"condition wording differs for {len(reworded)} characteristic(s): {', '.join(reworded)}"
        )
    if ref["included"] != new["included"]:
        sub.append(f"included services {ref['included']} -> {new['included']}")
    if ref["specification"] != new["specification"]:
        info.append(
            f'specification "{ref["specification"]}" -> "{new["specification"]}"'
        )
    if ref["status"] != new["status"]:
        info.append(f"verification {ref['status']} -> {new['status']}")
    return sub, info


def _diff_registry(ref_chars, new_chars):
    lines = []
    for u in sorted(ref_chars.keys() - new_chars.keys()):
        lines.append(f"- removed characteristic {u} {ref_chars[u]}")
    for u in sorted(new_chars.keys() - ref_chars.keys()):
        lines.append(f"- added characteristic {u} {new_chars[u]}")
    for u in sorted(ref_chars.keys() & new_chars.keys()):
        if ref_chars[u] != new_chars[u]:
            lines.append(
                f'- renamed characteristic {u}: "{ref_chars[u]}" -> "{new_chars[u]}"'
            )
    return lines


def run(ctx):
    reference = ctx.reference_path
    if not reference.exists():
        return f"no reference at {reference} (first run?); skipped"
    ref_data, new_data = (
        json.loads(reference.read_text()),
        json.loads(ctx.output_path.read_text()),
    )
    ref, ref_chars = _normalize(ref_data)
    new, new_chars = _normalize(new_data)
    # The generated output is always schema 2.0; {} only guards the type.
    new_chars = new_chars or {}
    shown = (
        reference.relative_to(ctx.root)
        if reference.is_relative_to(ctx.root)
        else reference
    )
    schemas = (
        ref_data.get("metadata", {}).get("schemaVersion", "1"),
        new_data["metadata"].get("schemaVersion", "1"),
    )

    lines = [
        f"# Diff: {ctx.output_path.relative_to(ctx.root)} vs {shown}",
        "",
        f"Schema {schemas[0]} -> {schemas[1]}",
        "",
    ]
    n_sub = n_same = 0
    for u in sorted(ref.keys() - new.keys()):
        lines.append(f"- service {u} {ref[u]['name']} missing from generated output")
        n_sub += 1
    for u in sorted(new.keys() - ref.keys()):
        lines.append(f"- service {u} {new[u]['name']} not in reference")
        n_sub += 1
    if ref_chars is not None:
        reg = _diff_registry(ref_chars, new_chars)
        if reg:
            lines += ["## Characteristics registry"] + reg + [""]
    else:
        lines += [
            f"Reference has no characteristics registry; generated registry has {len(new_chars)} entries.",
            "",
        ]
    info_lines = []
    for u in sorted(ref.keys() & new.keys()):
        sub, info = _diff_service(ref[u], new[u])
        if sub:
            n_sub += 1
            lines.append(f"## {u} {new[u]['name']}")
            lines += [f"- {s}" for s in sub] + [""]
        else:
            n_same += 1
        if info:
            info_lines.append(f"### {u} {new[u]['name']}")
            info_lines += [f"- {s}" for s in info] + [""]
    lines += ["", "# Informational differences", ""] + info_lines
    (ctx.build_dir / "diff_report.md").write_text("\n".join(lines) + "\n")
    return (
        f"{n_same} services match the reference on membership/requirements, "
        f"{n_sub} differ (see build/diff_report.md)"
    )
