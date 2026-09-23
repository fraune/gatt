"""Step 6: resolve spec-table names to official Assigned Numbers characteristics (name + UUID).

Resolution order for each extracted row:
  1. placeholders   - a category row (e.g. "ESS Characteristic") expanded from the SIG permitted list
  2. manual_uuids   - explicit UUID for names Assigned Numbers does not list (always carries a flag)
  3. exact name     - case/dash-insensitive match against characteristic_uuids.yaml
  4. aliases        - per-service, then global (spec abbreviation -> official name)
  5. trailing parenthetical stripped, e.g. "Content Control ID (CCID)"
Anything else is reported as unresolved; nothing is guessed.

Output: build/resolved.json
"""

import re

from ..context import norm_text

REQ_WORDS = {"M": "mandatory", "O": "optional", "C": "conditional"}
WORD_REQS = {v: k for k, v in REQ_WORDS.items()}


def _index(assigned):
    by_name, by_id = {}, {}
    for c in assigned["characteristics"]:
        by_name.setdefault(norm_text(c["name"]), []).append(c)
        by_id[c["id"]] = c
    return by_name, by_id


def _lookup(name, by_name, aliases):
    candidates = [name]
    for table in aliases:
        for k, v in table.items():
            if norm_text(k) == norm_text(name):
                candidates.append(v)
    stripped = re.sub(r"\s*\([^)]*\)\s*$", "", name)
    if stripped != name:
        candidates.append(stripped)
    for cand in candidates:
        hits = by_name.get(norm_text(cand))
        if hits:
            return hits, cand
    return None, None


def _resolve_service(uuid, ext, overrides, assigned, by_name, by_id, svc_ids):
    """Resolve one service's extracted rows; returns {characteristics, problems, info}."""
    ov = overrides["services"].get(uuid, {})
    aliases = [ov.get("aliases", {}), overrides["aliases"]]
    placeholders = {
        norm_text(k): v or {} for k, v in ov.get("placeholders", {}).items()
    }
    manual = {norm_text(k): v for k, v in ov.get("manual_uuids", {}).items()}
    req_ov = {norm_text(k): v for k, v in ov.get("requirements", {}).items()}
    chars, problems, info, seen = [], [], [], set()

    def add(c_uuid, c_name, req, cond, spec_name, flag=None, source=None):
        if c_uuid in seen:
            problems.append(
                f'duplicate characteristic {c_uuid} {c_name} (from "{spec_name}")'
            )
            return
        seen.add(c_uuid)
        o = req_ov.get(norm_text(c_name)) or req_ov.get(norm_text(spec_name))
        if o:
            req = WORD_REQS[o["requirement"]] if "requirement" in o else req
            cond = o.get("condition", cond) if req == "C" or "condition" in o else None
        entry = {"uuid": c_uuid, "name": c_name, "requirement": REQ_WORDS[req]}
        if cond and req != "M":
            entry["condition"] = cond
        if flag:
            entry["flag"] = flag
        if norm_text(spec_name) != norm_text(c_name):
            entry["specName"] = spec_name
        if source:
            entry["source"] = source
        chars.append(entry)

    rows = ext.get("rows", []) if ext.get("status") == "ok" else []
    used_permitted = False
    for row in rows:
        key = norm_text(row["spec_name"])
        if row["requirement"] == "?":
            problems.append(
                f'unrecognized requirement {row["raw_requirement"]} for "{row["spec_name"]}"'
            )
            continue
        if key in placeholders:
            ph = placeholders[key]
            ids = assigned["permitted"].get(svc_ids.get(uuid), [])
            if not ids:
                problems.append(
                    f'placeholder "{row["spec_name"]}" has no permitted list for {svc_ids.get(uuid)}'
                )
            used_permitted = True
            req = (
                WORD_REQS[ph["requirement"]]
                if "requirement" in ph
                else row["requirement"]
            )
            cond = ph.get("condition", row.get("condition"))
            for pid in ids:
                c = by_id.get(pid)
                if not c:
                    problems.append(
                        f"permitted id {pid} not in characteristic_uuids.yaml"
                    )
                    continue
                add(
                    c["uuid"],
                    c["name"],
                    req,
                    cond,
                    row["spec_name"],
                    source=assigned["permitted_sources"].get(svc_ids.get(uuid)),
                )
            continue
        if key in manual:
            m = manual[key]
            add(
                m["uuid"].upper(),
                m.get("name", row["spec_name"]),
                row["requirement"],
                row.get("condition"),
                row["spec_name"],
                flag=m["flag"],
            )
            continue
        hits, matched = _lookup(row["spec_name"], by_name, aliases)
        if not hits:
            problems.append(
                f'unresolved name "{row["spec_name"]}" (add an alias or manual_uuids entry)'
            )
            continue
        if len(hits) > 1:
            problems.append(f'ambiguous name "{matched}": {[h["uuid"] for h in hits]}')
        add(
            hits[0]["uuid"],
            hits[0]["name"],
            row["requirement"],
            row.get("condition"),
            row["spec_name"],
        )

    permitted_ids = assigned["permitted"].get(svc_ids.get(uuid))
    if permitted_ids and not used_permitted and ov.get("permitted") != "ignore":
        problems.append(
            f"service has a permitted-characteristics list ({len(permitted_ids)} entries) "
            "but no placeholder row consumed it; add a placeholder or permitted: ignore"
        )
    for name in (
        set(req_ov)
        - {norm_text(c["name"]) for c in chars}
        - {norm_text(c.get("specName", "")) for c in chars}
    ):
        info.append(f'requirements override "{name}" matched no characteristic')

    return {"characteristics": chars, "problems": problems, "info": info}


def run(ctx):
    assigned = ctx.read_json("assigned_numbers.json")
    extracted = ctx.read_json("extracted.json")
    overrides = ctx.load_overrides()
    by_name, by_id = _index(assigned)
    svc_ids = {s["uuid"]: s["id"] for s in assigned["services"]}
    resolved = {}

    for uuid, ext in extracted.items():
        resolved[uuid] = _resolve_service(
            uuid, ext, overrides, assigned, by_name, by_id, svc_ids
        )

    ctx.write_json("resolved.json", resolved)
    n_problems = sum(len(r["problems"]) for r in resolved.values())
    for uuid, r in resolved.items():
        for p in r["problems"]:
            ctx.log(f"WARN {uuid}: {p}")
        for p in r["info"]:
            ctx.log(f"INFO {uuid}: {p}")
    return f"{sum(len(r['characteristics']) for r in resolved.values())} characteristics resolved, {n_problems} problems"
