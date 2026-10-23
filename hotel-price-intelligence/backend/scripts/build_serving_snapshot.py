"""Dựng serving snapshot hồi cứu (offline, inference-only, chạy bằng anaconda — môi trường đã replay).

    C:\\Users\\ADMIN\\anaconda3\\python.exe -B scripts\\build_serving_snapshot.py --hotels-meta <hotels_meta_*.json> [--out-root <dir>]

Mặc định ghi vào `hotel-price-intelligence/data/serving/v1/<snapshotId>/` (thư mục `data/` đã gitignore). Không ghi đè snapshotId có sẵn. KHÔNG fit, KHÔNG DB.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.ml_serving.registry import REGISTRY_PATH, WORKSPACE_ROOT, load_registry, sha256_file  # noqa: E402
from app.ml_serving.snapshot import build_snapshot_payload, load_hotels_meta, publish_snapshot  # noqa: E402

DEFAULT_OUT = WORKSPACE_ROOT / "hotel-price-intelligence" / "data" / "serving" / "v1"
BUNDLE_IDS = {1: "reg-h1-hgb_l1-08", 3: "reg-h3-hgb_l1-ratio1"}
CLASSIFIER_BUNDLE_IDS = {1: "cls-h1-xgb-06", 3: "cls-h3-xgb-06"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hotels-meta", required=True, type=Path, help="hotels_meta_*.json đã xuất (có hotelsSha256)")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-classifier", action="store_true", help="bỏ nhánh phân loại (capability dependency_missing)")
    args = parser.parse_args()

    registry = load_registry()
    hotels, hotels_info = load_hotels_meta(args.hotels_meta)
    dataset_dirs = {}
    for h, bundle_id in BUNDLE_IDS.items():
        dataset_id = registry.bundle_entry(bundle_id)["datasetVersion"]
        dataset_dirs[h] = WORKSPACE_ROOT / "outputs" / "datasets" / dataset_id
    payload = build_snapshot_payload(registry, hotels, dataset_dirs=dataset_dirs, bundle_ids=BUNDLE_IDS,
                                     classifier_bundle_ids=None if args.no_classifier else CLASSIFIER_BUNDLE_IDS)
    final = publish_snapshot(args.out_root, payload, hotels_info=hotels_info, registry_sha256=sha256_file(REGISTRY_PATH))
    manifest = payload["manifest"]
    print(f"snapshot: {final}")
    print(f"snapshotId={manifest['snapshotId']} asOf={manifest['dataAsOf']} counts={manifest['counts']}")
    for bundle in manifest["bundles"]:
        print(f"  replay h{bundle['horizon']}: rows={bundle['replay']['rows']} unrouted max={bundle['replay']['unrouted']['maxAbsDiffVnd']:.3e} routed max={bundle['replay']['routed']['maxAbsDiffVnd']:.3e}")
    for bundle in manifest.get("classifierBundles", []):
        print(f"  classifier replay h{bundle['horizon']}: rows={bundle['replay']['rows']} max|dp|={bundle['replay']['maxAbsProbabilityDiff']:.3e} label_mismatch={bundle['replay']['labelMismatchCount']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
