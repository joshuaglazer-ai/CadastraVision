"""Build candidate plots for completed processing jobs.

    python -m backend.scripts.build_plots JOB-2FEF95C563
    python -m backend.scripts.build_plots --all

Runs the same step the server runs after each new job (and on
POST /api/processing/{job_id}/plots), for jobs that finished before it
existed. The jobs' other layers are not changed. Stop the server first or
run it while no job is processing: both write to the same state database.
"""

from __future__ import annotations

import argparse

from backend.config import settings
from backend.core.store import get_store
from backend.services import plot_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", nargs="*", help="job ids")
    parser.add_argument("--all", action="store_true", help="every completed job")
    args = parser.parse_args(argv)

    store = get_store()
    ids = args.jobs or ([j["job_id"] for j in store.list_jobs(limit=500) if j["status"] == "COMPLETED"] if args.all else [])
    if not ids:
        parser.error("name one or more job ids, or --all")
    failed = 0
    for job_id in ids:
        state = plot_service.build_for_job(job_id, settings, store, actor="OPERATOR-CLI")
        if state["status"] == "COMPLETED":
            area = state.get("area_m2") or {}
            print(f"{job_id}: {state['plots']} plots from {state['seed_buildings']} buildings, "
                  f"median {area.get('median')} m2, one-building share "
                  f"{state.get('one_building_share_seed_rule')} (buildings >= 5 m2) / "
                  f"{state.get('one_building_share_all')} (all detected), "
                  f"{state['elapsed_seconds']} s")
        else:
            failed += 1
            print(f"{job_id}: FAILED {state.get('error')}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
