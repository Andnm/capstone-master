"""Nap + kiem tra `configs/train_v1.yaml` va tinh `config_sha256` (nhan dien cau hinh trong metadata model)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "train_v1.yaml"
_TARGETS = ("log_ratio", "log_price", "price")
_REQUIRED_TOP = ("version", "seed", "horizons", "target_transform", "accuracy_tolerance", "stable_threshold", "min_rows", "cv",
                 "scoring", "models", "selection")


class ConfigError(ValueError):
    pass


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
