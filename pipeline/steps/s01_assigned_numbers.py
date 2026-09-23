"""Step 1: fetch the Bluetooth SIG Assigned Numbers (services, characteristics, permitted lists).

Output: build/assigned_numbers.json
"""

import json
from datetime import UTC, datetime

import yaml

from ..context import normalize_uuid

REPO_RAW = "https://bitbucket.org/bluetooth-SIG/public/raw/main/"
REPO_API = "https://api.bitbucket.org/2.0/repositories/bluetooth-SIG/public/src/main/"
SERVICES = "assigned_numbers/uuids/service_uuids.yaml"
CHARACTERISTICS = "assigned_numbers/uuids/characteristic_uuids.yaml"
PROFILES_DIR = "assigned_numbers/profiles_and_services/"


def _load_uuid_list(ctx, repo_path):
    path = ctx.fetch(
        REPO_RAW + repo_path,
        ctx.cache_dir / "assigned_numbers" / repo_path.split("/")[-1],
    )
    entries = yaml.safe_load(path.read_text())["uuids"]
    return [
        {"uuid": normalize_uuid(e["uuid"]), "name": e["name"], "id": e["id"]}
        for e in entries
    ]


def _list_dir(ctx, repo_path):
    """List a repo directory through the Bitbucket API, following pagination."""
    out, url, page = [], f"{REPO_API}{repo_path}?pagelen=100", 0
    while url:
        page += 1
        cache = (
            ctx.cache_dir
            / "assigned_numbers"
            / "listings"
            / f"{repo_path.strip('/').replace('/', '__')}.{page}.json"
        )
        data = json.loads(ctx.fetch(url, cache).read_text())
        out += data.get("values", [])
        url = data.get("next")
    return out


def _permitted_lists(ctx):
    """Find every *_permitted_characteristics.yaml under profiles_and_services/ and merge them."""
    permitted, sources = {}, {}
    for entry in _list_dir(ctx, PROFILES_DIR):
        if entry.get("type") != "commit_directory":
            continue
        for f in _list_dir(ctx, entry["path"] + "/"):
            if not f["path"].endswith("_permitted_characteristics.yaml"):
                continue
            path = ctx.fetch(
                REPO_RAW + f["path"],
                ctx.cache_dir / "assigned_numbers" / f["path"].split("/")[-1],
            )
            for block in yaml.safe_load(path.read_text())["permitted_characteristics"]:
                permitted.setdefault(block["service"], []).extend(
                    block["characteristics"]
                )
                sources[block["service"]] = f["path"]
    return permitted, sources


def run(ctx):
    services = _load_uuid_list(ctx, SERVICES)
    characteristics = _load_uuid_list(ctx, CHARACTERISTICS)
    permitted, sources = _permitted_lists(ctx)
    ctx.write_json(
        "assigned_numbers.json",
        {
            "fetched": datetime.now(UTC).date().isoformat(),
            "sources": {
                "services": REPO_RAW + SERVICES,
                "characteristics": REPO_RAW + CHARACTERISTICS,
            },
            "services": services,
            "characteristics": characteristics,
            "permitted": permitted,
            "permitted_sources": sources,
        },
    )
    return (
        f"{len(services)} services, {len(characteristics)} characteristics, "
        f"{len(permitted)} permitted-characteristic lists"
    )
