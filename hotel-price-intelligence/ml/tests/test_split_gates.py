"""Test GPT review vong 1 (dataset builder): DB-M1 split theo coverage that, DB-M2/M3 primary horizon-specific + moi chi so primary tren cung tap,
DB-M4 hop dong cot mot nguon su that, DB-m2 timeout SELECT huu han, DB-m3 official fail-closed khi ml/ dirty, nhat quan chinh sach split <-> sufficiency.
Thuan Python (khong MySQL)."""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager

import numpy as np
import pandas as pd
import pytest

from dataset_builder import config as cfg
from dataset_builder import db
from dataset_builder.dictionary import dictionary_rows
from dataset_builder.export import ProvenanceError, assert_official_provenance
from dataset_builder.feature_spec import HORIZONS, feature_config
from dataset_builder.features import output_columns
from dataset_builder.splitter import SplitPlan, plan_split
from dataset_builder.sufficiency import candidate_frame, evaluate_horizon, gate_for, sufficiency_report
from dataset_builder.validation import split_policy_consistency

D = dt.date
POLICY = cfg.split_selection_policy()
GATES = POLICY["gates"]
CITIES = ("Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc")


def make_samples(*, hotels_per_city: int | dict, days: int, first: dt.date = D(2026, 8, 21), sparse_days: list[int] | None = None) -> pd.DataFrame:
    """Mau chon: moi hotel x ngay quan sat, co nhan hK khi ngay + K nam trong chuoi. `sparse_days`: chi giu nhung ngay offset nay."""
    rows = []
    offsets = sparse_days if sparse_days is not None else list(range(days))
    last = max(offsets)
    for ci, city in enumerate(CITIES):
        n = hotels_per_city[city] if isinstance(hotels_per_city, dict) else hotels_per_city
        for h in range(n):
            for off in offsets:
                row = {"hotel_id": f"{ci}-{h}", "city": city, "canonical_series_id": f"s-{ci}-{h}",
                       "vn_observation_date": pd.Timestamp(first) + pd.Timedelta(days=off)}
                for k in HORIZONS:
                    row[f"has_label_h{k}"] = (off + k) in offsets and off + k <= last
                rows.append(row)
    return pd.DataFrame(rows)


def evaluator(samples: pd.DataFrame):
    return lambda plan, horizon: evaluate_horizon(candidate_frame(samples, plan, horizon), horizon, gate_for(GATES, horizon))


def run_plan(samples: pd.DataFrame, purge: int = 14):
    first, last = samples["vn_observation_date"].min().date(), samples["vn_observation_date"].max().date()
    return plan_split(first, last, policy=POLICY, purge_gap_days=purge, evaluate=evaluator(samples))


# ----------------------------------------------------------------------------------------------------- DB-M1
def test_sparse_days_over_a_long_calendar_span_no_longer_count_as_feasible():
    """Hai ngay co mau cach nhau 120 ngay: truoc day van bi ghi 'gate_driven:H=14'. Nay: ung vien lich-kha-thi nhung gate that bai -> fallback."""
    samples = make_samples(hotels_per_city=30, days=121, sparse_days=[0, 120])
    plan = run_plan(samples)
    assert plan.policy_path == "fallback_ratio" and plan.feasible_horizon is None
    h14 = next(c for c in plan.candidates if c["horizon"] == 14)
    assert h14["calendar_feasible"] is True and h14["gate_pass"] is False and h14["failed_gates"]


def test_dense_coverage_with_enough_hotels_selects_the_largest_horizon_that_passes_its_own_gates():
    samples = make_samples(hotels_per_city=24, days=140)        # 120 hotel (24/thanh pho), ngay lien tuc
    plan = run_plan(samples)
    assert plan.feasible_horizon == 14 and plan.policy_path == "gate_driven:H=14"
    chosen = next(c for c in plan.candidates if c["horizon"] == 14)
    assert chosen["gate_pass"] is True and chosen["splits"]["validation"]["hotels_seen_in_train"] == 120


