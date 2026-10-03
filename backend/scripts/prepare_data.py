"""Build the layer index ahead of time.

    python -m backend.scripts.prepare_data

The API builds the same index on first start; running this beforehand
simply moves the half-minute wait out of the first page load.
"""

from __future__ import annotations

import time

from backend.config import settings
from backend.core import runtime


def main() -> int:
    settings.ensure_dirs()
    started = time.time()
    indexed = runtime.ensure_source(runtime.EXISTING_SOURCE)
    layers = runtime.get_layers()
    for kind, record in indexed.items():
        if record is None:
            print(f"{kind:10s} unavailable (file missing or unreadable)")
            continue
        stats = layers.stats(runtime.EXISTING_SOURCE, kind)
        print(
            f"{kind:10s} {record['feature_count']:>7,} features  "
            f"{stats['totals']['area_m2'] / 10000:>8.2f} ha  CRS {record['crs']}  "
            f"skipped {record['skipped_count']}"
        )
        for entry in stats["classes"].values():
            print(
                f"    {entry['class_name']:10s} {entry['count']:>6,}  {entry['area_m2']:>12,.1f} m²  "
                f"fragments {entry['fragments']:,}"
            )
    for key, message in runtime.bootstrap_errors().items():
        print(f"ERROR {key}: {message}")
    print(f"Done in {time.time() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
