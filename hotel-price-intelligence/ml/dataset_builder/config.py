"""`build_config_json` cua dataset build (spec muc 16): envelope bat bien, canonical JSON + SHA-256.

Envelope bat buoc (DB CHECK `chk_dataset_config_projection`): import_batch_id, reference_algorithm_version,
label_config, feature_config, label_config_sha256, feature_config_sha256, reference_quality_gate,
split_selection_policy, purge_gap_days, random_seed. Them: anomaly, eligibility_overrides, purpose,
builder_version - moi tham so lam doi sample/feature/label deu nam TRONG identity nay.

Split dates, state van hanh, heartbeat, library versions, checksum output la ket qua/metadata, khong nam day.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from . import BUILDER_VERSION
from . import env  # noqa: F401 - nap backend vao sys.path truoc khi import app.*
from .code_identity import builder_code_manifest
from .feature_spec import HORIZONS, feature_config, label_config

from app.warehouse.hashing import canonical_json, sha256_hex  # noqa: E402

REFERENCE_ALGORITHM_VERSION = "warehouse-causal-1.0.0"
DATASET_VERSION_RE = re.compile(r"^ds_[0-9]{8}_[a-z0-9]{1,24}$")  # <= 40 ky tu (VARCHAR(40))
PURPOSES = ("rehearsal", "dev", "official")
ANOMALY_MODES = ("evaluation_asof", "retrospective_full")

# Tieu chi du lieu da dang ky truoc (discuss/canonical-key-duplicates/05 muc 1.2): 1 dong/horizon.
REGISTERED_SUFFICIENCY_GATES = {
    "h1_h3_h7": {
        "eligible_prediction_dates": {"train": 28, "validation": 7, "test": 7},
        "labeled_samples": {"train": 5000, "validation": 1000, "test": 1000},
        "hotels_per_eval_split": {"total": 150, "per_city": 20},
    },
    "h14": {
        "eligible_prediction_dates": {"train": 14, "validation": 7, "test": 7},
        "labeled_samples": {"train": 2500, "validation": 500, "test": 500},
        "hotels_per_eval_split": {"total": 100, "per_city": 15},
    },
}


def split_selection_policy() -> dict[str, Any]:
    """Chinh sach CHON bien split - xac dinh, chi dung do dai/coverage cua mau, khong nhin metric mo hinh."""
    return {
        "name": "gate_driven_v1",
        "horizon_candidates_desc": [14, 7, 3, 1],
        "gates": REGISTERED_SUFFICIENCY_GATES,
        "val_test_window_days": "min_eligible_dates(H) + H",
        "train_min_window_days": "min_eligible_dates_train(H) + H",
        "test_is_latest_window": True,
        "fallback_when_infeasible": {"train": 0.6, "validation": 0.2, "test": 0.2, "sufficiency": "none"},
        "primary_requires_hotel_seen_in_train": True,
    }


DEFAULT_REQUIRED_LABEL_SPLITS = {"h1": ["train", "validation", "test"]}


def reference_quality_gate(*, min_runs: int, min_coverage: float) -> dict[str, Any]:
    return {
        "min_runs": int(min_runs),
        "min_coverage": float(min_coverage),
        "unique_per_item": True,
        "require_price_not_null": True,
        "evidence": "run completed + include_reference, item success + include_reference, is_sold_out=0, canonical_room_key<>EMPTY",
        "rank_order": [
            "observation_count = distinct_item_count DESC", "item_coverage DESC", "distinct_run_count DESC",
            "max_occupancy<=2 DESC", "observation_count DESC", "canonical_room_key ASC", "canonical_rate_key ASC",
        ],
    }


def _iso(value: dt.datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def build_config(
    *, import_batch_id: str, purpose: str, min_runs: int, min_coverage: float,
    anomaly_mode: str, anomaly_cutoff_at: dt.datetime | None, anomaly_registry_file_sha256: str,
    random_seed: int = 20261005, purge_gap_days: int = 14, exclude_hotels: tuple[str, ...] = (),
    required_label_splits: dict[str, list[str]] | None = None, builder_code: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`min_runs`/`min_coverage` BAT BUOC lay tu `etl_config` da pin cua batch (khong doc settings/.env).

    `builder_code` = manifest hash moi file ma builder dung de quyet dinh ket qua (code_identity.py, GPT review vong 2 R2-M1); mac dinh tinh
    tu cay hien tai. No nam TRONG identity cua dataset: doi mot byte ma/dependency => config_sha256 khac => phai tao dataset_version moi."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose {purpose!r} khong thuoc {PURPOSES}")
    if anomaly_mode not in ANOMALY_MODES:
        raise ValueError(f"anomaly_mode {anomaly_mode!r} khong thuoc {ANOMALY_MODES}")
    if anomaly_mode == "evaluation_asof" and anomaly_cutoff_at is None:
        raise ValueError("evaluation_asof can anomaly_cutoff_at (1 cutoff duy nhat cho ca train/val/test).")
    label = label_config()
    feature = feature_config()
    return {
        "builder_version": BUILDER_VERSION,
        "builder_code": builder_code if builder_code is not None else builder_code_manifest(),
        "purpose": purpose,
        "import_batch_id": import_batch_id,
        "reference_algorithm_version": REFERENCE_ALGORITHM_VERSION,
        "label_config": label,
        "label_config_sha256": sha256_hex(canonical_json(label)),
        "feature_config": feature,
        "feature_config_sha256": sha256_hex(canonical_json(feature)),
        "reference_quality_gate": reference_quality_gate(min_runs=min_runs, min_coverage=min_coverage),
        "split_selection_policy": split_selection_policy(),
        "purge_gap_days": int(purge_gap_days),
        "random_seed": int(random_seed),
        "horizons_days": list(HORIZONS),
        "eligibility_overrides": {"exclude_hotels": sorted(exclude_hotels)},
        # PASS gate spec muc 17: nhan usable > 0 o cac (horizon, split) BAT BUOC theo cau hinh (sufficiency tach rieng, khong chan PASS).
        "pass_requirements": {"required_label_splits": required_label_splits if required_label_splits is not None
                              else DEFAULT_REQUIRED_LABEL_SPLITS},
        "anomaly": {
            "mode": anomaly_mode,
            "cutoff_at": _iso(anomaly_cutoff_at) if anomaly_cutoff_at else None,
            "registry_file_sha256": anomaly_registry_file_sha256,
        },
    }


def config_sha256(config: dict[str, Any]) -> str:
    return sha256_hex(canonical_json(config))


def default_registry_sha256() -> str:
    """SHA-256 cua file anomaly registry hien hanh (git tracked) - ghim vao config va manifest."""
    from app.scraper.anomaly_registry_lib import DEFAULT_REGISTRY_PATH, registry_file_sha256
    return registry_file_sha256(DEFAULT_REGISTRY_PATH.read_bytes())


def default_registry_sha256_for(path) -> str:
    from pathlib import Path
    from app.scraper.anomaly_registry_lib import registry_file_sha256
    return registry_file_sha256(Path(path).read_bytes())


def verify_config_projection(config: dict[str, Any], row: dict[str, Any]) -> list[str]:
    """So envelope JSON voi cac cot projection cua manifest + hash; tra danh sach sai lech (rong = khop).
    DB CHECK chi bao ve hinh dang JSON/gia tri projection; hash mat ma thuoc ve ung dung (spec muc 16)."""
    problems: list[str] = []
    if config_sha256(config) != row["build_config_sha256"]:
        problems.append("build_config_sha256 lech canonical JSON hien tai")
    if sha256_hex(canonical_json(config["label_config"])) != config["label_config_sha256"]:
        problems.append("label_config_sha256 trong JSON lech sub-object")
    if sha256_hex(canonical_json(config["feature_config"])) != config["feature_config_sha256"]:
        problems.append("feature_config_sha256 trong JSON lech sub-object")
    for key, column in (("import_batch_id", "import_batch_id"), ("reference_algorithm_version", "reference_algorithm_version"),
                        ("label_config_sha256", "label_config_sha256"), ("feature_config_sha256", "feature_config_sha256"),
                        ("purge_gap_days", "purge_gap_days"), ("random_seed", "random_seed")):
        if config[key] != row[column]:
            problems.append(f"cot {column}={row[column]!r} lech JSON {config[key]!r}")
    return problems
