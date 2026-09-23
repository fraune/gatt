"""Step 8: compare output/gatt_services.json against a reference file.

Default reference: build/previous_gatt_services.json, the output of the previous run (snapshotted by
the build step). Use --reference to compare against something else, e.g. a file exported from git.

Differences in characteristic membership, requirement, or included services are "substantive";
name/specification-label/condition wording differences are reported separately as informational.

Output: build/diff_report.md
"""

import json


def _load(path):
    return {s["uuid"]: s for s in json.loads(path.read_text())["services"]}


def _diff_service(ref, new):
    sub, info = [], []
    reworded = []
    rc = {c["uuid"]: c for c in ref["characteristics"]}
    nc = {c["uuid"]: c for c in new["characteristics"]}
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
        if a["name"] != b["name"]:
            info.append(f'{u}: name "{a["name"]}" -> "{b["name"]}"')
        if (
            a.get("condition")
            and b.get("condition")
            and a["condition"] != b["condition"]
        ):
            reworded.append(b["name"])
        if bool(a.get("flag")) != bool(b.get("flag")):
            sub.append(
                f"{u} {b['name']}: flag {'added' if b.get('flag') else 'removed'}"
            )
    if reworded:
        info.append(
            f"condition wording differs for {len(reworded)} characteristic(s): {', '.join(reworded)}"
        )
    ri = {i["uuid"]: i for i in ref.get("includedServices", [])}
    ni = {i["uuid"]: i for i in new.get("includedServices", [])}
    if ri.keys() != ni.keys():
        sub.append(f"included services {sorted(ri)} -> {sorted(ni)}")
    for u in ri.keys() & ni.keys():
        if ri[u].get("requirement") != ni[u].get("requirement"):
            sub.append(
                f"included {u}: requirement {ri[u].get('requirement')} -> {ni[u].get('requirement')}"
            )
    if ref.get("specification") != new.get("specification"):
        info.append(
            f'specification "{ref.get("specification")}" -> "{new.get("specification")}"'
        )
    if ref.get("verification") != new.get("verification"):
        info.append(
            f"verification {ref.get('verification')} -> {new.get('verification')}"
        )
    if ref.get("gattService", True) != new.get("gattService", True):
        sub.append(f"gattService {ref.get('gattService')} -> {new.get('gattService')}")
    return sub, info


def run(ctx):
    new_path = ctx.output_dir / "gatt_services.json"
    reference = ctx.reference_path
    if not reference.exists():
        return f"no reference at {reference} (first run?); skipped"
    ref, new = _load(reference), _load(new_path)
    shown = (
        reference.relative_to(ctx.root)
        if reference.is_relative_to(ctx.root)
        else reference
    )

    lines = [f"# Diff: {new_path.relative_to(ctx.root)} vs {shown}", ""]
    n_sub = n_same = 0
    for u in sorted(ref.keys() - new.keys()):
        lines.append(f"- service {u} {ref[u]['name']} missing from generated output")
        n_sub += 1
    for u in sorted(new.keys() - ref.keys()):
        lines.append(f"- service {u} {new[u]['name']} not in reference")
        n_sub += 1
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
