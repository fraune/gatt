"""Step 7: assemble the schema 2.0 dataset as a candidate file (validated and published by step 8).

Registries come straight from Assigned Numbers: every characteristic and every service, whether
or not any modeled spec references it. serviceDefinitions hold one entry per service.

verificationStatus per service definition:
  verified   - the characteristic list comes from the latest adopted spec and every entry resolved
               to an Assigned Numbers UUID; or, for services with no table, the spec text that
               proves it (expect_text) was found
  unverified - anything less: no spec/table, unresolved or ambiguous rows, a missing expected
               phrase, or a characteristic whose UUID is not confirmed by Assigned Numbers
               (overrides manual_uuids). characteristicRefs and includedServices are left empty
               and verificationNotes says why. Nothing partial is emitted.

Outputs: build/gatt_services.candidate.json, build/problems.md
"""

import json
from datetime import UTC, datetime

SCHEMA_VERSION = "2.0"


def _included(ov, ext, problems):
    out = []
    for inc in ov.get("included", []):
        entry = {
            "uuid": str(inc["uuid"]).upper(),
            "requirement": inc.get("requirement", "optional"),
        }
        if entry["requirement"] == "conditional" and inc.get("condition"):
            entry["condition"] = " ".join(inc["condition"].split())
        out.append(entry)
        phrase = inc.get("expect_text")
        if phrase and not ext.get("text_checks", {}).get(phrase):
            problems.append(f'included-service evidence not found in spec: "{phrase}"')
    for text in ext.get("included_sections", []):
        if not out and "no additional services" not in text.lower():
            problems.append(
                f'spec has an "Included services" section but overrides list none: "{text[:200]}"'
            )
    return out


def _ref(c):
    ref = {"uuid": c["uuid"], "requirement": c["requirement"]}
    if c["requirement"] == "conditional":
        ref["condition"] = c.get("condition") or ""
    return ref


def _definition(svc, ov, spec, ext, res):
    problems = list(res["problems"])
    if not spec:
        problems.insert(0, "no adopted specification found on bluetooth.com")
    elif ext.get("status") == "error":
        problems.insert(0, ext["error"])
    expected = ov.get("expect_text")
    if expected and not ext.get("text_checks", {}).get(expected):
        problems.append(f'expected phrase not found in spec: "{expected}"')
    included = _included(ov, ext, problems)
    flags = [
        f"{c['name']} ({c['uuid']}): {' '.join(c['flag'].split())}"
        for c in res["characteristics"]
        if c.get("flag")
    ]

    definition = {
        "uuid": svc["uuid"],
        "specification": spec["label"] if spec else None,
        "specificationUrl": spec["doc_url"] if spec else None,
    }
    note = " ".join(ov.get("note", "").split())
    if problems or flags:
        reasons = problems + flags
        definition["verificationStatus"] = "unverified"
        definition["verificationNotes"] = " ".join(
            ["Characteristic list withheld: " + " | ".join(reasons)]
            + ([note] if note else [])
        )
        definition["includedServices"] = []
        definition["characteristicRefs"] = []
    else:
        definition["verificationStatus"] = "verified"
        if note:
            definition["verificationNotes"] = note
        definition["includedServices"] = included
        definition["characteristicRefs"] = [_ref(c) for c in res["characteristics"]]
    return definition, problems + flags


def run(ctx):
    assigned = ctx.read_json("assigned_numbers.json")
    specs = ctx.read_json("service_specs.json")["services"]
    extracted = ctx.read_json("extracted.json")
    resolved = ctx.read_json("resolved.json")
    overrides = ctx.load_overrides()["services"]

    definitions, report = [], []
    for svc in assigned["services"]:
        uuid = svc["uuid"]
        definition, reasons = _definition(
            svc,
            overrides.get(uuid, {}),
            specs.get(uuid),
            extracted.get(uuid, {}),
            resolved.get(uuid, {"characteristics": [], "problems": []}),
        )
        definitions.append(definition)
        if reasons:
            report.append(
                (uuid, svc["name"], definition["verificationStatus"], reasons)
            )

    dataset = {
        "metadata": {
            "generatedAt": datetime.now(UTC).date().isoformat(),
            "primarySource": (
                "Bluetooth SIG Assigned Numbers, bluetooth-SIG/public repository commit "
                f"{assigned['commit'][:12]} ({assigned['commit_date'][:10]})"
            ),
            "schemaVersion": SCHEMA_VERSION,
        },
        "characteristics": [
            {"uuid": c["uuid"], "name": c["name"]} for c in assigned["characteristics"]
        ],
        "services": [
            {"uuid": s["uuid"], "name": s["name"]} for s in assigned["services"]
        ],
        "serviceDefinitions": definitions,
    }
    ctx.candidate_output.parent.mkdir(parents=True, exist_ok=True)
    ctx.candidate_output.write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False) + "\n"
    )

    lines = ["# Pipeline problems", ""]
    for uuid, name, status, reasons in report:
        lines.append(f"## {uuid} {name} ({status})")
        lines += [f"- {r}" for r in reasons] + [""]
    (ctx.build_dir / "problems.md").write_text("\n".join(lines) + "\n")

    counts = {}
    for d in definitions:
        counts[d["verificationStatus"]] = counts.get(d["verificationStatus"], 0) + 1
    return (
        f"wrote {ctx.candidate_output.relative_to(ctx.root)}: "
        f"{len(dataset['characteristics'])} characteristics, {len(dataset['services'])} services, "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    )