def test_too_few_hotels_or_one_weak_city_blocks_gate_driven_choice():
    few = make_samples(hotels_per_city=15, days=140)            # 75 hotel < 100 (h14) va < 150 (h1/h3/h7)
    assert run_plan(few).policy_path == "fallback_ratio"
    weak_city = make_samples(hotels_per_city={"Hồ Chí Minh": 40, "Hà Nội": 40, "Vũng Tàu": 40, "Đà Lạt": 40, "Phú Quốc": 10}, days=140)
    plan = run_plan(weak_city)
    assert plan.policy_path == "fallback_ratio"
    assert any("hotels/city" in reason for c in plan.candidates if c.get("failed_gates") for reason in c["failed_gates"])


def with_series(samples: pd.DataFrame, n: int) -> pd.DataFrame:
    """Nhan `n` chuoi cho moi hotel (gate dem MAU co nhan, ma moi hotel that co nhieu series)."""
    parts = []
    for s in range(n):
        part = samples.copy()
        part["canonical_series_id"] = part["canonical_series_id"] + f"-{s}"
        parts.append(part)
    return pd.concat(parts, ignore_index=True)


def test_smaller_horizon_wins_when_the_larger_one_is_not_feasible_and_candidates_are_audited():
    samples = with_series(make_samples(hotels_per_city=30, days=95), 3)      # 150 hotel x 3 series; 95 ngay < 98 can cho H14
    plan = run_plan(samples)
    assert plan.feasible_horizon == 7 and plan.policy_path == "gate_driven:H=7"
    by_h = {c["horizon"]: c for c in plan.candidates}
    assert by_h[14]["calendar_feasible"] is False and by_h[14]["gate_pass"] is None
    assert by_h[7]["gate_pass"] is True and by_h[7]["splits"]["test"]["hotels_seen_in_train"] == 150
    # va khi du ngay + du mau thi H14 thang
    assert run_plan(with_series(make_samples(hotels_per_city=30, days=100), 3)).feasible_horizon == 14


def test_gate_failure_not_just_calendar_infeasibility_pushes_selection_down():
    """150 hotel x 1 series, 95 ngay: du lich cho H7 nhung train chi co 4.800 mau co nhan < 5.000 -> gate loai H7 (truoc day chi nhin lich nen chon H7); H3 pass."""
    plan = run_plan(make_samples(hotels_per_city=30, days=95))
    h7 = next(c for c in plan.candidates if c["horizon"] == 7)
    assert h7["calendar_feasible"] is True and h7["gate_pass"] is False and h7["failed_gates"] == ["train: labeled_samples 4800 < 5000"]
    assert plan.feasible_horizon == 3 and plan.policy_path == "gate_driven:H=3"


# ------------------------------------------------------------------------------------------------ candidate_frame
def test_candidate_frame_marks_labels_usable_only_when_target_is_in_the_same_split():
    first = D(2026, 9, 1)
    plan = SplitPlan(first, first + dt.timedelta(days=9), first + dt.timedelta(days=12), first + dt.timedelta(days=14), first + dt.timedelta(days=17),
                     first + dt.timedelta(days=22), 2, "gate_driven:H=3", 3)
    samples = make_samples(hotels_per_city=1, days=23, first=first)
    frame = candidate_frame(samples, plan, 3)
    one = frame[frame["hotel_id"] == "0-0"].set_index(frame[frame["hotel_id"] == "0-0"]["vn_observation_date"].dt.day)
    # ngay offset 0..9 train; nhan +3 phai nam trong train (<= offset 9) -> usable cho offset 0..6; 7,8,9 target roi vao purge -> khong usable
    assert [bool(one.loc[d, "label_usable_h3"]) for d in range(1, 11)] == [True] * 7 + [False] * 3
    assert one.loc[14, "split"] == "validation" and bool(one.loc[14, "label_usable_h3"]) is False    # val offset 13..14, target 16..17 -> purge/test
    assert bool(one.loc[19, "label_usable_h3"]) is True and one.loc[19, "split"] == "test"          # test offset 17..22, target 20..22 -> test (offset 19 -> 22)


# ------------------------------------------------------------------------------------------------- DB-M2 / DB-M3
def _frame(rows: list[tuple]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["hotel_id", "city", "canonical_series_id", "vn_observation_date", "split", "label_usable_h1", "label_usable_h14"])
    frame["vn_observation_date"] = pd.to_datetime(frame["vn_observation_date"])
    return frame


