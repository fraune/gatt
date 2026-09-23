"""Step 8: validate the candidate catalog, then publish it to output/gatt_catalog.json.

Fails the run (non-zero exit, output/ left untouched) if any check fails:
  - referential integrity: every characteristicRefs key exists in `characteristics`, every
    includedServices key exists in `services`
  - no duplicate keys anywhere in the file (JSON parsers would silently keep the last one)
  - every UUID key is 4 uppercase hex digits; registry names are non-empty and free of markup
  - serviceDefinitions has exactly the same keys as the services registry
  - requirement is mandatory | optional | conditional; condition present iff conditional
  - verificationStatus is verified | unverified; unverified definitions have empty maps and notes
  - no unexpected keys (catches schema drift)

On success the previous output is snapshotted to build/previous_gatt_catalog.json (for the diff
step) and the candidate is copied to output/gatt_catalog.json.

Outputs: output/gatt_catalog.json, build/validation.md
"""

import json
import re
import shutil

from ..context import PipelineError

SCHEMA_VERSION = "3.0"
UUID16_RE = re.compile(r"^[0-9A-F]{4}$")
# LaTeX-style markup leaking from the Assigned Numbers YAML (see s01 clean_name).
MARKUP_RE = re.compile(r"[\\{}]")
REQUIREMENTS = {"mandatory", "optional", "conditional"}
STATUSES = {"verified", "unverified"}
KEYS = {
    "top": ({"metadata", "characteristics", "services", "serviceDefinitions"}, set()),
    "metadata": ({"generatedAt", "primarySource", "schemaVersion"}, set()),
    "definition": (
        {
            "specification",
            "specificationUrl",
            "verificationStatus",
            "includedServices",
            "characteristicRefs",
        },
        {"verificationNotes"},
    ),
    "entry": ({"requirement"}, {"condition"}),
}


class DuplicateKeys:
    """object_pairs_hook that records duplicate keys instead of silently dropping them."""

    def __init__(self):
        self.duplicates = []

    def __call__(self, pairs):
        seen = {}
        for key, value in pairs:
            if key in seen:
                self.duplicates.append(key)
            seen[key] = value
        return seen


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


def _check_uuid_map(obj, where, errors):
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object keyed by UUID")
        return {}
    for key in obj:
        if not UUID16_RE.match(key):
            errors.append(f"{where}: malformed UUID key {key!r}")
    return obj


def _check_registry(registry, label, errors):
    registry = _check_uuid_map(registry, label, errors)
    for uuid, name in registry.items():
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.{uuid}: name must be a non-empty string")
        elif MARKUP_RE.search(name):
            errors.append(f"{label}.{uuid}: name contains markup: {name!r}")
    return set(registry)


def _check_entry(entry, where, errors):
    if not _check_keys(entry, "entry", where, errors):
        return
    req = entry["requirement"]
    if req not in REQUIREMENTS:
        errors.append(f"{where}: invalid requirement {req!r}")
    has_condition = bool(str(entry.get("condition", "")).strip())
    if req == "conditional" and not has_condition:
        errors.append(f"{where}: conditional without a condition")
    if req != "conditional" and "condition" in entry:
        errors.append(f"{where}: condition present on a {req} entry")


def validate(dataset, duplicate_keys=()):
    errors = [f"duplicate key {k!r} in file" for k in duplicate_keys]
    if not _check_keys(dataset, "top", "catalog", errors):
        return errors
    _check_keys(dataset["metadata"], "metadata", "metadata", errors)
    version = dataset["metadata"].get("schemaVersion")
    if version != SCHEMA_VERSION:
        errors.append(
            f"metadata.schemaVersion is {version!r}, expected {SCHEMA_VERSION!r}"
        )
    char_ids = _check_registry(dataset["characteristics"], "characteristics", errors)
    svc_ids = _check_registry(dataset["services"], "services", errors)

    definitions = _check_uuid_map(
        dataset["serviceDefinitions"], "serviceDefinitions", errors
    )
    for uuid in sorted(svc_ids - definitions.keys()):
        errors.append(f"services.{uuid} has no serviceDefinition")
    for uuid in sorted(definitions.keys() - svc_ids):
        errors.append(f"serviceDefinitions.{uuid}: not in the services registry")

    for uuid, d in definitions.items():
        where = f"serviceDefinitions.{uuid}"
        if not _check_keys(d, "definition", where, errors):
            continue
        status = d["verificationStatus"]
        if status not in STATUSES:
            errors.append(f"{where}: invalid verificationStatus {status!r}")
        if status == "unverified":
            if d["characteristicRefs"] or d["includedServices"]:
                errors.append(f"{where}: unverified definition must have empty maps")
            if not str(d.get("verificationNotes", "")).strip():
                errors.append(f"{where}: unverified definition needs verificationNotes")
        if status == "verified" and not d["specificationUrl"]:
            errors.append(f"{where}: verified definition without a specificationUrl")

        refs = _check_uuid_map(
            d["characteristicRefs"], f"{where}.characteristicRefs", errors
        )
        for ref_uuid, entry in refs.items():
            rw = f"{where}.characteristicRefs.{ref_uuid}"
            if ref_uuid not in char_ids:
                errors.append(
                    f"{rw}: dangling reference (not in characteristics registry)"
                )
            _check_entry(entry, rw, errors)

        included = _check_uuid_map(
            d["includedServices"], f"{where}.includedServices", errors
        )
        for inc_uuid, entry in included.items():
            iw = f"{where}.includedServices.{inc_uuid}"
            if inc_uuid not in svc_ids:
                errors.append(f"{iw}: dangling reference (not in services registry)")
            if inc_uuid == uuid:
                errors.append(f"{iw}: service includes itself")
            _check_entry(entry, iw, errors)
    return errors


def run(ctx):
    hook = DuplicateKeys()
    dataset = json.loads(ctx.candidate_output.read_text(), object_pairs_hook=hook)
    errors = validate(dataset, hook.duplicates)
    report = ctx.build_dir / "validation.md"
    if errors:
        report.write_text(
            "# Validation FAILED\n\n" + "\n".join(f"- {e}" for e in errors) + "\n"
        )
        for e in errors[:20]:
            ctx.log(f"ERROR {e}")
        raise PipelineError(
            f"validation failed with {len(errors)} error(s); output not published "
            f"(see {report.relative_to(ctx.root)})"
        )

    definitions = dataset["serviceDefinitions"].values()
    n_refs = sum(len(d["characteristicRefs"]) for d in definitions)
    n_inc = sum(len(d["includedServices"]) for d in definitions)
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
