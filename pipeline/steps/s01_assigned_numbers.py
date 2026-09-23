"""Step 1: fetch the Bluetooth SIG Assigned Numbers (services, characteristics, permitted lists).

Every file is fetched at one pinned commit of the SIG repository so the run is internally
consistent and the exact Assigned Numbers version can be recorded in the output metadata. The
commit is resolved from the head of `main` and cached; `--refresh` resolves it again.

Output: build/assigned_numbers.json
"""

import json
import re
from datetime import UTC, datetime

import yaml

from ..context import normalize_uuid

REPO_WEB = "https://bitbucket.org/bluetooth-SIG/public"
REPO_API = "https://api.bitbucket.org/2.0/repositories/bluetooth-SIG/public"
SERVICES = "assigned_numbers/uuids/service_uuids.yaml"
CHARACTERISTICS = "assigned_numbers/uuids/characteristic_uuids.yaml"
PROFILES_DIR = "assigned_numbers/profiles_and_services/"

# A few YAML names carry LaTeX markup, e.g. "CO\\textsubscript{2} Concentration" (0x2B8C), which the
# Assigned Numbers PDF renders as CO₂. Convert sub/superscripts to Unicode; any other markup is
# left in place and rejected by the validate step.
_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SUP = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")


def clean_name(name):
    name = re.sub(
        r"\\textsubscript\{([^}]*)\}", lambda m: m.group(1).translate(_SUB), name
    )
    name = re.sub(
        r"\\textsuperscript\{([^}]*)\}", lambda m: m.group(1).translate(_SUP), name
    )
    return name


def _resolve_commit(ctx):
    """Head commit of main (hash + date), cached until --refresh."""
    url = f"{REPO_API}/commits/main?pagelen=1&fields=values.hash,values.date"
    path = ctx.fetch(url, ctx.cache_dir / "assigned_numbers" / "head_commit.json")
    head = json.loads(path.read_text())["values"][0]
    return head["hash"], head["date"]


def _load_uuid_list(ctx, commit, repo_path):
    path = ctx.fetch(
        f"{REPO_WEB}/raw/{commit}/{repo_path}",
        ctx.cache_dir / "assigned_numbers" / commit[:12] / repo_path.split("/")[-1],
    )
    entries = yaml.safe_load(path.read_text())["uuids"]
    return [
        {
            "uuid": normalize_uuid(e["uuid"]),
            "name": clean_name(e["name"]),
            "id": e["id"],
        }
        for e in entries
    ]


def _list_dir(ctx, commit, repo_path):
    """List a repo directory through the Bitbucket API, following pagination."""
    out, url, page = [], f"{REPO_API}/src/{commit}/{repo_path}?pagelen=100", 0
    while url:
        page += 1
        cache = (
            ctx.cache_dir
            / "assigned_numbers"
            / commit[:12]
            / "listings"
            / f"{repo_path.strip('/').replace('/', '__')}.{page}.json"
        )
        data = json.loads(ctx.fetch(url, cache).read_text())
        out += data.get("values", [])
        url = data.get("next")
    return out


def _permitted_lists(ctx, commit):
    """Find every *_permitted_characteristics.yaml under profiles_and_services/ and merge them."""
    permitted, sources = {}, {}
    for entry in _list_dir(ctx, commit, PROFILES_DIR):
        if entry.get("type") != "commit_directory":
            continue
        for f in _list_dir(ctx, commit, entry["path"] + "/"):
            if not f["path"].endswith("_permitted_characteristics.yaml"):
                continue
            path = ctx.fetch(
                f"{REPO_WEB}/raw/{commit}/{f['path']}",
                ctx.cache_dir
                / "assigned_numbers"
                / commit[:12]
                / f["path"].split("/")[-1],
            )
            for block in yaml.safe_load(path.read_text())["permitted_characteristics"]:
                permitted.setdefault(block["service"], []).extend(
                    block["characteristics"]
                )
                sources[block["service"]] = f["path"]
    return permitted, sources


def run(ctx):
    commit, commit_date = _resolve_commit(ctx)
    services = _load_uuid_list(ctx, commit, SERVICES)
    characteristics = _load_uuid_list(ctx, commit, CHARACTERISTICS)
    permitted, sources = _permitted_lists(ctx, commit)
    ctx.write_json(
        "assigned_numbers.json",
        {
            "fetched": datetime.now(UTC).date().isoformat(),
            "commit": commit,
            "commit_date": commit_date,
            "sources": {
                "services": f"{REPO_WEB}/src/{commit}/{SERVICES}",
                "characteristics": f"{REPO_WEB}/src/{commit}/{CHARACTERISTICS}",
            },
            "services": services,
            "characteristics": characteristics,
            "permitted": permitted,
            "permitted_sources": sources,
        },
    )
    return (
        f"commit {commit[:12]} ({commit_date[:10]}): {len(services)} services, "
        f"{len(characteristics)} characteristics, {len(permitted)} permitted-characteristic lists"
    )
