"""Wave B - Curated ML EDA (EDA_CURATED_PLAN.md muc 8, GPT file 52 muc 1): tinh toan thuan pandas tren cac frame da nap, de test duoc bang fixture nho.

Nguyen tac:
  * Moi ham `*_table` nhan DataFrame, tra DataFrame; KHONG doc DB. Nap du lieu (SQL qua `wave_b_queries`, Parquet) nam o `load_data`.
  * Mau so TACH BAT: moi bang ghi ro `n_*` cua tung mau so; mau so 0 => ty le NaN + `rate_status='undefined_zero_denominator'` (khong bao gio 0%/100% tuy y).
  * Bucket lead time nua mo `[0,3) [3,7) [7,14) [14,30) [30,60) [60,inf)`; nhan trung nhan cua builder (lt3, 3-7, 7-14, 14-30, 30-60, gt60).
  * Gio: `observed_at`/`approved_at`/`run_*_at` la UTC naive; ngay VN = +7h co dinh (`timezone.py`). Ngay quan sat cua mau da la `vn_observation_date`.
  * Review-score tier chi la phan khuc POST-HOC tren snapshot thuoc tinh luc build warehouse (khong as-of, khong la feature).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import db
import timezone
import wave_b_queries as wbq
from wave_b_inputs import WaveBInputs

HORIZONS = (1, 3, 7, 14)
LEAD_BINS = [-math.inf, 0, 3, 7, 14, 30, 60, math.inf]
LEAD_LABELS = ["negative", "lt3", "3-7", "7-14", "14-30", "30-60", "gt60"]
REVIEW_TIERS = [(-math.inf, 8.0, "<8.0"), (8.0, 9.0, "8.0-8.9"), (9.0, math.inf, ">=9.0")]
ANALYSIS_VERSION = "wave-b-1.1.0"      # 1.1.0: match/lead theo event time observation (GPT file 56 W55-M1), recheck usable theo tung record (W55-M2)
OBSERVATION_PHASES = ("pre_approval", "post_approval")
APPROVAL_RUN_PHASES = ("pre_approval_run", "approving_run", "post_approval_run", "same_finish_other_run")
MATCH_STATUSES = ("exact", "alias", "unavailable", "ambiguous")
LAG_DAYS = (1, 3, 7, 14)
IDENTIFIER_GROUP, LABEL_GROUP = "identifier", "label"


@dataclass
class WaveBData:
    inputs: WaveBInputs
    preflight: dict[str, Any]
    samples: pd.DataFrame
    ml_samples: pd.DataFrame
    assignments: pd.DataFrame
    first_evidence: pd.DataFrame
    matches: pd.DataFrame
    hotels: pd.DataFrame
    dictionary: pd.DataFrame | None
    evaluation_horizons: list[int]


@dataclass
class Table:
    table_id: str
    frame: pd.DataFrame
    bullets: tuple[int, ...]
    grain: str
    denominator: str
    note: str = ""


# ------------------------------------------------------------------------------------------------ helpers
def rate(numerator: Any, denominator: Any) -> float:
    """numerator/denominator; mau so 0 hoac NaN => NaN (kem `rate_status` o noi goi), khong chia 0 thanh 0%/100%."""
    if denominator is None or (isinstance(denominator, float) and math.isnan(denominator)) or denominator == 0:
        return float("nan")
    return float(numerator) / float(denominator)


def with_rate(frame: pd.DataFrame, column: str, numerator: str, denominator: str) -> pd.DataFrame:
    frame = frame.copy()
    frame[column] = [rate(n, d) for n, d in zip(frame[numerator], frame[denominator])]
    frame[f"{column}_status"] = np.where(frame[denominator].fillna(0) > 0, "defined", "undefined_zero_denominator")
    return frame


def lead_bucket(lead_days: pd.Series) -> pd.Series:
    return pd.cut(lead_days, bins=LEAD_BINS, labels=LEAD_LABELS, right=False).astype("object")


def split_label(split: pd.Series) -> pd.Series:
    return split.where(split.notna(), "purge").astype("object")


def sanitize(value: Any) -> Any:
    """NaN/inf -> None de ghi JSON strict (`allow_nan=False`); numpy -> python."""
    if value is pd.NaT or value is pd.NA:                                   # NaT la instance cua datetime => phai kiem truoc nhanh datetime
        return None
    if isinstance(value, dict):
        return {str(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if (math.isnan(float(value)) or math.isinf(float(value))) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series)


def prepare_samples(samples: pd.DataFrame) -> pd.DataFrame:
    frame = samples.copy()
    frame["vn_observation_date"] = _dates(frame["vn_observation_date"])
    frame["checkin_date"] = _dates(frame["checkin_date"])
    frame["split_label"] = split_label(frame["split"])
    frame["lead_bucket"] = lead_bucket(frame["lead_time"] if "lead_time" in frame.columns else (frame["checkin_date"] - frame["vn_observation_date"]).dt.days)
    return frame


# ------------------------------------------------------------------------------------------------ nap du lieu
def load_data(conn, inputs: WaveBInputs, preflight: dict[str, Any]) -> WaveBData:
    dv, batch = inputs.dataset_version, inputs.batch_id
    samples = pd.read_parquet(inputs.dataset_dir / "samples.parquet")
    dictionary_path = inputs.dataset_dir / "data_dictionary.csv"
    matches = wbq.run_query(conn, "item_matches", batch, dv, dv)
    for column in ("run_started_at", "run_finished_at", "approved_at", "event_utc", "selected_observed_at", "item_obs_min", "item_obs_max"):
        matches[column] = pd.to_datetime(matches[column])
    assignments = wbq.run_query(conn, "assignments", dv)
    assignments["approved_at"] = pd.to_datetime(assignments["approved_at"])
    db._ensure_backend_importable()
    from app.warehouse.canonicalize import EMPTY_ROOM_KEY                               # CUNG hang so voi causal builder, khong magic constant rieng
    first = wbq.run_query(conn, "first_reference_evidence", batch, batch, dv, EMPTY_ROOM_KEY)
    first["first_evidence_run_finished_at"] = pd.to_datetime(first["first_evidence_run_finished_at"])
    ml = wbq.run_query(conn, "ml_samples", dv)
    ml["vn_observation_date"] = pd.to_datetime(ml["vn_observation_date"])
    hotels = wbq.run_query(conn, "hotels_snapshot", dv)
    horizons = preflight["artifact_generation"].get("evaluation_horizons") or list(HORIZONS)
    return WaveBData(inputs, preflight, samples, ml, assignments, first, matches, hotels,
                     pd.read_csv(dictionary_path) if dictionary_path.exists() else None, [int(h) for h in horizons])


# ------------------------------------------------------------------------------------------------ 1. assignment + time to approval
def assignment_summary_table(assignments: pd.DataFrame) -> pd.DataFrame:
    frame = assignments.copy()
    frame["approved_vn_date"] = timezone.to_vn_date_series(frame["approved_at"])
    def agg(group: pd.DataFrame, label: str) -> dict[str, Any]:
        return {"city": label, "assignments": int(len(group)), "hotels": int(group["hotel_id"].nunique()), "checkin_dates": int(group["checkin_date"].nunique()),
                "first_approved_vn_date": min(group["approved_vn_date"]) if len(group) else None, "last_approved_vn_date": max(group["approved_vn_date"]) if len(group) else None,
                "median_evidence_run_count": float(group["evidence_run_count"].median()) if len(group) else float("nan"),
                "mean_coverage": float(group["coverage"].astype(float).mean()) if len(group) else float("nan")}
    rows = [agg(group, city) for city, group in frame.groupby("city", sort=True)] + [agg(frame, "ALL")]
    return pd.DataFrame(rows)


TIME_TO_APPROVAL_BINS = [0, 1, 2, 3, 5, 8, 15, math.inf]
TIME_TO_APPROVAL_LABELS = ["[0,1)", "[1,2)", "[2,3)", "[3,5)", "[5,8)", "[8,15)", ">=15"]


def time_to_approval_frame(assignments: pd.DataFrame, first_evidence: pd.DataFrame) -> pd.DataFrame:
    """days = approved_at - run.finished_at dau tien chua bang chung reference (run completed + item success + include_reference): run-completion semantics, UTC."""
    merged = assignments.merge(first_evidence, on="assignment_id", how="left", validate="one_to_one")
    merged["days_to_approval"] = (merged["approved_at"] - merged["first_evidence_run_finished_at"]).dt.total_seconds() / 86400.0
    return merged


def time_to_approval_tables(assignments: pd.DataFrame, first_evidence: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    merged = time_to_approval_frame(assignments, first_evidence)
    def stats(group: pd.DataFrame, label: str) -> dict[str, Any]:
        d = group["days_to_approval"].dropna()
        return {"city": label, "n_assignments": int(len(group)), "n_with_first_evidence": int(len(d)), "n_missing_first_evidence": int(len(group) - len(d)),
                "mean_days": float(d.mean()) if len(d) else float("nan"), "median_days": float(d.median()) if len(d) else float("nan"),
                "p10_days": float(d.quantile(0.10)) if len(d) else float("nan"), "p90_days": float(d.quantile(0.90)) if len(d) else float("nan"),
                "max_days": float(d.max()) if len(d) else float("nan"), "n_negative_days": int((d < 0).sum())}
    summary = pd.DataFrame([stats(g, c) for c, g in merged.groupby("city", sort=True)] + [stats(merged, "ALL")])
    d = merged["days_to_approval"].dropna()
    binned = pd.cut(d, bins=TIME_TO_APPROVAL_BINS, labels=TIME_TO_APPROVAL_LABELS, right=False)
    distribution = binned.value_counts().reindex(TIME_TO_APPROVAL_LABELS, fill_value=0).rename_axis("bin").reset_index(name="n_assignments")
    distribution["share_of_with_first_evidence"] = [rate(n, len(d)) for n in distribution["n_assignments"]]
    return summary, distribution


# ------------------------------------------------------------------------------------------------ 2. match audit (pre/post approval)
def prepare_matches(matches: pd.DataFrame) -> pd.DataFrame:
    """Thoi diem cua item = ACTUAL observation time (khong phai luc run bat dau): exact/alias -> observed_at cua selected record; unavailable/ambiguous -> MIN(observed_at) cua item.
    `observation_phase` (chieu CHINH, noi voi sample funnel): post_approval <=> event_utc >= approved_at (cung dieu kien `observed_at >= approved_at` cua builder).
    `approval_run_phase` la audit vong doi run (run_id == approving_run_warehouse_id, khong dung bang nhau timestamp); KHONG thay dieu kien sample."""
    frame = matches.copy()
    if frame["event_utc"].isna().any():
        raise ValueError(f"{int(frame['event_utc'].isna().sum())} item match khong co observation nao - khong xac dinh duoc thoi diem su kien.")
    frame["observation_phase"] = np.where(frame["event_utc"] >= frame["approved_at"], "post_approval", "pre_approval")
    frame["approval_run_phase"] = np.select([frame["run_id"] == frame["approving_run_warehouse_id"], frame["run_finished_at"] < frame["approved_at"],
                                             frame["run_finished_at"] > frame["approved_at"]], ["approving_run", "pre_approval_run", "post_approval_run"], "same_finish_other_run")
    event_vn = timezone.to_vn_date_series(frame["event_utc"])
    frame["item_lead_time"] = (pd.to_datetime(frame["checkin_date"]) - pd.to_datetime(event_vn)).dt.days
    frame["lead_bucket"] = lead_bucket(frame["item_lead_time"])
    frame["run_start_vn_date"] = pd.to_datetime(timezone.to_vn_date_series(frame["run_started_at"]))
    frame["event_vn_date"] = pd.to_datetime(event_vn)
    return frame


def event_time_audit_table(matches: pd.DataFrame) -> pd.DataFrame:
    """Do chenh giua thoi gian run va thoi gian observation tren CHINH dataset (de thay vi sao khong duoc dung run time): ngay VN khac, cong post khac, item nhieu event time, cung finished khac ID."""
    frame = matches
    post_by_run_finish = frame["run_finished_at"] > frame["approved_at"]
    post_by_event = frame["event_utc"] >= frame["approved_at"]
    multi = frame["item_obs_min"] != frame["item_obs_max"]
    row = {"n_items": int(len(frame)), "n_run_start_vn_date_differs_from_event_vn_date": int((frame["run_start_vn_date"] != frame["event_vn_date"]).sum()),
           "n_post_gate_differs_run_finish_vs_event": int((post_by_run_finish != post_by_event).sum()), "n_items_with_multiple_observed_at": int(multi.sum()),
           "n_equal_finish_other_run": int(((frame["run_finished_at"] == frame["approved_at"]) & (frame["run_id"] != frame["approving_run_warehouse_id"])).sum()),
           "n_unavailable_or_ambiguous_with_multiple_observed_at": int((multi & frame["match_status"].isin(["unavailable", "ambiguous"])).sum())}
    return pd.DataFrame([row])


def match_breakdown(matches: pd.DataFrame, by: str | None, phase_column: str = "observation_phase", phases: tuple[str, ...] = OBSERVATION_PHASES) -> pd.DataFrame:
    """Long format: phase x [nhom] x match_status; `n_group` = tong item cua (phase, nhom), `share` = n/n_group (mau so ro, khong gom hai giai doan). Cot phase ten `phase`."""
    matches = matches.assign(phase=matches[phase_column])
    keys = ["phase"] + ([by] if by else [])
    counts = matches.groupby(keys + ["match_status"], observed=True).size().rename("n_items").reset_index()
    totals = matches.groupby(keys, observed=True).size().rename("n_group").reset_index()
    out = counts.merge(totals, on=keys, how="left")
    out = with_rate(out, "share", "n_items", "n_group")
    out["phase"] = pd.Categorical(out["phase"], categories=list(phases), ordered=True)
    return out.sort_values(keys + ["match_status"]).reset_index(drop=True).astype({"phase": "object"})


# ------------------------------------------------------------------------------------------------ 3. daily snapshot
def snapshot_tables(ml_samples: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    reasons = (ml_samples.assign(daily_snapshot_reason=ml_samples["daily_snapshot_reason"].fillna("(null)"))
               .groupby(["is_daily_snapshot_selected", "daily_snapshot_reason"], dropna=False).size().rename("n_samples").reset_index())
    reasons["is_daily_snapshot_selected"] = reasons["is_daily_snapshot_selected"].astype(bool)
    reasons["share_of_all_candidates"] = [rate(n, len(ml_samples)) for n in reasons["n_samples"]]
    per_day = ml_samples.groupby(["assignment_id", "vn_observation_date"]).agg(candidates=("record_id", "size"), selected=("is_daily_snapshot_selected", "sum")).reset_index()
    dist = (per_day.assign(candidates_bucket=np.where(per_day["candidates"] >= 3, "3+", per_day["candidates"].astype(str)), selected=per_day["selected"].astype(int))
            .groupby(["candidates_bucket", "selected"]).size().rename("n_series_days").reset_index())
    dist["n_series_days_total"] = int(len(per_day))
    dist["is_exactly_one_selected"] = dist["selected"] == 1
    return reasons, dist


# ------------------------------------------------------------------------------------------------ 4. sample counts
def sample_count_tables(samples: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    n = len(samples)
    def counted(keys: list[str]) -> pd.DataFrame:
        out = samples.groupby(keys, observed=True).size().rename("n_samples").reset_index()
        out["share_of_selected"] = [rate(v, n) for v in out["n_samples"]]
        return out
    tables["sample_counts_by_split"] = counted(["split_label"])
    tables["sample_counts_by_mode"] = counted(["inference_mode"])
    tables["sample_counts_by_split_mode"] = counted(["split_label", "inference_mode"])
    tables["sample_counts_by_city"] = counted(["city"])
    tables["sample_counts_by_split_city"] = counted(["split_label", "city"])
    return tables


# ------------------------------------------------------------------------------------------------ 5. label rates (mau so tach bat)
def _calendar_possible(samples: pd.DataFrame, k: int, last_obs_date: pd.Timestamp) -> pd.Series:
    target_day = samples["vn_observation_date"] + pd.Timedelta(days=k)
    return (target_day <= last_obs_date) & (target_day <= samples["checkin_date"])


def label_rate_table(samples: pd.DataFrame, evaluation_horizons: list[int]) -> pd.DataFrame:
    last_obs = samples["vn_observation_date"].max()
    rows = []
    for k in HORIZONS:
        possible = _calendar_possible(samples, k, last_obs)
        frame = samples.assign(_possible=possible, _eligible=possible & samples["split"].notna(),
                               _primary=samples[f"label_usable_h{k}"] & ((samples["split"] == "train") | samples[f"hotel_seen_in_train_h{k}"]))
        for label, group in [*frame.groupby("split_label", sort=True), ("ALL", frame)]:
            rows.append({"split": label, "horizon": k, "evaluated_in_build": bool(k in evaluation_horizons), "n_selected": int(len(group)),
                         "n_calendar_possible": int(group["_possible"].sum()), "n_split_assigned": int(group["split"].notna().sum()),
                         "n_source_eligible": int(group["_eligible"].sum()), "n_has_label": int(group[f"has_label_h{k}"].sum()),
                         "n_usable": int(group[f"label_usable_h{k}"].sum()), "n_primary_seen_hotel": int(group["_primary"].sum())})
    frame = pd.DataFrame(rows)
    frame = with_rate(frame, "has_label_rate_of_calendar_possible", "n_has_label", "n_calendar_possible")
    frame = with_rate(frame, "usable_rate_of_source_eligible", "n_usable", "n_source_eligible")
    frame = with_rate(frame, "usable_share_of_has_label", "n_usable", "n_has_label")
    frame = with_rate(frame, "primary_share_of_usable", "n_primary_seen_hotel", "n_usable")
    order = {"train": 0, "validation": 1, "test": 2, "purge": 3, "ALL": 4}
    return frame.assign(_o=frame["split"].map(order)).sort_values(["horizon", "_o"]).drop(columns="_o").reset_index(drop=True)


# ------------------------------------------------------------------------------------------------ 6. target cung assignment / dung ngay / cung split
def label_integrity_table(ml_samples: pd.DataFrame) -> pd.DataFrame:
    base = ml_samples.set_index("record_id")
    rows = []
    for k in HORIZONS:
        source = ml_samples[ml_samples[f"has_label_h{k}"].astype(bool)]
        target_ids = source[f"label_source_record_id_h{k}"].astype("int64")
        target = base.reindex(target_ids.to_numpy())
        missing = target["assignment_id"].isna().to_numpy()
        src_assignment, src_date, src_split = source["assignment_id"].to_numpy(), source["vn_observation_date"].to_numpy(), source["split"].to_numpy()
        tgt_selected = target["is_daily_snapshot_selected"].fillna(False).astype(bool).to_numpy()
        same_assignment = target["assignment_id"].to_numpy() == src_assignment
        right_date = target["vn_observation_date"].to_numpy() == (src_date + np.timedelta64(k, "D"))
        tgt_split = target["split"].to_numpy()
        usable = np.array([a is not None and not pd.isna(a) and a == b for a, b in zip(src_split, tgt_split)], dtype=bool)
        rows.append({"horizon": k, "n_with_label": int(len(source)), "n_target_missing": int(missing.sum()), "n_target_not_selected": int((~missing & ~tgt_selected).sum()),
                     "n_cross_assignment": int((~missing & ~same_assignment).sum()), "n_wrong_target_date": int((~missing & ~right_date).sum()),
                     "n_cross_split_info_only": int((~usable).sum()), "n_usable_by_recompute": int(usable.sum())})
    out = pd.DataFrame(rows)
    out["violations_total"] = out[["n_target_missing", "n_target_not_selected", "n_cross_assignment", "n_wrong_target_date"]].sum(axis=1)
    return out


def label_recheck_table(samples: pd.DataFrame, ml_samples: pd.DataFrame) -> pd.DataFrame:
    """Tai tinh label usability cho TUNG dong Parquet (GPT file 56 W55-M2): target qua `label_source_record_id_hK` cua ml_samples, phai la mau da chon, cung assignment, ngay d+K;
    usable <=> co nhan hop le + source co split + target cung split. So sanh per-record (hoan vi co giu nguyen tong van bi bat) + khoa record-set va split/date/has_label giua Parquet-DB,
    + tai tinh hotel_seen_in_train_hK tu mau train usable."""
    selected = ml_samples[ml_samples["is_daily_snapshot_selected"].astype(bool)]
    db = selected.set_index("record_id")
    ids = samples["warehouse_record_id"].astype("int64")
    missing, extra, duplicated = int((~ids.isin(db.index)).sum()), int((~db.index.isin(ids)).sum()), int(ids.duplicated().sum())
    joined = samples.assign(_id=ids).merge(db.reset_index().add_suffix("_db"), left_on="_id", right_on="record_id_db", how="left")
    src_split_db = joined["split_db"]
    split_mismatch = int((joined["split"].fillna("~").to_numpy() != src_split_db.fillna("~").to_numpy()).sum())
    date_mismatch = int((pd.to_datetime(joined["vn_observation_date"]) != pd.to_datetime(joined["vn_observation_date_db"])).sum())
    rows = []
    for k in HORIZONS:
        target_id = joined[f"label_source_record_id_h{k}_db"]
        expected_has = target_id.notna()
        target = db.reindex(target_id.where(expected_has, -1).astype("int64").to_numpy())
        valid_target = (target["assignment_id"].to_numpy() == joined["assignment_id_db"].to_numpy()) &                        (target["vn_observation_date"].to_numpy() == (pd.to_datetime(joined["vn_observation_date_db"]) + pd.Timedelta(days=k)).to_numpy())
        same_split = src_split_db.notna().to_numpy() & (target["split"].to_numpy() == src_split_db.to_numpy())
        expected_usable = expected_has.to_numpy() & valid_target & same_split
        flag_usable = joined[f"label_usable_h{k}"].astype(bool).to_numpy()
        flag_has = joined[f"has_label_h{k}"].astype(bool).to_numpy()
        seen = set(joined.loc[expected_usable & (src_split_db == "train").to_numpy(), "hotel_id"])
        flag_seen = joined[f"hotel_seen_in_train_h{k}"].astype(bool).to_numpy()
        rows.append({"horizon": k, "n_rows": int(len(samples)), "n_record_missing_in_db": missing, "n_record_extra_in_db": extra, "n_duplicate_record_in_parquet": duplicated,
                     "n_split_mismatch": split_mismatch, "n_date_mismatch": date_mismatch, "n_has_label_mismatch": int((flag_has != expected_has.to_numpy()).sum()),
                     "n_usable_flag_true_expected_false": int((flag_usable & ~expected_usable).sum()), "n_usable_flag_false_expected_true": int((~flag_usable & expected_usable).sum()),
                     "n_hotel_seen_flag_mismatch": int((flag_seen != joined["hotel_id"].isin(seen).to_numpy()).sum()), "n_usable_recomputed": int(expected_usable.sum())})
    out = pd.DataFrame(rows)
    mismatch_columns = [c for c in out.columns if c.startswith("n_") and c not in ("n_rows", "n_usable_recomputed")]
    out["violations_total"] = out[mismatch_columns].sum(axis=1)
    return out


# ------------------------------------------------------------------------------------------------ 7. purge zones
def expected_zone(day: pd.Series, train_end: dt.date, validation_end: dt.date, purge: int) -> pd.Series:
    d, te, ve, p = pd.to_datetime(day), pd.Timestamp(train_end), pd.Timestamp(validation_end), pd.Timedelta(days=purge)
    return pd.Series(np.select([d <= te, d <= te + p, d <= ve, d <= ve + p], ["train", "purge_after_train", "validation", "purge_after_validation"], "test"), index=day.index)


def purge_zone_table(samples: pd.DataFrame, preflight: dict[str, Any]) -> pd.DataFrame:
    train_end, validation_end = dt.date.fromisoformat(preflight["split_train_end"]), dt.date.fromisoformat(preflight["split_validation_end"])
    purge = int(preflight["purge_gap_days"])
    zone = expected_zone(samples["vn_observation_date"], train_end, validation_end, purge)
    observed = samples["split_label"].replace({"purge": "(purge: split NULL)"})
    rows = []
    for name in ("train", "purge_after_train", "validation", "purge_after_validation", "test"):
        mask = zone == name
        want_null = name.startswith("purge")
        rows.append({"zone": name, "n_samples": int(mask.sum()), "first_vn_date": samples.loc[mask, "vn_observation_date"].min(), "last_vn_date": samples.loc[mask, "vn_observation_date"].max(),
                     "n_split_null": int((mask & samples["split"].isna()).sum()), "n_split_assigned": int((mask & samples["split"].notna()).sum()),
                     "n_mismatch_vs_expected": int((mask & (samples["split"].notna() if want_null else (observed != name))).sum()),
                     "split_train_end": train_end, "split_validation_end": validation_end, "purge_gap_days": purge})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------ 8. coverage thuc te so voi cap ngay ly thuyet trong dataset
def coverage_attrition_table(samples: pd.DataFrame) -> pd.DataFrame:
    """Cap ly thuyet NOI TAI dataset: mau S o ngay d co cap (S, d+k) neu d+k nam trong cua so ngay quan sat cua dataset va <= checkin. Khong cung quan the voi
    theoretical_date_pairs cua Wave A (full-history, theo lich protocol) nen KHONG gop ty le; day la mau so rieng cua dataset."""
    last_obs = samples["vn_observation_date"].max()
    keys = samples[["canonical_series_id", "vn_observation_date"]].drop_duplicates().assign(_exists=True)
    rows = []
    for k in HORIZONS:
        possible = _calendar_possible(samples, k, last_obs)
        probe = samples[["canonical_series_id"]].assign(vn_observation_date=samples["vn_observation_date"] + pd.Timedelta(days=k))
        exists = probe.merge(keys, on=["canonical_series_id", "vn_observation_date"], how="left")["_exists"].fillna(False).astype(bool).to_numpy()
        has = samples[f"has_label_h{k}"].astype(bool).to_numpy()
        usable = samples[f"label_usable_h{k}"].astype(bool).to_numpy()
        poss = possible.to_numpy()
        rows.append({"horizon": k, "n_selected": int(len(samples)), "n_not_calendar_possible": int((~poss).sum()), "n_calendar_possible": int(poss.sum()),
                     "n_target_day_sample_missing": int((poss & ~exists).sum()), "n_has_label": int(has.sum()), "n_has_label_cross_split": int((has & ~usable).sum()),
                     "n_usable": int(usable.sum()), "n_has_label_but_not_calendar_possible": int((has & ~poss).sum()),
                     "n_has_label_disagrees_with_target_exists": int((has != exists).sum())})
    return with_rate(pd.DataFrame(rows), "usable_rate_of_calendar_possible", "n_usable", "n_calendar_possible")


# ------------------------------------------------------------------------------------------------ 9. feature missingness + kiem tra lag causal
def feature_columns(samples: pd.DataFrame, dictionary: pd.DataFrame | None) -> list[str]:
    if dictionary is not None and {"column", "group"} <= set(dictionary.columns):
        names = dictionary.loc[~dictionary["group"].isin([IDENTIFIER_GROUP, LABEL_GROUP]), "column"].tolist()
        return [c for c in names if c in samples.columns]
    raise ValueError("thieu data_dictionary.csv de xac dinh nhom feature - khong doan danh sach feature tu ten cot.")


def feature_missingness_table(samples: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    rows = []
    for column in features:
        for dim, label_col in (("split", "split_label"), ("inference_mode", "inference_mode")):
            for value, group in samples.groupby(label_col, sort=True):
                rows.append({"feature": column, "dimension": dim, "value": value, "n_samples": int(len(group)), "n_null": int(group[column].isna().sum())})
        rows.append({"feature": column, "dimension": "ALL", "value": "ALL", "n_samples": int(len(samples)), "n_null": int(samples[column].isna().sum())})
    return with_rate(pd.DataFrame(rows), "null_share", "n_null", "n_samples")


def lag_causality_table(samples: pd.DataFrame) -> pd.DataFrame:
    """price_lag_k phai non-null DUNG KHI series co mau da chon o d-k, va (khi non-null) bang gia cua mau do - kiem tra causal truc tiep tren Parquet."""
    price = samples[["canonical_series_id", "vn_observation_date", "current_price"]].drop_duplicates(["canonical_series_id", "vn_observation_date"])
    rows = []
    for k in LAG_DAYS:
        column = f"price_lag_{k}"
        probe = samples[["canonical_series_id"]].assign(vn_observation_date=samples["vn_observation_date"] - pd.Timedelta(days=k))
        merged = probe.merge(price, on=["canonical_series_id", "vn_observation_date"], how="left")
        expected_present = merged["current_price"].notna().to_numpy()
        observed_present = samples[column].notna().to_numpy()
        both = expected_present & observed_present
        value_mismatch = int((np.abs(samples[column].to_numpy(dtype=float)[both] - merged["current_price"].to_numpy(dtype=float)[both]) > 1e-6).sum())   # lag la ban sao gia: so sanh tuyet doi, khong rtol
        rows.append({"feature": column, "n_samples": int(len(samples)), "n_expected_present": int(expected_present.sum()), "n_observed_present": int(observed_present.sum()),
                     "n_presence_mismatch": int((expected_present != observed_present).sum()), "n_value_mismatch": value_mismatch})
    out = pd.DataFrame(rows)
    out["violations_total"] = out["n_presence_mismatch"] + out["n_value_mismatch"]
    return out


# ------------------------------------------------------------------------------------------------ 10. cold-start vs history-enriched
def readiness_mode_tables(samples: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for (split, mode), group in samples.groupby(["split_label", "inference_mode"], sort=True):
        row = {"split": split, "inference_mode": mode, "n_samples": int(len(group)),
               "median_history_observation_count": float(group["history_observation_count"].median())}
        for k in HORIZONS:
            row[f"n_usable_h{k}"] = int(group[f"label_usable_h{k}"].sum())
        rows.append(row)
    by_mode = pd.DataFrame(rows)
    first = samples.groupby("canonical_series_id").agg(first_day=("vn_observation_date", "min"))
    enriched = samples[samples["inference_mode"] == "history_enriched"].groupby("canonical_series_id").agg(first_enriched=("vn_observation_date", "min"))
    joined = first.join(enriched, how="left")
    days = (joined["first_enriched"] - joined["first_day"]).dt.days.dropna()
    summary = pd.DataFrame([{"n_series": int(len(joined)), "n_series_reaching_history_enriched": int(len(days)),
                             "share_reaching_history_enriched": rate(len(days), len(joined)),
                             "median_days_to_history_enriched": float(days.median()) if len(days) else float("nan"),
                             "p90_days_to_history_enriched": float(days.quantile(0.9)) if len(days) else float("nan")}])
    return by_mode, summary


# ------------------------------------------------------------------------------------------------ 11. review-score tier (POST-HOC)
def review_tier_table(samples: pd.DataFrame, hotels: pd.DataFrame) -> pd.DataFrame:
    tiers = hotels.copy()
    score = pd.to_numeric(tiers["review_score"], errors="coerce")
    tiers["review_tier"] = "unknown"
    for lo, hi, name in REVIEW_TIERS:
        tiers.loc[(score >= lo) & (score < hi), "review_tier"] = name
    merged = samples.merge(tiers[["hotel_id", "review_tier"]], on="hotel_id", how="left", validate="many_to_one")
    merged["review_tier"] = merged["review_tier"].fillna("unknown")
    rows = []
    for tier, group in merged.groupby("review_tier", sort=True):
        row = {"review_tier": tier, "n_hotels": int(group["hotel_id"].nunique()), "n_samples": int(len(group)), "share_of_samples": rate(len(group), len(merged))}
        for k in HORIZONS:
            row[f"n_usable_h{k}"] = int(group[f"label_usable_h{k}"].sum())
        rows.append(row)
    out = pd.DataFrame(rows)
    out["caveat"] = "POST-HOC: review_score la snapshot luc build warehouse, khong as-of tai prediction_time; khong phai feature/model input."
    return out


# ------------------------------------------------------------------------------------------------ 12. strata exact/alias
def strata_table(samples: pd.DataFrame, matches: pd.DataFrame, ml_samples: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """prediction/label match status suy tu `ml_item_reference_matches` theo selected_record_id (hoat dong ca voi artifact cu chua co cot strata); neu Parquet co cot strata
    thi doi chieu tung dong. Tra (bang strata, bang doi chieu Parquet)."""
    selected = matches.dropna(subset=["selected_record_id"]).drop_duplicates("selected_record_id")
    by_record = dict(zip(selected["selected_record_id"].astype("int64").tolist(), selected["match_status"].tolist()))
    pred = samples["warehouse_record_id"].map(lambda r: by_record.get(int(r)))
    ml = ml_samples.set_index("record_id")
    rows, agreement = [], []
    for k in HORIZONS:
        target_ids = samples["warehouse_record_id"].map(ml[f"label_source_record_id_h{k}"])
        label_status = target_ids.map(lambda r: by_record.get(int(r)) if pd.notna(r) else None)
        usable = samples[f"label_usable_h{k}"].astype(bool)
        frame = pd.DataFrame({"split": samples["split_label"], "pred": pred, "label": label_status.where(target_ids.notna(), None), "usable": usable})
        for split, group in [*frame.groupby("split", sort=True), ("ALL", frame)]:
            u = group[group["usable"]]
            rows.append({"horizon": k, "split": split, "n_usable": int(len(u)), "n_both_exact": int(((u["pred"] == "exact") & (u["label"] == "exact")).sum()),
                         "n_any_alias": int(((u["pred"] == "alias") | (u["label"] == "alias")).sum()), "n_pred_alias": int((u["pred"] == "alias").sum()),
                         "n_label_alias": int((u["label"] == "alias").sum()), "n_status_missing": int((u["pred"].isna() | u["label"].isna()).sum())})
        if f"label_match_status_h{k}" in samples.columns:
            pq_label = samples[f"label_match_status_h{k}"].astype("object").where(samples[f"label_match_status_h{k}"].notna(), None)
            agreement.append({"column": f"label_match_status_h{k}", "n_rows": int(len(samples)),
                              "n_disagree": int(((pq_label.fillna("~") != label_status.where(target_ids.notna(), None).fillna("~"))).sum())})
    if "prediction_match_status" in samples.columns:
        agreement.append({"column": "prediction_match_status", "n_rows": int(len(samples)), "n_disagree": int((samples["prediction_match_status"].astype("object") != pred).sum())})
    table = with_rate(pd.DataFrame(rows), "both_exact_share", "n_both_exact", "n_usable")
    return table, pd.DataFrame(agreement, columns=["column", "n_rows", "n_disagree"])


# ------------------------------------------------------------------------------------------------ tong hop
def compute_tables(data: WaveBData) -> dict[str, Table]:
    samples = prepare_samples(data.samples)
    matches = prepare_matches(data.matches)
    features = feature_columns(samples, data.dictionary)
    tables: dict[str, Table] = {}
    def add(table_id: str, frame: pd.DataFrame, bullets: tuple[int, ...], grain: str, denominator: str, note: str = "") -> None:
        tables[table_id] = Table(table_id, frame, bullets, grain, denominator, note)
    add("b01_assignment_summary", assignment_summary_table(data.assignments), (1,), "city", "assignment cua dung dataset_version")
    summary, distribution = time_to_approval_tables(data.assignments, data.first_evidence)
    add("b01_time_to_approval", summary, (1,), "city", "assignment co bang chung reference dau tien",
        "days = approved_at - finished_at cua run chua bang chung dau tien (run-completion semantics, UTC)")
    add("b01_time_to_approval_distribution", distribution, (1,), "bin ngay", "assignment co bang chung dau tien")
    add("b02_match_by_phase", match_breakdown(matches, None), (2,), "observation phase x match_status", "item trong phase",
        "phase theo EVENT TIME observation (exact/alias: observed_at cua selected record; unavailable/ambiguous: MIN(observed_at) cua item): post_approval <=> event >= approved_at; "
        "unavailable = khong thay reference option, KHONG phai hotel chet")
    add("b02_match_by_city", match_breakdown(matches, "city"), (2,), "observation phase x city x match_status", "item trong (phase, city)")
    add("b02_match_by_lead_bucket", match_breakdown(matches, "lead_bucket"), (2,), "observation phase x lead bucket x match_status", "item trong (phase, bucket)",
        "lead = checkin_date - ngay VN cua EVENT TIME observation (khong phai run.started_at); bucket nua mo")
    add("b02_match_by_approval_run_phase", match_breakdown(matches, None, "approval_run_phase", APPROVAL_RUN_PHASES), (2,), "approval-run phase x match_status", "item trong phase",
        "AUDIT vong doi run (approving_run nhan dien bang run_id == approving_run_warehouse_id); KHONG phai dieu kien sample")
    add("b02_event_time_audit", event_time_audit_table(matches), (2,), "1 dong", "item match", "do chenh run time vs observation time tren chinh dataset")
    add("b02_match_by_source", match_breakdown(matches, "source_code"), (2,), "phase x source x match_status", "item trong (phase, source)")
    reasons, per_day = snapshot_tables(data.ml_samples)
    add("b03_snapshot_reasons", reasons, (3,), "selected x reason", "toan bo ml_samples (ke ca mau khong duoc chon)")
    add("b03_snapshot_candidates_per_series_day", per_day, (3,), "candidates x selected", "(assignment, ngay VN)")
    for name, frame in sample_count_tables(samples).items():
        add("b04_" + name, frame, (4,), name.replace("sample_counts_by_", ""), "mau duoc chon (is_daily_snapshot_selected)")
    add("b05_label_rates", label_rate_table(samples, data.evaluation_horizons), (5,), "split x horizon", "tach bat: selected / calendar_possible / split_assigned / source_eligible / has_label / usable / primary",
        "evaluated_in_build=False: horizon khong thuoc evaluation_horizons cua build (khong dung cho ket luan danh gia)")
    add("b06_label_integrity", label_integrity_table(data.ml_samples), (6,), "horizon", "mau co nhan", "violations_total phai = 0; cross_split chi la thong tin (nhan khong usable)")
    add("b06_label_usability_recheck", label_recheck_table(samples, data.ml_samples), (6,), "horizon", "moi dong Parquet (doi chieu TUNG warehouse_record_id voi ml_samples)",
        "tai tinh has_label/usable/split/date/hotel_seen tu ml_samples; moi mismatch la vi pham (khong so tong)")
    add("b07_purge_zones", purge_zone_table(samples, data.preflight), (7,), "zone", "mau duoc chon", "n_mismatch_vs_expected phai = 0")
    add("b08_coverage_attrition", coverage_attrition_table(samples), (8,), "horizon", "cap ly thuyet NOI TAI dataset",
        "KHONG cung quan the voi theoretical_date_pairs cua Wave A (full-history): hai mau so rieng, khong gop thanh ty le chuyen doi")
    add("b09_feature_missingness", feature_missingness_table(samples, features), (9,), "feature x chieu", "mau duoc chon trong nhom")
    add("b09_lag_causality", lag_causality_table(samples), (9,), "price_lag_k", "mau duoc chon", "violations_total phai = 0")
    by_mode, mode_summary = readiness_mode_tables(samples)
    add("b10_readiness_by_mode", by_mode, (10,), "split x inference_mode", "mau duoc chon")
    add("b10_history_enrichment", mode_summary, (10,), "series", "frozen series co mau")
    add("b11_review_tier", review_tier_table(samples, data.hotels), (11,), "review tier (post-hoc)", "mau duoc chon", "snapshot, khong as-of")
    strata, agreement = strata_table(samples, matches, data.ml_samples)
    add("b12_strata_exact_alias", strata, (12,), "horizon x split", "nhan usable")
    add("b12_strata_parquet_agreement", agreement, (12,), "cot strata", "mau duoc chon", "chi co khi Parquet co cot strata (features >= 1.2); n_disagree phai = 0")
    return tables


COVERAGE_BULLETS: dict[int, str] = {
    1: "causal assignment count va thoi gian toi approved_at", 2: "exact/alias/unavailable/ambiguous theo city/lead time/source (pre va post approval)",
    3: "daily snapshot duoc chon va ly do loai duplicate", 4: "sample count theo split va inference_mode", 5: "label count/rate h1/h3/h7/h14 (mau so tach bat)",
    6: "target cung assignment, dung ngay, cung split", 7: "purge zones", 8: "coverage thuc te so voi cap ngay ly thuyet (+attrition)",
    9: "feature availability/missingness sau prediction time (+kiem tra lag causal)", 10: "cold-start so voi history-enriched readiness",
    11: "post-hoc segmentation theo review-score tier + caveat", 12: "strata exact/alias (audit; khong phai feature)",
}


def coverage_matrix(tables: dict[str, Table], figures: dict[str, tuple[int, ...]]) -> pd.DataFrame:
    rows = []
    for bullet, text in COVERAGE_BULLETS.items():
        ids = [t.table_id for t in tables.values() if bullet in t.bullets]
        figs = [name for name, bullets in figures.items() if bullet in bullets]
        rows.append({"bullet": bullet, "requirement": text, "tables": ";".join(ids), "figures": ";".join(figs), "n_tables": len(ids),
                     "n_rows_total": int(sum(len(tables[i].frame) for i in ids)), "covered": bool(ids)})
    return pd.DataFrame(rows)


def hard_violations(tables: dict[str, Table]) -> dict[str, int]:
    """Cac kiem tra toan ven BAT BUOC = 0: neu khac 0, runner danh dau analysis FAILED (khong publish nhu PASS)."""
    out = {
        "label_integrity_violations": int(tables["b06_label_integrity"].frame["violations_total"].sum()),
        "label_usability_recheck_mismatch": int(tables["b06_label_usability_recheck"].frame["violations_total"].sum()),
        "unavailable_or_ambiguous_items_with_multiple_event_times": int(tables["b02_event_time_audit"].frame["n_unavailable_or_ambiguous_with_multiple_observed_at"].sum()),
        "purge_zone_mismatch": int(tables["b07_purge_zones"].frame["n_mismatch_vs_expected"].sum()),
        "lag_causality_violations": int(tables["b09_lag_causality"].frame["violations_total"].sum()),
        "attrition_has_label_disagrees_with_target_exists": int(tables["b08_coverage_attrition"].frame["n_has_label_disagrees_with_target_exists"].sum()),
        "attrition_has_label_not_calendar_possible": int(tables["b08_coverage_attrition"].frame["n_has_label_but_not_calendar_possible"].sum()),
        "strata_parquet_disagree": int(tables["b12_strata_parquet_agreement"].frame["n_disagree"].sum()) if len(tables["b12_strata_parquet_agreement"].frame) else 0,
        "strata_status_missing": int(tables["b12_strata_exact_alias"].frame["n_status_missing"].sum()),
        "series_days_without_exactly_one_selected": int(tables["b03_snapshot_candidates_per_series_day"].frame.query("not is_exactly_one_selected")["n_series_days"].sum()),
    }
    return out
