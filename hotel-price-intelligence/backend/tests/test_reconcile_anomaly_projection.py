"""reconcile_anomaly_projection.py - is_anomaly la PROJECTION cua anomaly_review_resolutions, khong
phai gia tri goc. Test bang fake cursor mo phong dung cac bang lien quan."""
import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_anomaly_projection.py"
_spec = importlib.util.spec_from_file_location("reconcile_anomaly_projection", _SCRIPT_PATH)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)

compute_expected_true_ids = mod.compute_expected_true_ids
find_mismatches = mod.find_mismatches
compute_active_resolution_checksum = mod.compute_active_resolution_checksum
compute_anomaly_projection_checksum = mod.compute_anomaly_projection_checksum


class _FakeCursor:
    """price_observations: list[{"record_id","is_anomaly"}]
    resolutions: list[{"source_record_id","review_id","source_code"}] (da JOIN san voi decision active)
    """

    def __init__(self, price_observations, active_resolutions):
        self._price_observations = price_observations
        self._active_resolutions = active_resolutions  # da loc san state='active'
        self._next = None

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        if "anomaly_review_resolutions" in norm and "exclude_from_train" in norm:
            source_code = params[0]
            self._next = [
                {"source_record_id": r["source_record_id"]}
                for r in self._active_resolutions if r["source_code"] == source_code
            ]
        elif "r.source_record_id, r.review_id" in norm:
            source_code = params[0]
            self._next = [
                {"source_record_id": r["source_record_id"], "review_id": r["review_id"]}
                for r in self._active_resolutions if r["source_code"] == source_code
            ]
        elif "is_anomaly = TRUE" in norm:
            self._next = [{"record_id": r["record_id"]} for r in self._price_observations if r["is_anomaly"]]
        elif norm.startswith("SELECT record_id, is_anomaly FROM price_observations"):
            self._next = list(self._price_observations)
        else:
            raise AssertionError(f"unexpected SQL in fake cursor: {norm[:80]}")

    def fetchall(self):
        return self._next


def test_compute_expected_true_ids_filters_by_source_code():
    cursor = _FakeCursor(
        price_observations=[],
        active_resolutions=[
            {"source_record_id": 1, "review_id": "r1", "source_code": "local_primary"},
            {"source_record_id": 2, "review_id": "r2", "source_code": "vps"},
        ],
    )
    ids = compute_expected_true_ids(cursor, "local_primary")
    assert ids == {1}


def test_find_mismatches_detects_both_directions():
    cursor = _FakeCursor(
        price_observations=[
            {"record_id": 1, "is_anomaly": False},  # phai TRUE (thieu)
            {"record_id": 2, "is_anomaly": True},    # dung
            {"record_id": 3, "is_anomaly": True},    # phai FALSE (thua - stale v1)
        ],
        active_resolutions=[],
    )
    should_true, should_false = find_mismatches(cursor, expected_true_ids={1, 2})
    assert should_true == [1]
    assert should_false == [3]


def test_find_mismatches_empty_when_fully_reconciled():
    cursor = _FakeCursor(
        price_observations=[{"record_id": 1, "is_anomaly": True}, {"record_id": 2, "is_anomaly": False}],
        active_resolutions=[],
    )
    should_true, should_false = find_mismatches(cursor, expected_true_ids={1})
    assert should_true == []
    assert should_false == []


def test_active_resolution_checksum_deterministic_regardless_of_row_order():
    cursor_a = _FakeCursor([], [
        {"source_record_id": 1, "review_id": "r1", "source_code": "local_primary"},
        {"source_record_id": 2, "review_id": "r2", "source_code": "local_primary"},
    ])
    cursor_b = _FakeCursor([], [
        {"source_record_id": 2, "review_id": "r2", "source_code": "local_primary"},
        {"source_record_id": 1, "review_id": "r1", "source_code": "local_primary"},
    ])
    assert (compute_active_resolution_checksum(cursor_a, "local_primary")
            == compute_active_resolution_checksum(cursor_b, "local_primary"))


def test_anomaly_projection_checksum_only_counts_true_rows():
    cursor = _FakeCursor(
        price_observations=[
            {"record_id": 1, "is_anomaly": True}, {"record_id": 2, "is_anomaly": False},
            {"record_id": 3, "is_anomaly": True},
        ],
        active_resolutions=[],
    )
    checksum = compute_anomaly_projection_checksum(cursor)
    cursor2 = _FakeCursor(
        price_observations=[{"record_id": 1, "is_anomaly": True}, {"record_id": 3, "is_anomaly": True}],
        active_resolutions=[],
    )
    assert checksum == compute_anomaly_projection_checksum(cursor2)
