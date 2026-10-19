"""Chon bien split va gan `ml_samples.split` (spec muc 15, E7).

Quy tac CHON bien xac dinh, KHONG nhin metric mo hinh. Ung vien horizon H = 14, 7, 3, 1 (lon -> nho): voi moi H dung cua so lich theo gate da dang ky
(val va test moi cai dai `gate_dates(H) + H` ngay, test la cua so CUOI, hai vung purge, train la phan con lai), roi **do tren MAU THAT** cac gate cua chinh H do
(`sufficiency.evaluate_horizon`: ngay eligible, mau co nhan, so hotel tong/theo thanh pho cua tap primary). Chon H lon nhat pass TOAN BO gate cua H;
khong H nao pass (hoac chuoi lich qua ngan) -> fallback ty le 60/20/20 (sau khi tru hai purge), `policy_path='fallback_ratio'`, `feasible_horizon=None`.
Chi mot cap bien (`split_train_end`, `split_validation_end`) cho ca 4 horizon (manifest chi co mot cap); bao cao sufficiency van chay doc lap tung horizon
sau export va PHAI nhat quan voi ly do chon bien (kiem o buoc validation). (GPT review vong 1 DB-M1: truoc day chi nhin khoang ngay, khong nhin coverage.)

Cong thuc spec muc 15 (ngay lich VN):
    validation_start = split_train_end + P + 1
    test_start       = split_validation_end + P + 1
    train: d <= split_train_end;  purge_1: split_train_end < d <= split_train_end + P   -> split NULL
    validation: validation_start <= d <= split_validation_end;  purge_2: ... + P        -> split NULL
    test: d >= test_start
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from . import env  # noqa: F401
from .db import execute, fetch_all
from .sufficiency import candidate_frame, evaluate_horizon, gate_for


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
    candidates: tuple = field(default=(), compare=False)   # audit: ket qua danh gia tung H da xet

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

    def split_series(self, dates: pd.Series) -> pd.Series:
        """Phien ban vector hoa cua `split_of` (cung quy tac), tra Series object ('train'/'validation'/'test'/None)."""
        d = pd.to_datetime(dates)
        out = pd.Series([None] * len(d), index=d.index, dtype=object)
        out[d <= pd.Timestamp(self.train_end)] = "train"
        out[(d >= pd.Timestamp(self.validation_start)) & (d <= pd.Timestamp(self.validation_end))] = "validation"
        out[d >= pd.Timestamp(self.test_start)] = "test"
        return out

    def as_report(self) -> dict[str, Any]:
        report = {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in self.__dict__.items() if k != "candidates"}
        report["candidates"] = list(self.candidates)
        return report


def required_windows(policy: dict[str, Any], horizon: int) -> tuple[int, int, int]:
    """(train_days, val_days, test_days) toi thieu de co du ngay du bao eligible cua `horizon` trong tung cua so."""
    dates = gate_for(policy["gates"], horizon)["eligible_prediction_dates"]
    return dates["train"] + horizon, dates["validation"] + horizon, dates["test"] + horizon


Evaluate = Callable[[SplitPlan, int], dict[str, Any]]


def plan_split(first_day: dt.date, last_day: dt.date, *, policy: dict[str, Any], purge_gap_days: int, evaluate: Evaluate) -> SplitPlan:
    """`evaluate(plan, horizon)` PHAI tra ket qua `sufficiency.evaluate_horizon` tren mau thuc (bat buoc: khong con duong chi dua vao khoang lich)."""
    span = (last_day - first_day).days + 1
    purge = int(purge_gap_days)
    audit: list[dict[str, Any]] = []
    for horizon in policy["horizon_candidates_desc"]:
        train_len, val_len, test_len = required_windows(policy, horizon)
        needed = train_len + purge + val_len + purge + test_len
        if span < needed:
            audit.append({"horizon": horizon, "calendar_feasible": False, "needed_days": needed, "span_days": span, "calendar_days_short": needed - span,
                          "gate_pass": None})
            continue
        test_start = last_day - dt.timedelta(days=test_len - 1)
        validation_end = test_start - dt.timedelta(days=purge + 1)
        validation_start = validation_end - dt.timedelta(days=val_len - 1)
        train_end = validation_start - dt.timedelta(days=purge + 1)
        candidate = SplitPlan(first_day, train_end, validation_start, validation_end, test_start, last_day, purge,
                              f"gate_driven:H={horizon}", horizon)
        result = evaluate(candidate, horizon)
        passed = result["status"] == "primary_eligible"
        audit.append({"horizon": horizon, "calendar_feasible": True, "needed_days": needed, "span_days": span, "calendar_days_short": 0,
                      "gate_pass": passed, "failed_gates": result["failed_gates"][:6], "shortfall": result.get("shortfall"),
                      "splits": result["splits"]})   # `splits` = so do tren mau that, doi chieu o validation
        if passed:
            return SplitPlan(**{**candidate.__dict__, "candidates": tuple(audit)})
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
    return SplitPlan(first_day, train_end, validation_start, validation_end, test_start, last_day, purge, "fallback_ratio", None, tuple(audit))


_SAMPLES_SQL = """
SELECT s.vn_observation_date, a.hotel_id, h.city, a.canonical_series_id,
       (s.label_source_record_id_h1 IS NOT NULL) AS has_label_h1, (s.label_source_record_id_h3 IS NOT NULL) AS has_label_h3,
       (s.label_source_record_id_h7 IS NOT NULL) AS has_label_h7, (s.label_source_record_id_h14 IS NOT NULL) AS has_label_h14
FROM ml_samples s
JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version
JOIN hotels h ON h.hotel_id = a.hotel_id
WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = TRUE
"""


def load_selected_samples(conn, dataset_version: str) -> pd.DataFrame:
    rows = fetch_all(conn, _SAMPLES_SQL, (dataset_version,))
    conn.commit()
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise SplitInfeasible("khong co sample duoc chon nao - khong chia split duoc.")
    frame["vn_observation_date"] = pd.to_datetime(frame["vn_observation_date"])
    for k in (1, 3, 7, 14):
        frame[f"has_label_h{k}"] = frame[f"has_label_h{k}"].astype(bool)
    return frame


def build_split(conn, *, dataset_version: str, config: dict[str, Any]) -> dict[str, Any]:
    """Step `split`: CHI chay sau khi biet coverage that (spec muc 3b buoc 5); chon bien bang gate do tren mau that."""
    samples = load_selected_samples(conn, dataset_version)
    gates = config["split_selection_policy"]["gates"]

    def evaluate(plan: SplitPlan, horizon: int) -> dict[str, Any]:
        return evaluate_horizon(candidate_frame(samples, plan, horizon), horizon, gate_for(gates, horizon))

    first_day, last_day = samples["vn_observation_date"].min().date(), samples["vn_observation_date"].max().date()
    distinct_days = int(samples["vn_observation_date"].nunique())
    plan = plan_split(first_day, last_day, policy=config["split_selection_policy"], purge_gap_days=config["purge_gap_days"], evaluate=evaluate)
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
    return {"plan": plan.as_report(), "span_days": (last_day - first_day).days + 1, "distinct_days": distinct_days,
            "selected_samples_by_split": counts,
            "sufficiency": "none" if plan.feasible_horizon is None else f"gate_pass:H={plan.feasible_horizon}"}
