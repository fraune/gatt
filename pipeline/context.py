"""Shared state and helpers for pipeline steps: paths, cached HTTP fetches, JSON I/O."""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Mozilla/5.0 (compatible; gatt-services-pipeline)"
BLOCK_MARKERS = (b"Attention Required! | Cloudflare", b"cf-error-details")


class FetchError(RuntimeError):
    pass


class PipelineError(RuntimeError):
    """A step found a problem that must stop the run (the runner exits non-zero)."""


@dataclass
class Context:
    root: Path = ROOT
    refresh: bool = False
    reference: Path | None = (
        None  # diff reference; None = previous run's output (see previous_output)
    )
    workers: int = 8

    @property
    def cache_dir(self):
        return self.root / "cache"

    @property
    def build_dir(self):
        return self.root / "build"

    @property
    def output_dir(self):
        return self.root / "output"

    @property
    def output_path(self):
        return self.output_dir / "gatt_services.json"

    @property
    def candidate_output(self):
        """Dataset written by the build step; published to output_path only if validation passes."""
        return self.build_dir / "gatt_services.candidate.json"

    @property
    def previous_output(self):
        """Snapshot of output/gatt_services.json taken by the build step before it overwrites it."""
        return self.build_dir / "previous_gatt_services.json"

    @property
    def reference_path(self):
        return self.reference or self.previous_output

    @property
    def overrides_path(self):
        return self.root / "overrides" / "overrides.yaml"

    def log(self, msg):
        print(f"    {msg}", flush=True)

    # --- HTTP -----------------------------------------------------------------

    def fetch(self, url, dest, min_bytes=1):
        """Download url to dest unless a cached copy exists (or --refresh). Returns dest."""
        dest = Path(dest)
        if dest.exists() and dest.stat().st_size >= min_bytes and not self.refresh:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_err = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = resp.read()
                if any(m in body[:5000] for m in BLOCK_MARKERS):
                    raise FetchError(f"blocked by Cloudflare: {url}")
                if len(body) < min_bytes:
                    raise FetchError(f"response too small ({len(body)} bytes): {url}")
                tmp = dest.with_suffix(dest.suffix + ".part")
                tmp.write_bytes(body)
                tmp.replace(dest)
                return dest
            except (urllib.error.URLError, TimeoutError, FetchError) as e:
                last_err = e
                if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                    break
                time.sleep(2 * (attempt + 1))
        raise FetchError(f"failed to fetch {url}: {last_err}")

    def fetch_many(self, pairs, min_bytes=1):
        """Fetch [(url, dest), ...] concurrently. Returns {url: dest or FetchError}."""

        def one(pair):
            url, dest = pair
            try:
                return url, self.fetch(url, dest, min_bytes)
            except FetchError as e:
                return url, e

        with ThreadPoolExecutor(self.workers) as ex:
            return dict(ex.map(one, pairs))

    # --- build artifacts ------------------------------------------------------

    def write_json(self, name, obj):
        path = self.build_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
        return path

    def read_json(self, name):
        path = self.build_dir / name
        if not path.exists():
            sys.exit(f"missing build artifact {path}; run the earlier steps first")
        return json.loads(path.read_text())

    def load_overrides(self):
        data = yaml.safe_load(self.overrides_path.read_text()) or {}
        data.setdefault("aliases", {})
        data.setdefault("services", {})
        # YAML may parse keys like 1800 as ints; normalize to 4-char uppercase hex strings.
        data["services"] = {
            normalize_uuid(k): v or {} for k, v in data["services"].items()
        }
        return data


def normalize_uuid(value):
    """0x180D / 6157 / '180d' -> '180D'."""
    if isinstance(value, int):
        return f"{value:04X}"
    s = str(value).strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return s.upper().zfill(4)


def norm_text(s):
    """Normalize for comparisons: lowercase, unify dashes/quotes, drop zero-width chars, collapse spaces."""
    s = s.replace("\u200b", "").replace("\xad", "").replace("\xa0", " ")
    s = re.sub(r"[‐-―]", "-", s)
    s = s.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def version_key(v):
    """'1.0.1' / 'v1.1' / '1.1.2ed2' -> tuple of ints for ordering."""
    return tuple(int(n) for n in re.findall(r"\d+", v or "")) or (0,)
