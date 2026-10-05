"""Chon bien split va gan `ml_samples.split` (spec muc 15, E7).

Quy tac CHON bien xac dinh, chi dung do dai/coverage cua chuoi ngay quan sat (khong nhin metric mo hinh):
tim horizon H lon nhat (14, 7, 3, 1) ma chuoi du dai de cac cong da dang ky (ngay du bao eligible train/val/test)
dat duoc voi hai vung purge; khi do val va test moi cai dai `gate_dates(H) + H` ngay (test la cua so CUOI), train la phan
con lai. Mot cap bien (`split_train_end`, `split_validation_end`) cho ca 4 horizon (manifest chi co mot cap); bao cao
sufficiency tinh rieng tung horizon. Khong H nao kha thi -> fallback ty le (60/20/20 tren chuoi sau khi tru hai purge) va
danh dau `sufficiency=none`.

Cong thuc spec muc 15 (ngay lich VN):
    validation_start = split_train_end + P + 1
    test_start       = split_validation_end + P + 1
    train: d <= split_train_end;  purge_1: split_train_end < d <= split_train_end + P   -> split NULL
    validation: validation_start <= d <= split_validation_end;  purge_2: ... + P        -> split NULL
    test: d >= test_start
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from . import env  # noqa: F401
from .db import execute, fetch_all


class SplitInfeasible(RuntimeError):
    pass


@dataclass(frozen=True)
class SplitPlan:
    train_start: dt.date
    train_end: dt.date
    validation_start: dt.date
    validation_end: dt.date
    test_start: dt.date
    test_end: dt.date
    purge_gap_days: int
    policy_path: str                  # "gate_driven:H=<k>" | "fallback_ratio"
    feasible_horizon: int | None

    def split_of(self, day: dt.date) -> str | None:
        if day <= self.train_end:
            return "train"
        if day < self.validation_start:
            return None
        if day <= self.validation_end:
            return "validation"
        if day < self.test_start:
            return None
        return "test"

    def as_report(self) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in self.__dict__.items()}


def _gate_for(policy: dict[str, Any], horizon: int) -> dict[str, dict[str, int]]:
    gates = policy["gates"]
    return gates["h14"] if horizon == 14 else gates["h1_h3_h7"]


def required_windows(policy: dict[str, Any], horizon: int) -> tuple[int, int, int]:
    """(train_days, val_days, test_days) toi thieu de co du ngay du bao eligible cua `horizon` trong tung cua so."""
    dates = _gate_for(policy, horizon)["eligible_prediction_dates"]
    return dates["train"] + horizon, dates["validation"] + horizon, dates["test"] + horizon


def plan_split(first_day: dt.date, last_day: dt.date, *, policy: dict[str, Any], purge_gap_days: int) -> SplitPlan:
    span = (last_day - first_day).days + 1
    purge = int(purge_gap_days)
    for horizon in policy["horizon_candidates_desc"]:
        train_len, val_len, test_len = required_windows(policy, horizon)
        if span >= train_len + purge + val_len + purge + test_len:
            test_start = last_day - dt.timedelta(days=test_len - 1)
            validation_end = test_start - dt.timedelta(days=purge + 1)
            validation_start = validation_end - dt.timedelta(days=val_len - 1)
            train_end = validation_start - dt.timedelta(days=purge + 1)
            return SplitPlan(first_day, train_end, validation_start, validation_end, test_start, last_day, purge,
                             f"gate_driven:H={horizon}", horizon)
    ratios = policy["fallback_when_infeasible"]
    usable = span - 2 * purge
    if usable < 3:
        raise SplitInfeasible(
            f"chuoi mau chi {span} ngay ({first_day}..{last_day}); sau hai purge {purge} ngay con {usable} < 3 ngay - "
            f"khong tao duoc train/validation/test khong rong.")
    validation_len = max(1, int(usable * ratios["validation"]))
    test_len = max(1, int(usable * ratios["test"]))
    train_len = usable - validation_len - test_len
    if train_len < 1:
        raise SplitInfeasible(f"fallback ty le khong du ngay: usable={usable}")
    train_end = first_day + dt.timedelta(days=train_len - 1)
    validation_start = train_end + dt.timedelta(days=purge + 1)
    validation_end = validation_start + dt.timedelta(days=validation_len - 1)
    test_start = validation_end + dt.timedelta(days=purge + 1)
    return SplitPlan(first_day, train_end, validation_start, validation_end, test_start, last_day, purge, "fallback_ratio", None)


def build_split(conn, *, dataset_version: str, config: dict[str, Any]) -> dict[str, Any]:
    """Step `split`: CHI chay sau khi biet coverage that (spec muc 3b buoc 5)."""
    bounds = fetch_all(conn, "SELECT MIN(vn_observation_date) lo, MAX(vn_observation_date) hi, COUNT(DISTINCT vn_observation_date) days, "
                             "COUNT(*) n FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE", (dataset_version,))[0]
    if not bounds["n"]:
        raise SplitInfeasible("khong co sample duoc chon nao - khong chia split duoc.")
    plan = plan_split(bounds["lo"], bounds["hi"], policy=config["split_selection_policy"], purge_gap_days=config["purge_gap_days"])
    try:
        execute(conn, "UPDATE ml_samples SET split=NULL WHERE dataset_version=%s", (dataset_version,))
        execute(conn, """UPDATE ml_samples SET split = CASE
                           WHEN vn_observation_date <= %s THEN 'train'
                           WHEN vn_observation_date >= %s AND vn_observation_date <= %s THEN 'validation'
                           WHEN vn_observation_date >= %s THEN 'test'
                           ELSE NULL END
                         WHERE dataset_version = %s""",
                (plan.train_end, plan.validation_start, plan.validation_end, plan.test_start, dataset_version))
        execute(conn, "UPDATE dataset_build_manifests SET split_train_end=%s, split_validation_end=%s WHERE dataset_version=%s",
                (plan.train_end, plan.validation_end, dataset_version))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    counts = {r["split"] or "purge": r["n"] for r in fetch_all(
        conn, "SELECT split, COUNT(*) n FROM ml_samples WHERE dataset_version=%s AND is_daily_snapshot_selected=TRUE GROUP BY split",
        (dataset_version,))}
    conn.commit()
    empty = [name for name in ("train", "validation", "test") if not counts.get(name)]
    if empty:
        raise SplitInfeasible(f"split rong sau khi chia: {empty} (dem {counts}) - sua cau hinh/du lieu, khong ep PASS.")
    return {"plan": plan.as_report(), "span_days": (bounds["hi"] - bounds["lo"]).days + 1, "distinct_days": bounds["days"],
            "selected_samples_by_split": counts, "sufficiency": "none" if plan.feasible_horizon is None else f"H<={plan.feasible_horizon}"}
