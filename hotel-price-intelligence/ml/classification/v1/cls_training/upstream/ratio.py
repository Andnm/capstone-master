# BAN COPY NGUYEN VAN (khong sua tay) tu `ml/regression/training/v3_contract.py`.
# SHA-256 (chuan hoa LF) cua nguon luc copy: 224c653c2bc282bbc98df9d4993877978d95455eea6f6d2fc432628c19cd0c62
# Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y.
# Pham vi: STABLE_SORT, DATE_COLUMNS, RATIO_BLOCK_COLUMNS, RATIO_BLOCK_DEFINITION, RATIO_BLOCK_SHA256, stable_sort, add_ratio_block.
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd


STABLE_SORT = ["vn_observation_date", "hotel_id", "checkin_date", "canonical_series_id", "warehouse_record_id"]


DATE_COLUMNS = ("checkin_date", "vn_observation_date")


RATIO_BLOCK_COLUMNS = ("r_mean14", "r_max14", "r_min14", "log_current", "cv7", "abs_velocity")


RATIO_BLOCK_DEFINITION = (
    "r_mean14=current/price_rolling_mean_14 [mean>0]; r_max14=current/price_max_trailing_14 [den>0]; r_min14=current/price_min_trailing_14 [den>0]; "
    "log_current=ln(current) [current>0]; cv7=price_rolling_std_7/price_rolling_mean_7 [mean>0, std>=0]; abs_velocity=|price_velocity|; "
    "non-finite or non-positive denominator => NaN")


RATIO_BLOCK_SHA256 = hashlib.sha256(RATIO_BLOCK_DEFINITION.encode("utf-8")).hexdigest()


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
