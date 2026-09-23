"""Run the GATT services pipeline.

.venv/bin/python -m pipeline                    # all steps
.venv/bin/python -m pipeline --from extract     # resume from a step (earlier artifacts in build/)
.venv/bin/python -m pipeline --only resolve build
.venv/bin/python -m pipeline --refresh          # ignore the download cache
"""

import argparse
import sys
import time
from pathlib import Path

from .context import Context, PipelineError
from .steps import (
    s01_assigned_numbers,
    s02_spec_catalog,
    s03_select_specs,
    s04_download_docs,
    s05_extract_tables,
    s06_resolve,
    s07_build_output,
    s08_validate,
    s09_diff_reference,
)

STEPS = [
    ("assigned-numbers", s01_assigned_numbers),
    ("catalog", s02_spec_catalog),
    ("select", s03_select_specs),
    ("download", s04_download_docs),
    ("extract", s05_extract_tables),
    ("resolve", s06_resolve),
    ("build", s07_build_output),
    ("validate", s08_validate),
    ("diff", s09_diff_reference),
]
NAMES = [n for n, _ in STEPS]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--only",
        nargs="+",
        choices=NAMES,
        metavar="STEP",
        help=f"run only these steps: {NAMES}",
    )
    group.add_argument(
        "--from",
        dest="start",
        choices=NAMES,
        metavar="STEP",
        help="run this step and all later ones",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-download instead of using cache/"
    )
    parser.add_argument(
        "--reference",
        type=Path,
        help="reference JSON for the diff step (default: the previous run's output, build/previous_gatt_services.json)",
    )
    args = parser.parse_args(argv)

    ctx = Context(refresh=args.refresh)
    if args.reference:
        ctx.reference = args.reference.resolve()
    if args.only:
        selected = [(n, m) for n, m in STEPS if n in args.only]
    elif args.start:
        selected = STEPS[NAMES.index(args.start) :]
    else:
        selected = STEPS

    for i, (name, module) in enumerate(selected, 1):
        print(f"[{i}/{len(selected)}] {name}", flush=True)
        t0 = time.monotonic()
        try:
            summary = module.run(ctx)
        except PipelineError as e:
            print(f"    FAILED: {e}", file=sys.stderr, flush=True)
            return 1
        print(f"    -> {summary} ({time.monotonic() - t0:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