def test_primary_hotel_set_is_per_horizon_not_a_single_global_flag():
    day = "2026-09-01"
    frame = _frame([
        ("A", "Hà Nội", "sA", day, "train", True, False),          # A co mau train dung duoc o h1 nhung KHONG o h14
        ("B", "Hà Nội", "sB", day, "train", True, True),
        ("A", "Hà Nội", "sA", "2026-09-30", "validation", True, True),
        ("B", "Hà Nội", "sB", "2026-09-30", "validation", True, True),
    ])
    gate = {"eligible_prediction_dates": {"train": 1, "validation": 1, "test": 0}, "labeled_samples": {"train": 1, "validation": 1, "test": 0},
            "hotels_per_eval_split": {"total": 1, "per_city": 0}}
    h1, h14 = evaluate_horizon(frame, 1, gate), evaluate_horizon(frame, 14, gate)
    assert h1["splits"]["validation"]["hotels_seen_in_train"] == 2                      # A, B deu thay o train cua h1
    assert h14["splits"]["validation"]["hotels_seen_in_train"] == 1                     # chi B thay o train cua h14
    assert h14["splits"]["validation"]["excluded_unseen_hotel_samples"] == 1 and h14["splits"]["validation"]["all_hotels"] == 2


def test_every_primary_gate_is_computed_on_the_primary_frame_not_all_hotels():
    """Nhieu hotel chua thay o train lam so tong all-hotel dat gate nhung primary khong: PHAI exploratory (GPT DB-M3)."""
    rows = [("S", "Hà Nội", "sS", f"2026-09-{d:02d}", "train", True, True) for d in range(1, 11)]
    rows += [("S", "Hà Nội", "sS", "2026-10-20", "validation", True, True)]
    rows += [(f"U{i}", "Hà Nội", f"sU{i}", f"2026-10-{d:02d}", "validation", True, True) for i in range(30) for d in range(1, 11)]   # 30 hotel la, 300 mau
    frame = _frame(rows)
    gate = {"eligible_prediction_dates": {"train": 5, "validation": 5, "test": 0}, "labeled_samples": {"train": 5, "validation": 100, "test": 0},
            "hotels_per_eval_split": {"total": 1, "per_city": 0}}
    result = evaluate_horizon(frame, 1, gate)
    val = result["splits"]["validation"]
    assert val["all_labeled_samples"] == 301 >= 100 and val["labeled_samples"] == 1 < 100           # all-hotel qua, primary khong
    assert val["all_eligible_prediction_dates"] == 11 and val["eligible_prediction_dates"] == 1
    assert result["status"] == "exploratory"
    assert any("labeled_samples 1 < 100" in reason for reason in result["failed_gates"])
    assert any("eligible_prediction_dates 1 < 5" in reason for reason in result["failed_gates"])


def test_sufficiency_report_has_audit_fields_for_every_horizon():
    frame = _frame([("A", "Hà Nội", "sA", "2026-09-01", "train", True, True)])
    for k in (3, 7):
        frame[f"label_usable_h{k}"] = False
    report = sufficiency_report(frame, GATES)
    assert set(report["horizons"]) == {"h1", "h3", "h7", "h14"}
    assert "all_labeled_samples" in report["horizons"]["h1"]["splits"]["validation"] and "primary" in report["definition"]


# ----------------------------------------------------------------------------------------------------- DB-M4
def test_identifier_contract_is_a_single_source_of_truth():
    config = {"feature_config": feature_config()}
    identifiers = config["feature_config"]["identifier_columns"]
    assert "record_id_ref" not in identifiers and "warehouse_record_id" in identifiers
    assert [f"hotel_seen_in_train_h{k}" for k in HORIZONS] == [c for c in identifiers if c.startswith("hotel_seen_in_train")]
    assert "hotel_seen_in_train" not in identifiers
    columns = output_columns(config)
    assert columns[:len(identifiers)] == identifiers and len(columns) == len(set(columns))
    assert [r["column"] for r in dictionary_rows(columns)] == columns
    # Doi danh sach dinh danh trong config PHAI doi output_columns (khong con ban hard-code thu hai) va doi hash feature_config
    reordered = {"feature_config": {**config["feature_config"], "identifier_columns": list(reversed(identifiers))}}
    assert output_columns(reordered)[:len(identifiers)] == list(reversed(identifiers))
    from dataset_builder.config import sha256_hex, canonical_json
    assert sha256_hex(canonical_json(config["feature_config"])) != sha256_hex(canonical_json(reordered["feature_config"]))


