"""Doc cau hinh `configs/train.yaml` + `config_sha256` + VALIDATE o bien. Pin du lieu/snapshot nam TRONG config.

`config_sha256` = hash cua cau hinh da PARSE (canonical JSON): doi gia tri/khoa => hash moi; comment va dinh dang YAML khong doi danh tinh.
`load_config` mac dinh VALIDATE (production: preflight, runner, goi Colab). Khong co duong tu CLI de chay khong pin: moi horizon yeu cau dung MOT dataset pin va dung MOT snapshot pin.
Hop dong nhan la hang so trong ma (`labels.THRESHOLD`, `labels.LABELS`); config chi duoc phep NHAC LAI dung gia tri do, khong duoc doi (doi => ConfigError, khong nhan roi bo qua).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .labels import LABELS, THRESHOLD

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "train.yaml"
SUPPORTED_HORIZONS = (1, 3)                  # dataset dev hien co; h7/h14 chua du ngay (sufficiency)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
DATASET_PIN_HASHES = ("output_checksums_sha256", "dataset_contract_sha256", "samples_file_sha256", "feature_list_sha256")
DATASET_PIN_FIELDS = ("horizon", "dataset_version", "evaluation_horizons", *DATASET_PIN_HASHES, "n_rows", "n_entries", "n_features")
SNAPSHOT_PIN_FIELDS = ("horizon", "snapshot_file_sha256", "manifest_sha256")


class ConfigError(ValueError):
    pass


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


ENV_PACKAGES = ("scikit-learn", "xgboost", "numpy", "pandas")        # phien ban phai khop chinh xac (fail-closed) truoc khi huan luyen that
QUOTA = {"lr": 6, "hgb": 8, "xgb": 8}            # EVAL_PROTOCOL §3 (khoa): doi quota la doi protocol
FAMILY_KIND = {"lr": "linear", "hgb": "tree", "xgb": "tree"}
WEIGHT_MODES = ("none", "balanced")


def _grid_size(space: dict[str, Any]) -> int:
    size = 1
    for values in space.values():
        size *= len(values)
    return size


def _validate_code_stage(cfg: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    tuning = cfg.get("tuning")
    if (not isinstance(tuning, dict) or not _is_int(tuning.get("sampler_seed")) or tuning.get("family_order") != list(QUOTA) or not isinstance(tuning.get("families"), dict)
            or tuning.get("sampling_strategy") != "paired_weight"):
        problems.append(f"tuning phai co sampler_seed (int), sampling_strategy == paired_weight, family_order == {list(QUOTA)}, families (mapping): {tuning!r}")
    else:
        for fam, quota in QUOTA.items():
            spec = tuning["families"].get(fam)
            if not isinstance(spec, dict) or spec.get("kind") != FAMILY_KIND[fam] or spec.get("n_candidates") != quota or not isinstance(spec.get("fixed"), dict):
                problems.append(f"tuning.families.{fam}: can kind={FAMILY_KIND[fam]}, n_candidates={quota} (quota khoa), fixed (mapping)")
                continue
            weights, n_configs = spec.get("weights"), spec.get("n_configs")
            if weights != list(WEIGHT_MODES) or not _is_int(n_configs) or n_configs * len(WEIGHT_MODES) != quota:
                problems.append(f"tuning.families.{fam}: weights phai la {list(WEIGHT_MODES)} va n_configs x 2 == {quota} (nhan weights={weights!r}, n_configs={n_configs!r})")
            space = spec.get("space")
            if not isinstance(space, dict) or not space or any(not isinstance(v, list) or not v for v in space.values()) or "weight" in space:
                problems.append(f"tuning.families.{fam}.space phai la mapping cac danh sach khong rong, KHONG chua `weight` (weight di theo cap)")
                continue
            if _is_int(n_configs) and _grid_size(space) < n_configs:
                problems.append(f"tuning.families.{fam}: luoi {_grid_size(space)} < n_configs {n_configs}")
    if not isinstance(tuning, dict) or not _HEX64.match(str(tuning.get("candidate_set_sha256", ""))):
        problems.append("tuning.candidate_set_sha256 phai la sha256 hex 64 ky tu (van tay tap ung vien da lay mau; lech => dung som)")
    env = cfg.get("environment")
    versions = env.get("expected_versions") if isinstance(env, dict) else None
    if (not isinstance(versions, dict) or set(versions) != set(ENV_PACKAGES) or any(not isinstance(v, str) or not v for v in versions.values())
            or not isinstance(env.get("record_only"), list)):
        problems.append(f"environment phai co expected_versions cho dung {list(ENV_PACKAGES)} (chuoi phien ban) va record_only (danh sach): {env!r}")
    ablation = cfg.get("ablation")
    if ablation != {"a1": "a1_ratio", "a2": "a2_no_history", "a2_n_features": 21}:
        problems.append(f"ablation phai la {{a1: a1_ratio, a2: a2_no_history, a2_n_features: 21}} (nhan {ablation!r})")
    run = cfg.get("run")
    if (not isinstance(run, dict) or run.get("device") not in ("cpu", "cuda") or not _is_int(run.get("wall_clock_soft_cap_seconds")) or run["wall_clock_soft_cap_seconds"] < 1
            or not _is_int(run.get("n_jobs")) or not isinstance(run.get("probability_tolerance"), (int, float)) or not 0 < run["probability_tolerance"] <= 1e-3):
        problems.append(f"run khong hop le (device cpu|cuda, wall_clock_soft_cap_seconds>=1, n_jobs int, 0<probability_tolerance<=1e-3): {run!r}")
    return problems


def validate_config(cfg: dict[str, Any]) -> None:
    """Fail-closed: moi vi pham duoc gom lai va bao mot lan."""
    problems: list[str] = []
    horizons = cfg.get("horizons")
    if (not isinstance(horizons, list) or not horizons or any(not _is_int(h) for h in horizons) or len(set(horizons)) != len(horizons)
            or any(h not in SUPPORTED_HORIZONS for h in horizons)):
        problems.append(f"horizons={horizons!r} phai la danh sach khong rong, khong trung, tap con cua {list(SUPPORTED_HORIZONS)}")
        horizons = []
    if not _is_int(cfg.get("seed")):
        problems.append("seed phai la so nguyen")
    labels = cfg.get("labels")
    if not isinstance(labels, dict) or labels.get("threshold") != THRESHOLD or labels.get("classes") != list(LABELS):
        problems.append(f"labels phai dung hop dong co dinh: threshold={THRESHOLD}, classes={list(LABELS)} (nhan duoc {labels!r})")
    if cfg.get("primary_requires_seen_hotel") is not True:
        problems.append("primary_requires_seen_hotel phai la true (mau so primary cua protocol)")
    cv = cfg.get("cv")
    if not isinstance(cv, dict) or not _is_int(cv.get("min_train_days")) or cv["min_train_days"] < 1 or not isinstance(cv.get("n_splits"), dict):
        problems.append("cv phai co min_train_days (int>=1) va n_splits (mapping)")
    else:
        for h in horizons:
            if not _is_int(cv["n_splits"].get(str(h))) or cv["n_splits"][str(h)] < 2:
                problems.append(f"cv.n_splits[{h!r}] phai la int >= 2")
    panel = cfg.get("panel")
    if (not isinstance(panel, dict) or panel.get("momentum_threshold") != THRESHOLD or not isinstance(panel.get("tie_atol"), (int, float)) or not 0 < panel["tie_atol"] < 1e-3
            or not isinstance(panel.get("practical_gain_macro_f1"), (int, float)) or not 0 < panel["practical_gain_macro_f1"] < 1):
        problems.append(f"panel khong hop le (momentum_threshold phai = {THRESHOLD}; 0 < tie_atol < 1e-3; 0 < practical_gain_macro_f1 < 1): {panel!r}")
    boot = cfg.get("bootstrap")
    if (not isinstance(boot, dict) or not _is_int(boot.get("n")) or boot["n"] < 1000 or not _is_int(boot.get("seed"))
            or not isinstance(boot.get("min_valid_fraction"), (int, float)) or not 0 < boot["min_valid_fraction"] <= 1):
        problems.append(f"bootstrap khong hop le: {boot!r}")
    problems += _validate_code_stage(cfg)
    for kind, key, fields, hashes in (("dataset", "dataset_pins", DATASET_PIN_FIELDS, DATASET_PIN_HASHES), ("snapshot", "baseline_snapshot_pins", SNAPSHOT_PIN_FIELDS, ("snapshot_file_sha256", "manifest_sha256"))):
        pins = cfg.get(key)
        if not isinstance(pins, list):
            problems.append(f"{key} phai la danh sach")
            continue
        seen = [p.get("horizon") if isinstance(p, dict) else None for p in pins]
        for h in horizons:
            if seen.count(h) != 1:
                problems.append(f"{key}: horizon {h} can dung 1 pin, co {seen.count(h)}")
        extra = [x for x in seen if x not in horizons]
        if extra:
            problems.append(f"{key}: pin cho horizon khong duoc yeu cau: {extra}")
        for p in pins:
            if not isinstance(p, dict):
                problems.append(f"{key}: muc khong phai mapping")
                continue
            missing = [f for f in fields if f not in p]
            if missing:
                problems.append(f"{key}[h{p.get('horizon')}]: thieu truong {missing}")
            for f in hashes:
                if f in p and not _HEX64.match(str(p[f])):
                    problems.append(f"{key}[h{p.get('horizon')}].{f} khong phai sha256 hex 64 ky tu")
            if kind == "dataset" and "evaluation_horizons" in p and p["evaluation_horizons"] != [p.get("horizon")]:
                problems.append(f"{key}[h{p.get('horizon')}].evaluation_horizons phai la [{p.get('horizon')}]")
            if kind == "dataset":
                for f in ("n_rows", "n_entries", "n_features"):
                    if f in p and (not _is_int(p[f]) or p[f] < 1):
                        problems.append(f"{key}[h{p.get('horizon')}].{f} phai la so nguyen duong")
    if problems:
        raise ConfigError("cau hinh KHONG hop le: " + "; ".join(problems))


def load_config(path: Path | str | None = None, *, validate: bool = True) -> dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: khong phai mapping")
    data = dict(data)
    data.pop("config_sha256", None)
    if validate:
        validate_config(data)
    data["config_sha256"] = canonical_sha256(data)
    return data


def _single_pin(cfg: dict[str, Any], key: str, horizon: int) -> dict[str, Any]:
    pins = [p for p in cfg.get(key, []) if int(p["horizon"]) == int(horizon)]
    if len(pins) != 1:
        raise ConfigError(f"{key}: can dung 1 pin cho h{horizon}, co {len(pins)}")
    return pins[0]


def dataset_pin(cfg: dict[str, Any], horizon: int) -> dict[str, Any]:
    return _single_pin(cfg, "dataset_pins", horizon)


def snapshot_pin(cfg: dict[str, Any], horizon: int) -> dict[str, Any]:
    return _single_pin(cfg, "baseline_snapshot_pins", horizon)


def override_device(cfg: dict[str, Any], device: str) -> dict[str, Any]:
    """Ban sao cau hinh voi `run.device` khac; config_sha256 duoc tinh lai (doi thiet bi la doi danh tinh cau hinh)."""
    import copy
    if device not in ("cpu", "cuda"):
        raise ConfigError(f"device {device!r} khong hop le")
    new = copy.deepcopy(cfg)
    new["run"]["device"] = device
    new.pop("config_sha256", None)
    validate_config(new)
    new["config_sha256"] = canonical_sha256(new)
    return new
