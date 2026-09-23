"""Step 8: validate the candidate dataset, then publish it to output/gatt_services.json.

Fails the run (non-zero exit, output/ left untouched) if any check fails:
  - referential integrity: every characteristicRefs uuid exists in `characteristics`, every
    includedServices uuid exists in `services`
  - registries: unique, well-formed UUIDs and non-empty names free of markup
  - exactly one serviceDefinition per service in the registry, and none for unknown services
  - requirement is mandatory | optional | conditional; condition present iff conditional
  - verificationStatus is verified | unverified; unverified definitions have empty lists and notes
  - no unexpected keys (catches schema drift)

On success the previous output is snapshotted to build/previous_gatt_services.json (for the diff
step) and the candidate is copied to output/gatt_services.json.

Outputs: output/gatt_services.json, build/validation.md
"""

import json
import re
import shutil

from ..context import PipelineError

UUID16_RE = re.compile(r"^[0-9A-F]{4}$")
# LaTeX-style markup leaking from the Assigned Numbers YAML (see s01 clean_name).
MARKUP_RE = re.compile(r"[\\{}]")
REQUIREMENTS = {"mandatory", "optional", "conditional"}
STATUSES = {"verified", "unverified"}
KEYS = {
    "top": ({"metadata", "characteristics", "services", "serviceDefinitions"}, set()),
    "metadata": ({"generatedAt", "primarySource", "schemaVersion"}, set()),
    "registry": ({"uuid", "name"}, set()),
    "definition": (
        {
            "uuid",
            "specification",
            "specificationUrl",
            "verificationStatus",
            "includedServices",
            "characteristicRefs",
        },
        {"verificationNotes"},
    ),
    "ref": ({"uuid", "requirement"}, {"condition"}),
}


def _check_keys(obj, kind, where, errors):
    required, optional = KEYS[kind]
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object")
        return False
    missing, extra = required - obj.keys(), obj.keys() - required - optional
    if missing:
        errors.append(f"{where}: missing keys {sorted(missing)}")
    if extra:
        errors.append(f"{where}: unexpected keys {sorted(extra)}")
    return not missing


def _check_registry(items, label, errors):
    seen = set()
    for i, item in enumerate(items):
        where = f"{label}[{i}]"
        if not _check_keys(item, "registry", where, errors):
            continue
        if not UUID16_RE.match(str(item["uuid"])):
            errors.append(f"{where}: malformed uuid {item['uuid']!r}")
        if item["uuid"] in seen:
            errors.append(f"{where}: duplicate uuid {item['uuid']}")
        seen.add(item["uuid"])
        if not str(item["name"]).strip():
            errors.append(f"{where}: empty name")
        elif MARKUP_RE.search(str(item["name"])):
            errors.append(f"{where}: name contains markup: {item['name']!r}")
    return seen


def _check_requirement(ref, where, errors):
    req = ref.get("requirement")
    if req not in REQUIREMENTS:
        errors.append(f"{where}: invalid requirement {req!r}")
    has_condition = bool(str(ref.get("condition", "")).strip())
    if req == "conditional" and not has_condition:
        errors.append(f"{where}: conditional without a condition")
    if req != "conditional" and "condition" in ref:
        errors.append(f"{where}: condition present on a {req} entry")


def validate(dataset):
    errors = []
    if not _check_keys(dataset, "top", "dataset", errors):
        return errors
    _check_keys(dataset["metadata"], "metadata", "metadata", errors)
    if dataset["metadata"].get("schemaVersion") != "2.0":
        errors.append(
            f"metadata.schemaVersion is {dataset['metadata'].get('schemaVersion')!r}, expected '2.0'"
        )
    char_ids = _check_registry(dataset["characteristics"], "characteristics", errors)
    svc_ids = _check_registry(dataset["services"], "services", errors)

    defined = set()
    for i, d in enumerate(dataset["serviceDefinitions"]):
        where = (
            f"serviceDefinitions[{i}] ({d.get('uuid') if isinstance(d, dict) else '?'})"
        )
        if not _check_keys(d, "definition", where, errors):
            continue
        if d["uuid"] not in svc_ids:
            errors.append(
                f"{where}: definition for a service not in the services registry"
            )
        if d["uuid"] in defined:
            errors.append(f"{where}: duplicate definition")
        defined.add(d["uuid"])

        status = d["verificationStatus"]
        if status not in STATUSES:
            errors.append(f"{where}: invalid verificationStatus {status!r}")
        if status == "unverified":
            if d["characteristicRefs"] or d["includedServices"]:
                errors.append(f"{where}: unverified definition must have empty lists")
            if not str(d.get("verificationNotes", "")).strip():
                errors.append(f"{where}: unverified definition needs verificationNotes")
        if status == "verified" and not d["specificationUrl"]:
            errors.append(f"{where}: verified definition without a specificationUrl")

        seen_refs = set()
        for j, ref in enumerate(d["characteristicRefs"]):
            rw = f"{where}.characteristicRefs[{j}]"
            if not _check_keys(ref, "ref", rw, errors):
                continue
            if ref["uuid"] not in char_ids:
                errors.append(
                    f"{rw}: dangling reference {ref['uuid']} (not in characteristics registry)"
                )
            if ref["uuid"] in seen_refs:
                errors.append(f"{rw}: duplicate reference {ref['uuid']}")
            seen_refs.add(ref["uuid"])
            _check_requirement(ref, rw, errors)

        for j, inc in enumerate(d["includedServices"]):
            iw = f"{where}.includedServices[{j}]"
            if not _check_keys(inc, "ref", iw, errors):
                continue
            if inc["uuid"] not in svc_ids:
                errors.append(
                    f"{iw}: dangling reference {inc['uuid']} (not in services registry)"
                )
            if inc["uuid"] == d["uuid"]:
                errors.append(f"{iw}: service includes itself")
            _check_requirement(inc, iw, errors)

    for missing in sorted(svc_ids - defined):
        errors.append(f"services registry entry {missing} has no serviceDefinition")
    return errors


def run(ctx):
    dataset = json.loads(ctx.candidate_output.read_text())
    errors = validate(dataset)
    report = ctx.build_dir / "validation.md"
    if errors:
        report.write_text(
            "# Validation FAILED\n\n" + "\n".join(f"- {e}" for e in errors) + "\n"
        )
        for e in errors[:20]:
            ctx.log(f"ERROR {e}")
        raise PipelineError(
            f"validation failed with {len(errors)} error(s); output not published (see {report.relative_to(ctx.root)})"
        )

    n_refs = sum(len(d["characteristicRefs"]) for d in dataset["serviceDefinitions"])
    n_inc = sum(len(d["includedServices"]) for d in dataset["serviceDefinitions"])
    report.write_text(
        f"# Validation passed\n\n- {len(dataset['characteristics'])} characteristics, "
        f"{len(dataset['services'])} services, {len(dataset['serviceDefinitions'])} definitions\n"
        f"- {n_refs} characteristic references and {n_inc} included-service references, all resolved\n"
    )
    if ctx.output_path.exists():
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ctx.output_path, ctx.previous_output)
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ctx.candidate_output, ctx.output_path)
    return (
        f"all checks passed ({n_refs} characteristic refs, {n_inc} included-service refs); "
        f"published {ctx.output_path.relative_to(ctx.root)}"
    )
