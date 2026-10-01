"""Rebuild reference-room calibration from existing observations without deleting them.

Run from backend/ after applying the latest reference migration:
    python scripts/recompute_references.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.durable import DurableQueueRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true",
        help="Retire active definitions and rebuild them; without this flag only prints the plan.",
    )
    args = parser.parse_args()
    if not args.apply:
        print("Dry run: observations will be preserved; active/proposed references will be retired and rebuilt.")
        print("Re-run with --apply to execute.")
        return

    repository = DurableQueueRepository()
    repaired_urls = repository.repair_not_bookable_item_urls()
    result = repository.recalibrate_all_references()
    print(
        "Recalibration complete: "
        f"series={result['series']}, retired={result['retired']}, "
        f"approved={result['approved']}, proposed={result['proposed']}, "
        f"orphaned_removed={result['orphaned_removed']}, repaired_urls={repaired_urls}"
    )


if __name__ == "__main__":
    main()
