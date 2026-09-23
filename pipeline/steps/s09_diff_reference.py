"""Step 9: compare output/gatt_catalog.json against a reference file.

Default reference: build/previous_gatt_catalog.json, the output of the previous run (snapshotted by
the validate step). Use --reference to compare against something else, e.g. a file exported from git.
Either file may be schema 1 (services[].characteristics), 2.0 (registries + serviceDefinitions as
arrays), or 3.0 (the same, keyed by UUID); each is normalized to one shape before comparing.

Differences in characteristic membership, requirement, included services, or registry contents are
"substantive"; condition wording, spec labels, and verification notes are informational.

Output: build/diff_report.md
"""

import json


def _as_v3(data):
    """Schema 2.0 (arrays with a uuid field) -> schema 3.0 shape (objects keyed by UUID)."""

    def keyed(items):
        return {i["uuid"]: {k: v for k, v in i.items() if k != "uuid"} for i in items}

    return {
        "characteristics": {c["uuid"]: c["name"] for c in data["characteristics"]},
        "services": {s["uuid"]: s["name"] for s in data["services"]},
        "serviceDefinitions": {
            d["uuid"]: {
                **{k: v for k, v in d.items() if k != "uuid"},
                "characteristicRefs": keyed(d["characteristicRefs"]),
                "includedServices": keyed(d["includedServices"]),
            }
            for d in data["serviceDefinitions"]
        },
    }


def _normalize(data):
    """-> (services {uuid: {...}}, characteristic registry, service registry); registries are
    None for schema 1, which has none."""
    if "serviceDefinitions" in data:  # schema 2.0 or 3.0
        if isinstance(data["serviceDefinitions"], list):
            data = _as_v3(data)
        char_names, svc_names = data["characteristics"], data["services"]
        services = {}
        for uuid, d in data["serviceDefinitions"].items():
            services[uuid] = {
                "name": svc_names.get(uuid, "?"),
                "status": d["verificationStatus"],
                "specification": d["specification"],
                "refs": {
                    ref_uuid: {
                        "name": char_names.get(ref_uuid, "?"),
                        "requirement": r["requirement"],
                        "condition": r.get("condition"),
                    }
                    for ref_uuid, r in d["characteristicRefs"].items()
                },
                "included": {
                    inc_uuid: i["requirement"]
                    for inc_uuid, i in d["includedServices"].items()
                },
            }
        return services, char_names, svc_names
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
    return services, None, None


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


def _diff_registry(ref_reg, new_reg, kind):
    lines = []
    for u in sorted(ref_reg.keys() - new_reg.keys()):
        lines.append(f"- removed {kind} {u} {ref_reg[u]}")
    for u in sorted(new_reg.keys() - ref_reg.keys()):
        lines.append(f"- added {kind} {u} {new_reg[u]}")
    for u in sorted(ref_reg.keys() & new_reg.keys()):
        if ref_reg[u] != new_reg[u]:
            lines.append(f'- renamed {kind} {u}: "{ref_reg[u]}" -> "{new_reg[u]}"')
    return lines


def run(ctx):
    reference = ctx.reference_path
    if not reference.exists():
        return f"no reference at {reference} (first run?); skipped"
    ref_data, new_data = (
        json.loads(reference.read_text()),
        json.loads(ctx.output_path.read_text()),
    )
    ref, ref_chars, ref_svcs = _normalize(ref_data)
    new, new_chars, new_svcs = _normalize(new_data)
    # The generated output always has registries; {} only guards the type.
    new_chars, new_svcs = new_chars or {}, new_svcs or {}
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
    if ref_chars is not None and ref_svcs is not None:
        for title, reg in (
            (
                "Characteristics registry",
                _diff_registry(ref_chars, new_chars, "characteristic"),
            ),
            ("Services registry", _diff_registry(ref_svcs, new_svcs, "service")),
        ):
            if reg:
                lines += [f"## {title}"] + reg + [""]
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
