"""Nap + kiem tra cau hinh (mac dinh `configs/train_v2.yaml`; `train_v1.yaml` giu de tai lap) va tinh `config_sha256` (nhan dien cau hinh trong metadata model)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "train_v2.yaml"
_TARGETS = ("log_ratio", "log_price", "price")
_REQUIRED_TOP = ("version", "seed", "horizons", "target_transform", "accuracy_tolerance", "stable_threshold", "min_rows", "cv",
                 "scoring", "models", "selection")


class ConfigError(ValueError):
    pass


# Chieu tot cua metric dung de XEP HANG mo hinh. Cau hinh co the khai bao `selection.directions`; thieu thi dung bang nay (v1: accuracy_at_tol -> max).
DEFAULT_METRIC_DIRECTIONS = {"accuracy_at_tol": "max", "mae": "min", "rmse": "min", "smape": "min", "mape": "min", "median_ape": "min", "r2": "max",
                             "directional_accuracy": "max"}


def metric_direction(cfg: dict[str, Any], metric: str) -> str:
    declared = (cfg.get("selection") or {}).get("directions") or {}
    direction = declared.get(metric, DEFAULT_METRIC_DIRECTIONS.get(metric))
    if direction not in ("min", "max"):
        raise ConfigError(f"khong biet chieu tot (min|max) cua metric {metric!r}")
    return direction


def _validate_selection(cfg: dict[str, Any]) -> None:
    selection = cfg["selection"]
    for key in ("primary_metric", "tie_break"):
        if key not in selection:
            raise ConfigError(f"selection thieu '{key}'")
        metric_direction(cfg, selection[key])
    declared = selection.get("directions")
    if declared is not None:
        bad = {k: v for k, v in declared.items() if v not in ("min", "max")}
        if bad:
            raise ConfigError(f"selection.directions chi nhan min|max, nhan duoc {bad}")
    if selection.get("final_tie", "model_name_asc") != "model_name_asc":
        raise ConfigError("selection.final_tie chi ho tro model_name_asc (xac dinh)")


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    missing = [key for key in _REQUIRED_TOP if key not in cfg]
    if missing:
        raise ConfigError(f"thieu khoa cau hinh: {missing}")
    if cfg["target_transform"] not in _TARGETS:
        raise ConfigError(f"target_transform phai thuoc {_TARGETS}, nhan duoc {cfg['target_transform']!r}")
    if not 0 < float(cfg["accuracy_tolerance"]) < 1:
        raise ConfigError("accuracy_tolerance phai nam trong (0, 1)")
    for split in ("train", "validation", "test"):
        if split not in cfg["min_rows"]:
            raise ConfigError(f"min_rows thieu '{split}'")
    _validate_selection(cfg)
    for name in ("rf", "xgb"):
        space = cfg["models"].get(name, {}).get("random_search", {}).get("space", {})
        if not space or any(not isinstance(v, list) or not v for v in space.values()):
            raise ConfigError(f"models.{name}.random_search.space phai la dict cac danh sach roi rac khong rong")
    return _with_hash(cfg)


def _with_hash(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg.pop("config_sha256", None)
    cfg["config_sha256"] = hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return cfg


def apply_overrides(cfg: dict[str, Any], *, device: str | None = None) -> dict[str, Any]:
    """Ghi de cau hinh luc chay (vd Colab GPU: `device='cuda'` cho XGBoost) va TINH LAI `config_sha256` de bao cao phan anh cau hinh thuc te."""
    if device is not None:
        if device not in ("cpu", "cuda"):
            raise ConfigError(f"device phai la 'cpu' hoac 'cuda', nhan duoc {device!r}")
        cfg["models"]["xgb"].setdefault("fixed", {})["device"] = device
    return _with_hash(cfg)
