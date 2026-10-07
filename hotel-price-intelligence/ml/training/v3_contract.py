"""Hop dong `train-v3` (thread discuss/model-results-improvement-20261008; GPT PASS file 14; C1-C18 o file 11 + 13): hang so, nap cau hinh, dinh nghia dung chung.

Module thuan (khong fit, khong doc dataset). v2 (`training/__init__.py`, `runner.py`, `configs/train_v2.yaml`) BAT BIEN: nhan phien ban cua v3 nam o day, khong sua `TRAINING_VERSION` cua v2.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from sklearn.model_selection import ParameterGrid

from .config import ConfigError, _with_hash

TRAINING_VERSION_V3 = "training-1.3.0"
CONFIG_VERSION_V3 = "train-v3.0.0"
DEFAULT_CONFIG_V3 = Path(__file__).resolve().parents[1] / "configs" / "train_v3.yaml"

KNOWN_FAMILIES = ("hgb_l1", "xgb_abs", "xgb_ph")
KNOWN_CONTROLS = ("hgb_l2", "xgb_l2", "rf_l2", "ridge")
PERSISTENCE = "persistence"
ARMS = ("A", "B")                                   # A: MAE VND that (primary, lift_vnd); B: MAE log (thu nghiem, lift_log)
ARM_METRIC = {"A": "lift_vnd", "B": "lift_log"}

# Thu tu on dinh cua hang (C: `07` G.E6): dung cho moi lan fit/hoan vi de hai cai dat doc lap cho cung ket qua.
STABLE_SORT = ["vn_observation_date", "hotel_id", "checkin_date", "canonical_series_id", "warehouse_record_id"]
DATE_COLUMNS = ("checkin_date", "vn_observation_date")

# Khoi feature "ty le-1.0" (C10'): cong thuc ghim, causal, cung hang, chi dung cot san co; mau so khong huu han hoac <= 0 => NaN (khong inf, khong 0 gia).
RATIO_BLOCK_COLUMNS = ("r_mean14", "r_max14", "r_min14", "log_current", "cv7", "abs_velocity")
RATIO_BLOCK_DEFINITION = (
    "r_mean14=current/price_rolling_mean_14 [mean>0]; r_max14=current/price_max_trailing_14 [den>0]; r_min14=current/price_min_trailing_14 [den>0]; "
    "log_current=ln(current) [current>0]; cv7=price_rolling_std_7/price_rolling_mean_7 [mean>0, std>=0]; abs_velocity=|price_velocity|; "
    "non-finite or non-positive denominator => NaN")
RATIO_BLOCK_SHA256 = hashlib.sha256(RATIO_BLOCK_DEFINITION.encode("utf-8")).hexdigest()

_REQUIRED_TOP = ("version", "seed", "horizons", "target_transform", "accuracy_tolerance", "stable_threshold", "min_rows", "tie_atol", "bootstrap", "cv",
                 "tuning", "controls", "ablation", "routing", "null_test", "claims", "budget", "environment")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def feature_list_sha256(features: list[str]) -> str:
    """Tuan tu hoa CHUAN da thong nhat o thread (GPT file 10): json compact UTF-8, ensure_ascii=False, separators=(',',':')."""
    return sha256_bytes(json.dumps(list(features), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def load_config_v3(path: Path | str = DEFAULT_CONFIG_V3) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in _REQUIRED_TOP if key not in cfg]
    if missing:
        raise ConfigError(f"thieu khoa cau hinh v3: {missing}")
    if cfg["version"] != CONFIG_VERSION_V3:
        raise ConfigError(f"version={cfg['version']!r} != {CONFIG_VERSION_V3!r}")
    if cfg["target_transform"] != "log_ratio":
        raise ConfigError("v3 chi ho tro target_transform=log_ratio")
    if not (float(cfg["tie_atol"]) > 0):
        raise ConfigError("tie_atol phai > 0")
    families = cfg["tuning"]["families"]
    if tuple(cfg["tuning"].get("family_order", ())) != tuple(families):
        raise ConfigError("tuning.family_order phai trung thu tu khai bao cua tuning.families")
    if set(families) - set(KNOWN_FAMILIES):
        raise ConfigError(f"ho khong biet: {sorted(set(families) - set(KNOWN_FAMILIES))}")
    n_iter = int(cfg["tuning"]["n_iter"])
    for name, spec in families.items():
        space = spec.get("space") or {}
        if not space or any(not isinstance(v, list) or not v for v in space.values()):
            raise ConfigError(f"tuning.families.{name}.space phai la dict cac danh sach roi rac khong rong")
        if n_iter > len(ParameterGrid(space)):
            raise ConfigError(f"tuning.families.{name}: n_iter={n_iter} > kich thuoc khong gian {len(ParameterGrid(space))}")
    unknown_controls = set(cfg["controls"]) - set(KNOWN_CONTROLS)
    if unknown_controls:
        raise ConfigError(f"control khong biet: {sorted(unknown_controls)}")
    if tuple(cfg["tuning"].get("control_order", ())) != tuple(cfg["controls"]):
        raise ConfigError("tuning.control_order phai trung thu tu khai bao cua controls")
    for h in cfg["horizons"]:
        if str(h) not in cfg["cv"]["n_splits"]:
            raise ConfigError(f"cv.n_splits thieu horizon {h}")
    for key in ("min_rows", "min_hotels", "min_dates"):
        if int(cfg["routing"][key]) <= 0:
            raise ConfigError(f"routing.{key} phai > 0")
    expected = cfg["environment"].get("expected_versions") or {}
    if not expected:
        raise ConfigError("environment.expected_versions rong")
    return _with_hash(cfg)


def apply_smoke_overrides(cfg: dict[str, Any], *, families: list[str] | None, n_iter: int | None, null_n: int | None) -> dict[str, Any]:
    """Chi cho `--smoke` (kiem cuc bo): thu nho pool/ho/null, TINH LAI config_sha256 de run smoke khong bao gio trung identity voi run that."""
    cfg = json.loads(json.dumps(cfg))
    if families is not None:
        unknown = [f for f in families if f not in cfg["tuning"]["families"]]
        if unknown:
            raise ConfigError(f"smoke families khong biet: {unknown}")
        cfg["tuning"]["families"] = {f: cfg["tuning"]["families"][f] for f in cfg["tuning"]["family_order"] if f in families}
        cfg["tuning"]["family_order"] = list(cfg["tuning"]["families"])
    if n_iter is not None:
        cfg["tuning"]["n_iter"] = int(n_iter)
    if null_n is not None:
        cfg["null_test"]["n"] = int(null_n)
    cfg["smoke"] = True
    return _with_hash(cfg)


# --------------------------------------------------------------------------- dinh nghia dung chung

def stable_sort(frame: pd.DataFrame) -> pd.DataFrame:
    """Chuyen cot ngay sang chuoi ISO (so sanh/sap xep nhu nhau o moi split) roi sap theo STABLE_SORT (mergesort => on dinh)."""
    out = frame.copy()
    for column in DATE_COLUMNS:
        out[column] = out[column].astype(str)
    return out.sort_values(STABLE_SORT, kind="mergesort").reset_index(drop=True)


def add_ratio_block(frame: pd.DataFrame) -> pd.DataFrame:
    """Them 6 cot cua khoi ty le-1.0 (cong thuc o RATIO_BLOCK_DEFINITION). Khong bao gio inf: mau so <=0/NaN/inf => NaN; ket qua phep tinh khong huu han (tran so du mau so
    tiny) => NaN va duoc DEM (`out.attrs['ratio_block_invalid']`, GPT file 16 MIN3) - khong clip am tham."""
    out = frame.copy()
    cur = pd.to_numeric(out["current_price"], errors="coerce").to_numpy(float)
    invalid: dict[str, int] = {}

    def num(column: str) -> np.ndarray:
        return pd.to_numeric(out[column], errors="coerce").to_numpy(float)

    def finalize(name: str, result: np.ndarray, valid_inputs: np.ndarray) -> np.ndarray:
        bad = valid_inputs & ~np.isfinite(result)                 # dau vao hop le nhung ket qua tran so
        invalid[name] = int(bad.sum())
        result = np.where(np.isfinite(result), result, np.nan)
        return result

    def ratio(name: str, den: np.ndarray) -> np.ndarray:
        ok = np.isfinite(cur) & np.isfinite(den) & (den > 0)
        result = np.full(len(out), np.nan)
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            result[ok] = cur[ok] / den[ok]
        return finalize(name, result, ok)

    out["r_mean14"] = ratio("r_mean14", num("price_rolling_mean_14"))
    out["r_max14"] = ratio("r_max14", num("price_max_trailing_14"))
    out["r_min14"] = ratio("r_min14", num("price_min_trailing_14"))
    log_current = np.full(len(out), np.nan)
    ok = np.isfinite(cur) & (cur > 0)
    log_current[ok] = np.log(cur[ok])
    out["log_current"] = finalize("log_current", log_current, ok)
    mean7, std7 = num("price_rolling_mean_7"), num("price_rolling_std_7")
    cv7 = np.full(len(out), np.nan)
    ok = np.isfinite(mean7) & (mean7 > 0) & np.isfinite(std7) & (std7 >= 0)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        cv7[ok] = std7[ok] / mean7[ok]
    out["cv7"] = finalize("cv7", cv7, ok)
    velocity = num("price_velocity")
    abs_velocity = np.full(len(out), np.nan)
    ok = np.isfinite(velocity)
    abs_velocity[ok] = np.abs(velocity[ok])
    out["abs_velocity"] = finalize("abs_velocity", abs_velocity, ok)
    out.attrs["ratio_block_invalid"] = invalid
    return out


class InvalidPredictionError(ValueError):
    """Du bao khong hop le: z khong huu han/sai shape, hoac gia sau nghich dao khong huu han hay khong duong (GPT file 16 M2)."""


def price_from_z(current: np.ndarray, zhat: np.ndarray, *, label: str = "prediction") -> np.ndarray:
    """MOT helper nghich dao muc tieu (C1) cho CV, refit, null, TEST, metric va serialization: `price = current*exp(z)`. Kiem shape + z huu han + current huu han > 0 va gia ket qua
    HUU HAN VA > 0 (exp tran so/underflow ve 0 la loi, KHONG clip). Loi => InvalidPredictionError voi ly do (candidate failed / run khong hoan tat)."""
    current, zhat = np.asarray(current, float), np.asarray(zhat, float)
    if current.shape != zhat.shape or current.ndim != 1:
        raise InvalidPredictionError(f"{label}: shape current {current.shape} != z {zhat.shape} (hoac khong phai vector 1 chieu)")
    if not (np.isfinite(current).all() and (current > 0).all()):
        raise InvalidPredictionError(f"{label}: current_price khong huu han/khong duong")
    if not np.isfinite(zhat).all():
        raise InvalidPredictionError(f"{label}: z khong huu han ({int((~np.isfinite(zhat)).sum())} gia tri)")
    with np.errstate(over="ignore", under="ignore"):
        price = current * np.exp(zhat)
    if not (np.isfinite(price).all() and (price > 0).all()):
        raise InvalidPredictionError(f"{label}: gia du bao khong huu han hoac khong duong sau nghich dao (z trong [{zhat.min():.4g}, {zhat.max():.4g}])")
    return price


def _threshold_fraction(threshold: float) -> tuple[int, int]:
    from fractions import Fraction

    frac = Fraction(str(threshold))
    return frac.numerator, frac.denominator


def strict_changed(y: np.ndarray, cur: np.ndarray, threshold: float = 0.02) -> np.ndarray:
    """|y - cur| > threshold * cur theo SO HOC CHINH XAC (GPT file 16 MIN2): nguong 0.02 = 1/50 nen so sanh `|y-cur|*50 > cur` bang so nguyen (gia VND la so nguyen < 2^53 => chinh xac).
    Dung 2.0% (vd 1000 -> 1020 / 980) KHONG la 'doi'; vuot 2% moi la doi. Khong dung `abs(y/cur - 1) > 0.02` (float lam 1020/1000-1 = 0.020000000000000018 > 0.02)."""
    num, den = _threshold_fraction(threshold)
    return np.abs(np.asarray(y, float) - np.asarray(cur, float)) * den > num * np.asarray(cur, float)


def strata_masks(y: np.ndarray, cur: np.ndarray, threshold: float = 0.02) -> dict[str, np.ndarray]:
    """Strata ex-post (dung y => CHI chan doan/tuyen bo pham vi, KHONG de chon/route/loc). ordinary: 0.5*cur <= y <= 2*cur (bien thuoc ordinary; phep nhan 0.5/2 chinh xac);
    changed: |y-cur| > threshold*cur theo so hoc chinh xac (`strict_changed`); extreme = phan bu cua ordinary."""
    y, cur = np.asarray(y, float), np.asarray(cur, float)
    ordinary = (y >= 0.5 * cur) & (y <= 2.0 * cur)
    return {"all": np.ones(len(y), bool), "ordinary": ordinary, "extreme": ~ordinary,
            "changed_nonspike": ordinary & strict_changed(y, cur, threshold), "exact_unchanged": y == cur}


def validate_horizon_support(cfg: dict[str, Any], horizons: list[int]) -> None:
    """Horizon chua co tham so da chot cho control (RF-L2 chi co h1/h3 tu report v2) => tu choi truoc khi chay (GPT file 16 MIN1), khong am tham dung bo tham so cua horizon khac."""
    for name, spec in cfg["controls"].items():
        by = spec.get("params_by_horizon")
        if by is None:
            continue
        missing = [h for h in horizons if str(h) not in by]
        if missing:
            raise ConfigError(f"controls.{name}.params_by_horizon chua co tham so da chot cho horizon {missing} (chi co {sorted(by)}); chua duoc ho tro o train-v3.0.0.")
