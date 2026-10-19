"""Metric v3 (C1/C13): lift so voi persistence tren hai thang, macro theo khach san, strata ex-post, bootstrap THEO CUM KHACH SAN.

`EvalSet` giu mot split (tap hang) + quan the hotel CUA CA SPLIT va draw bootstrap DUNG CHUNG cho moi candidate (paired; hotel vang mat trong mot mask dong gop 0; mau so khong xac dinh duoc dem,
khong gan 0). Metric khong xac dinh (mau so persistence = 0) => `None` + ly do, khong bao gio 0 (GPT file 10 MIN4).
Cong thuc da duoc doi chieu doc lap (GPT: tolerance 1e-9 tren E2-E7).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .v3_contract import InvalidPredictionError, price_from_z, strata_masks


def _finite(array: np.ndarray, label: str) -> np.ndarray:
    array = np.asarray(array, float)
    if not np.isfinite(array).all():
        raise InvalidPredictionError(f"du doan khong huu han ({label}): {int((~np.isfinite(array)).sum())} gia tri")
    return array


class EvalSet:
    def __init__(self, frame: pd.DataFrame, *, n_boot: int = 2000, seed: int = 20261008, stable_threshold: float = 0.02):
        self.frame = frame.reset_index(drop=True)
        self.y = self.frame["y_true"].to_numpy(float)
        self.cur = self.frame["current_price"].to_numpy(float)
        if not (np.isfinite(self.y).all() and np.isfinite(self.cur).all() and (self.y > 0).all() and (self.cur > 0).all()):
            raise InvalidPredictionError("EvalSet can y_true va current_price huu han va > 0 (horizon_frames da loc)")
        self.z = np.log(self.y / self.cur)
        self.codes, self.hotels = pd.factorize(self.frame["hotel_id"], sort=True)
        self.nh = int(len(self.hotels))
        self.ep = np.abs(self.cur - self.y)
        self.masks = strata_masks(self.y, self.cur, stable_threshold)
        self.n_boot = int(n_boot)
        rng = np.random.default_rng(int(seed))
        self.idx = rng.integers(0, self.nh, size=(self.n_boot, self.nh))
        self.dates = self.frame["vn_observation_date"].astype(str).to_numpy()

    # ----- co ban
    def price(self, zhat: np.ndarray) -> np.ndarray:
        """Nghich dao muc tieu qua MOT helper dung chung: z huu han, gia ket qua huu han va > 0 (khong clip), nguoc lai InvalidPredictionError."""
        return price_from_z(self.cur, zhat, label="EvalSet.price")

    def hotel_sum(self, values: np.ndarray) -> np.ndarray:
        return np.bincount(self.codes, weights=values, minlength=self.nh)

    def hotel_mean_log_loss(self, zhat: np.ndarray) -> np.ndarray:
        cnt = np.bincount(self.codes, minlength=self.nh)
        return self.hotel_sum(np.abs(self.z - zhat)) / cnt

    def lifts(self, zhat: np.ndarray, mask: np.ndarray | None = None) -> dict[str, float | None]:
        zhat = _finite(zhat, "lifts")
        m = np.ones(len(self.y), bool) if mask is None else mask
        em = np.abs(self.price(zhat) - self.y)
        den_v = self.ep[m].sum()
        den_l = np.abs(self.z[m]).sum()
        return {"lift_vnd": float(1 - em[m].sum() / den_v) if den_v > 0 else None,
                "lift_log": float(1 - np.abs(self.z - zhat)[m].sum() / den_l) if den_l > 0 else None}

    def _boot_ratio(self, numerator_h: np.ndarray, denominator_h: np.ndarray) -> dict[str, Any]:
        num, den = numerator_h[self.idx].sum(1), denominator_h[self.idx].sum(1)
        ok = den > 0
        boot = num[ok] / den[ok]
        return {"ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))] if len(boot) else None,
                "undefined_bootstrap_denominators": int((~ok).sum())}

    def summary(self, zhat: np.ndarray, *, with_ci: bool = True) -> dict[str, Any]:
        zhat = _finite(zhat, "summary")
        em = np.abs(self.price(zhat) - self.y)
        gain = self.ep - em
        out: dict[str, Any] = {"n": int(len(self.y)), "n_hotels": self.nh, **self.lifts(zhat),
                               "macro_log_mae": float(self.hotel_mean_log_loss(zhat).mean()),
                               "mae_vnd": float(em.mean()), "persistence_mae_vnd": float(self.ep.mean()),
                               "mae_log": float(np.abs(self.z - zhat).mean()), "persistence_mae_log": float(np.abs(self.z).mean()),
                               "median_delta_row": float(np.median(em - self.ep)),
                               "share_better": float(np.mean(em < self.ep)), "share_worse": float(np.mean(em > self.ep)), "share_tie": float(np.mean(em == self.ep)),
                               "identical_to_persistence": bool(np.all(zhat == 0))}
        if with_ci:
            ci = self._boot_ratio(self.hotel_sum(gain), self.hotel_sum(self.ep))
            out["lift_vnd_ci95_hotel"] = ci["ci95"]
            out["undefined_bootstrap_denominators"] = ci["undefined_bootstrap_denominators"]
        return out

    def strata(self, zhat: np.ndarray) -> dict[str, Any]:
        zhat = _finite(zhat, "strata")
        em = np.abs(self.price(zhat) - self.y)
        gain = self.ep - em
        result: dict[str, Any] = {}
        for name, mask in self.masks.items():
            if not mask.any():
                result[name] = {"n": 0}
                continue
            den = self.ep[mask].sum()
            entry: dict[str, Any] = {"n": int(mask.sum()), "share_of_rows": float(mask.mean()),
                                     "lift_vnd": float(gain[mask].sum() / den) if den > 0 else None,
                                     "lift_vnd_undefined_reason": None if den > 0 else "persistence_abs_error_sum_is_zero",
                                     "median_delta_row": float(np.median((em - self.ep)[mask])),
                                     "share_better": float(np.mean(em[mask] < self.ep[mask])), "share_worse": float(np.mean(em[mask] > self.ep[mask])),
                                     "share_tie": float(np.mean(em[mask] == self.ep[mask]))}
            ci = self._boot_ratio(self.hotel_sum(np.where(mask, gain, 0.0)), self.hotel_sum(np.where(mask, self.ep, 0.0)))
            entry["lift_vnd_ci95_hotel"], entry["undefined_bootstrap_denominators"] = ci["ci95"], ci["undefined_bootstrap_denominators"]
            result[name] = entry
        return result

    def mask_lift(self, zhat: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
        """Lift VND tren mot mask (mode/ngay/...) voi CI theo quan the hotel CUA CA split (hotel vang mat dong gop 0)."""
        zhat = _finite(zhat, "mask_lift")
        em = np.abs(self.price(zhat) - self.y)
        gain = self.ep - em
        den = self.ep[mask].sum()
        ci = self._boot_ratio(self.hotel_sum(np.where(mask, gain, 0.0)), self.hotel_sum(np.where(mask, self.ep, 0.0)))
        return {"n": int(mask.sum()), "n_hotels_active": int(len(np.unique(self.codes[mask]))) if mask.any() else 0,
                "n_dates": int(len(set(self.dates[mask]))), "lift_vnd": float(gain[mask].sum() / den) if den > 0 else None,
                "ci95_hotel_full_split_population": ci["ci95"], "undefined_bootstrap_denominators": ci["undefined_bootstrap_denominators"]}

    def incr_lift(self, z_base: np.ndarray, z_new: np.ndarray) -> dict[str, Any]:
        """(sum|base-y| - sum|new-y|)/sum|cur-y| + CI theo hotel: loi the cua `new` so voi `base` (KHONG phai lift so voi persistence)."""
        eb = np.abs(self.price(_finite(z_base, "base")) - self.y)
        en = np.abs(self.price(_finite(z_new, "new")) - self.y)
        d = eb - en
        den = self.ep.sum()
        ci = self._boot_ratio(self.hotel_sum(d), self.hotel_sum(self.ep))
        return {"incr_lift_vnd": float(d.sum() / den) if den > 0 else None, "ci95_hotel": ci["ci95"],
                "undefined_bootstrap_denominators": ci["undefined_bootstrap_denominators"]}

    def macro_diff(self, z_a: np.ndarray, z_b: np.ndarray) -> dict[str, Any]:
        """macro_log_mae(a) - macro_log_mae(b) theo hotel (> 0: b tot hon); bootstrap hotel."""
        d = self.hotel_mean_log_loss(_finite(z_a, "a")) - self.hotel_mean_log_loss(_finite(z_b, "b"))
        boot = d[self.idx].mean(1)
        return {"diff": float(d.mean()), "ci95_hotel": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))]}

    def breakdown(self, zhat: np.ndarray, column: str) -> list[dict[str, Any]]:
        """Bang theo mot cot nhom (ngay quan sat, mode, lead_time_bucket, city): mask_lift cho moi nhom (CI theo quan the hotel cua ca split)."""
        rows = []
        values = self.frame[column].astype(str).to_numpy()
        for key in sorted(set(values)):
            mask = values == key
            rows.append({column: key, **self.mask_lift(zhat, mask)})
        return rows

    def exact_unchanged_bias(self, zhat: np.ndarray) -> float | None:
        mask = self.masks["exact_unchanged"]
        return float(np.median(self.price(zhat)[mask] / self.cur[mask] - 1)) if mask.any() else None


def is_valid(value: Any) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(float(value))