def test_feature_version_was_bumped_for_the_contract_change():
    assert feature_config()["version"] != "features-v1.0.0"


# ------------------------------------------------------------------------------------------------- DB-m2 / DB-m3
def test_connect_sets_finite_select_timeout_and_zero_disables_it(monkeypatch):
    executed: list[str] = []

    class Cursor:
        def execute(self, sql, *a):
            executed.append(sql)

        def close(self):
            pass

    class Conn:
        def cursor(self, *a, **k):
            return Cursor()

    @contextmanager
    def fake_connection(database):
        yield Conn()

    monkeypatch.setattr(db, "warehouse_connection", fake_connection)
    monkeypatch.setattr(db, "require_target_database", lambda name: name)
    old = db.max_execution_ms()
    try:
        assert old == 30 * 60 * 1000
        with db.connect("warehouse_x"):
            pass
        assert executed == ["SET SESSION max_execution_time = 1800000"]
        executed.clear()
        db.set_max_execution_ms(0)
        with db.connect("warehouse_x"):
            pass
        assert executed == []
        with pytest.raises(ValueError):
            db.set_max_execution_ms(-1)
    finally:
        db.set_max_execution_ms(old)


@pytest.mark.parametrize("purpose, state, ok", [
    ("official", {"head": "abc", "ml_dir_dirty": False}, True),
    ("official", {"head": "abc", "ml_dir_dirty": True, "ml_dirty_files": 2}, False),
    ("official", {"head": None, "error": "no git"}, False),
    ("rehearsal", {"head": "abc", "ml_dir_dirty": True}, True),
    ("dev", {"head": None, "error": "no git"}, True),
])
def test_official_build_fails_closed_on_unknown_or_dirty_code(purpose, state, ok):
    if ok:
        assert_official_provenance(purpose, state)
    else:
        with pytest.raises(ProvenanceError):
            assert_official_provenance(purpose, state)


# ---------------------------------------------------------------------------------- split policy <-> sufficiency
def _consistency_inputs(plan_h, sufficiency_status="primary_eligible", numbers_match=True):
    splits = {s: {"eligible_prediction_dates": 9, "labeled_samples": 99, "hotels_seen_in_train": 5, "hotels_seen_in_train_per_city": {"a": 1}}
              for s in ("train", "validation", "test")}
    final = {s: dict(v) for s, v in splits.items()}
    if not numbers_match:
        final["validation"]["labeled_samples"] = 98
    report = {"plan": {"policy_path": f"gate_driven:H={plan_h}", "feasible_horizon": plan_h,
                       "candidates": [{"horizon": plan_h, "gate_pass": True, "splits": splits}]}}
    return report, {"horizons": {f"h{plan_h}": {"status": sufficiency_status, "splits": final, "failed_gates": []}}}


def test_split_policy_consistency_accepts_matching_numbers_and_rejects_drift():
    assert split_policy_consistency(*_consistency_inputs(7))[0] is True
    assert split_policy_consistency(*_consistency_inputs(7, numbers_match=False))[0] is False       # so luc chon bien != so sau export
    assert split_policy_consistency(*_consistency_inputs(7, sufficiency_status="exploratory"))[0] is False
    assert split_policy_consistency(None, None)[0] is False


def test_split_policy_consistency_for_fallback_requires_no_wrongly_rejected_candidate():
    ok = {"plan": {"policy_path": "fallback_ratio", "feasible_horizon": None, "candidates": [{"horizon": 14, "gate_pass": False}]}}
    bad = {"plan": {"policy_path": "fallback_ratio", "feasible_horizon": None, "candidates": [{"horizon": 14, "gate_pass": True}]}}
    assert split_policy_consistency(ok, {"horizons": {}})[0] is True
    assert split_policy_consistency(bad, {"horizons": {}})[0] is False


