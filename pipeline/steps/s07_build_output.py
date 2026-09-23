"""Step 7: assemble output/gatt_services.json and a problems report.

Verification status per service:
  verified - table extracted and every characteristic resolved from primary sources, or (for
             services with no table) every expect_text phrase was found in the spec
  partial  - extracted, but some characteristics carry a flag or rows failed to resolve
  flagged  - no document, no usable table, or an expected phrase was missing

Outputs: output/gatt_services.json, build/problems.md, and build/previous_gatt_services.json
(a copy of the prior output, taken before overwriting it, used by the diff step).
"""

import json
import shutil
from datetime import UTC, datetime


def _included(uuid, ov, ext, services_by_uuid, problems):
    out = []
    for inc in ov.get("included", []):
        iu = str(inc["uuid"]).upper()
        entry = {
            "uuid": iu,
            "name": services_by_uuid.get(iu, {}).get("name", "?"),
            "requirement": inc.get("requirement", "optional"),
        }
        if inc.get("condition"):
            entry["condition"] = inc["condition"]
        out.append(entry)
        if iu not in services_by_uuid:
            problems.append(f"included service {iu} is not in Assigned Numbers")
        phrase = inc.get("expect_text")
        if phrase and not ext.get("text_checks", {}).get(phrase):
            problems.append(f'included-service evidence not found in spec: "{phrase}"')
    for text in ext.get("included_sections", []):
        if not out and "no additional services" not in text.lower():
            problems.append(
                f'spec has an "Included services" section but overrides list none: "{text[:200]}"'
            )
    return out


def run(ctx):
    assigned = ctx.read_json("assigned_numbers.json")
    specs = ctx.read_json("service_specs.json")["services"]
    extracted = ctx.read_json("extracted.json")
    resolved = ctx.read_json("resolved.json")
    overrides = ctx.load_overrides()["services"]
    services_by_uuid = {s["uuid"]: s for s in assigned["services"]}

    services, report = [], []
    for svc in assigned["services"]:
        uuid = svc["uuid"]
        ov, spec = overrides.get(uuid, {}), specs.get(uuid)
        ext, res = (
            extracted.get(uuid, {}),
            resolved.get(uuid, {"characteristics": [], "problems": []}),
        )
        problems = list(res["problems"])
        if ext.get("status") == "error":
            problems.insert(0, ext["error"])
        for phrase, found in ext.get("text_checks", {}).items():
            if not found and phrase == ov.get("expect_text"):
                problems.append(f'expected phrase not found in spec: "{phrase}"')

        entry = {
            "uuid": uuid,
            "name": svc["name"],
            "specification": spec["label"] if spec else None,
            "specificationUrl": spec["doc_url"] if spec else None,
            "gattService": ov.get("gatt_service", True),
            "includedServices": _included(uuid, ov, ext, services_by_uuid, problems),
            "characteristics": res["characteristics"],
        }
        flagged_chars = any("flag" in c for c in entry["characteristics"])
        if (
            not spec
            or ext.get("status") == "error"
            or any("expected phrase" in p for p in problems)
        ):
            status = "flagged"
        elif problems or flagged_chars:
            status = "partial"
        else:
            status = "verified"
        entry["verification"] = status
        if ov.get("note"):
            entry["notes"] = ov["note"].strip()
        services.append(entry)
        if problems or status != "verified":
            report.append((uuid, svc["name"], status, problems))

    meta = {
        "generated": datetime.now(UTC).date().isoformat(),
        "generator": "pipeline (python -m pipeline); hand-maintained decisions live in overrides/overrides.yaml",
        "serviceListSource": assigned["sources"]["services"],
        "characteristicNameSource": assigned["sources"]["characteristics"],
        "requirementValues": {
            "mandatory": "M in the specification table",
            "optional": "O in the specification table",
            "conditional": "C.n in the specification table; condition holds the footnote text",
        },
        "verificationValues": {
            "verified": "Characteristics and requirements extracted from the latest adopted specification",
            "partial": "Extracted, but at least one characteristic is flagged or unresolved",
            "flagged": "Specification or table could not be found or verified",
        },
        "scope": "Characteristics only; descriptors are omitted. specName is present when the spec table "
        "uses a different name than Assigned Numbers.",
    }
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = ctx.output_dir / "gatt_services.json"
    if out_path.exists():
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_path, ctx.previous_output)
    out_path.write_text(
        json.dumps(
            {"metadata": meta, "services": services}, indent=2, ensure_ascii=False
        )
        + "\n"
    )

    lines = ["# Pipeline problems", ""]
    for uuid, name, status, problems in report:
        lines.append(f"## {uuid} {name} ({status})")
        lines += [f"- {p}" for p in problems] or [
            "- (no problems recorded; see flags on characteristics)"
        ]
        lines.append("")
    (ctx.build_dir / "problems.md").write_text("\n".join(lines) + "\n")

    counts = {}
    for s in services:
        counts[s["verification"]] = counts.get(s["verification"], 0) + 1
    return f"wrote {out_path.relative_to(ctx.root)}: " + ", ".join(
        f"{v} {k}" for k, v in sorted(counts.items())
    )
