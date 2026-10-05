"""Chay/resume dataset build (spec muc 18). Chay bang `eda/.venv/Scripts/python.exe`.

    build_dataset.py --database D --dataset-version V --apply [--stop-after STEP]
    build_dataset.py --database D --dataset-version V --rebuild-from STEP
    build_dataset.py --database D --dataset-version V --retry-failed-step --reason "..." --actor claude
    build_dataset.py --database D --dataset-version V --status

Thu tu step: causal_references -> item_matches -> samples_labels -> split -> features_labels -> validation.
Exit code: 0 OK/PASS; 1 build loi/validation FAIL; 2 sai tham so; 3 lock dang bi giu; 4 circuit-breaker mo.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import runner  # noqa: E402
from dataset_builder.manifest import STEPS, ManifestError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="build_dataset (state machine, resume, rebuild-from, retry).")
    parser.add_argument("--database", required=True)
    parser.add_argument("--dataset-version", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--rebuild-from", choices=STEPS)
    group.add_argument("--retry-failed-step", action="store_true")
    group.add_argument("--status", action="store_true")
    parser.add_argument("--reason")
    parser.add_argument("--actor")
    parser.add_argument("--stop-after", choices=STEPS)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    try:
        if args.status:
            print(json.dumps(runner.describe(args.database, args.dataset_version), indent=2, default=str))
            return 0
        if args.apply:
            row = runner.apply(args.database, args.dataset_version, output_root=args.output_root, stop_after=args.stop_after)
        elif args.rebuild_from:
            row = runner.rebuild_from(args.database, args.dataset_version, args.rebuild_from, output_root=args.output_root,
                                      stop_after=args.stop_after)
        else:
            if not args.reason or not args.actor:
                print("FAIL: --retry-failed-step can --reason va --actor", file=sys.stderr)
                return 2
            row = runner.retry_failed_step(args.database, args.dataset_version, reason=args.reason, actor=args.actor,
                                           output_root=args.output_root, stop_after=args.stop_after)
    except runner.LockHeldError as exc:
        print(f"LOCK: {exc}", file=sys.stderr)
        return 3
    except runner.CircuitOpenError as exc:
        print(f"CIRCUIT: {exc}", file=sys.stderr)
        return 4
    except (ManifestError, runner.BuildFailedError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: row[k] for k in ("dataset_version", "status", "last_completed_step", "active_step", "split_train_end",
                                           "split_validation_end", "fail_reason")}, indent=2, default=str))
    return 0 if row["status"] in ("pass", "running") else 1


if __name__ == "__main__":
    raise SystemExit(main())