# ----------------------------------------------------------------- GPT vong 2: thieu hut co cau truc (thay cho scalar days_until_feasible)
def test_shortfall_is_structured_deterministic_and_zero_when_gate_passes():
    from dataset_builder.sufficiency import shortfall

    gate = {"eligible_prediction_dates": {"train": 28, "validation": 7, "test": 7}, "labeled_samples": {"train": 5000, "validation": 1000, "test": 1000},
            "hotels_per_eval_split": {"total": 150, "per_city": 20}}
    cities = {"Hồ Chí Minh": 30, "Hà Nội": 25, "Vũng Tàu": 12, "Đà Lạt": 20, "Phú Quốc": 3}
    splits = {
        "train": {"eligible_prediction_dates": 20, "labeled_samples": 6000, "hotels_seen_in_train": 200, "hotels_seen_in_train_per_city": cities},
        "validation": {"eligible_prediction_dates": 7, "labeled_samples": 400, "hotels_seen_in_train": 70, "hotels_seen_in_train_per_city": cities},
        "test": {"eligible_prediction_dates": 9, "labeled_samples": 1000, "hotels_seen_in_train": 150, "hotels_seen_in_train_per_city": {c: 30 for c in cities}},
    }
    got = shortfall(splits, gate)
    assert got["train"] == {"eligible_prediction_dates_short": 8, "labeled_samples_short": 0}
    assert got["validation"] == {"eligible_prediction_dates_short": 0, "labeled_samples_short": 600, "hotels_total_short": 80,
                                 "hotels_per_city_short": {"Vũng Tàu": 8, "Phú Quốc": 17}}
    assert got["test"] == {"eligible_prediction_dates_short": 0, "labeled_samples_short": 0, "hotels_total_short": 0, "hotels_per_city_short": {}}
    assert shortfall(splits, gate) == got


# ----------------------------------------------------------------- tai lap: file coverage_report.json khong duoc chua thoi gian chay
def test_coverage_report_embeds_the_split_report_without_volatile_timings():
    import pandas as pd

    from dataset_builder.feature_spec import HORIZONS
    from dataset_builder.reports import coverage_report

    frame = pd.DataFrame({"hotel_id": ["h1", "h2"], "canonical_series_id": ["s1", "s2"], "vn_observation_date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
                          "split": ["train", "test"], "city": ["Hà Nội", "Đà Lạt"], "lead_time_bucket": ["3-7", "7-14"], "inference_mode": ["cold_start", "history_enriched"]})
    for h in HORIZONS:
        frame[f"has_label_h{h}"], frame[f"label_usable_h{h}"], frame[f"hotel_seen_in_train_h{h}"] = [True, False], [True, False], [True, False]
    split = {"elapsed_s": 3.14, "step": "split", "span_days": 45, "plan": {"policy_path": "fallback_ratio"}}
    first, second = coverage_report(frame, {**split, "elapsed_s": 3.1}), coverage_report(frame, {**split, "elapsed_s": 9.9})
    assert first == second and "elapsed_s" not in first["split"] and first["split"]["span_days"] == 45
    assert split["elapsed_s"] == 3.14                                            # khong sua doi tuong dau vao
    assert coverage_report(frame, None)["split"] is None


# ----------------------------------------------------------------- GPT file 46: ANALYZE TABLE co the tra Msg_type='error' ma khong nem exception
class _FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def execute(self, sql):
        self.executed.append(sql)

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self, dictionary=False):
        assert dictionary is True
        return self.cursor_obj


def test_analyze_tables_returns_status_and_raises_on_error_rows():
    from dataset_builder.db import AnalyzeError, analyze_tables

    ok = _FakeConn([{"Table": "wh.ml_samples", "Op": "analyze", "Msg_type": "status", "Msg_text": "OK"}])
    assert analyze_tables(ok, ("ml_samples",)) == [{"table": "ml_samples", "msg_type": "status", "msg_text": "OK"}]
    assert ok.cursor_obj.executed == ["ANALYZE TABLE ml_samples"]
    noted = _FakeConn([{"Msg_type": "note", "Msg_text": "Table does not support optimize"}, {"Msg_type": "status", "Msg_text": "OK"}])
    assert [m["msg_type"] for m in analyze_tables(noted, ("ml_samples",))] == ["note", "status"]            # note/warning chi ghi audit
    bad = _FakeConn([{"Msg_type": "Error", "Msg_text": "Table doesn't exist"}])
    with pytest.raises(AnalyzeError, match="doesn't exist"):
        analyze_tables(bad, ("ml_samples",))
    with pytest.raises(AnalyzeError, match="khong co ket qua"):
        analyze_tables(_FakeConn([]), ("ml_samples",))
    with pytest.raises(ValueError):
        analyze_tables(ok, ("ml_samples; DROP TABLE x",))                                                    # chi nhan identifier hop le
