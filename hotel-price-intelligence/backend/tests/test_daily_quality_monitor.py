"""scripts/ khong phai package (chay truc tiep bang `python scripts/daily_quality_monitor.py`,
khong co __init__.py) nen import bang duong dan file thay vi `import scripts.daily_quality_monitor`.
"""
import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "daily_quality_monitor.py"
_spec = importlib.util.spec_from_file_location("daily_quality_monitor", _SCRIPT_PATH)
daily_quality_monitor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daily_quality_monitor)


def test_classify_dead_link_rows_json_null_is_legacy_not_confirmed():
    rows = [
        {"hotel_name_hint": "Mac Valley", "checkin_date": "2026-09-17", "has_evidence": False, "probe_verdict": None},
    ]
    confirmed, legacy = daily_quality_monitor._classify_dead_link_rows(rows)
    assert confirmed == []
    assert legacy == ["Mac Valley@2026-09-17"]


def test_classify_dead_link_rows_verdict_confirmed_is_confirmed():
    rows = [
        {"hotel_name_hint": "Hotel A", "checkin_date": "2026-09-20", "has_evidence": True, "probe_verdict": "confirmed"},
    ]
    confirmed, legacy = daily_quality_monitor._classify_dead_link_rows(rows)
    assert confirmed == ["Hotel A@2026-09-20"]
    assert legacy == []


def test_classify_dead_link_rows_other_verdict_is_neither_confirmed_nor_legacy():
    # Co evidence (probe that su chay) nhung verdict khac "confirmed" (vd not_confirmed do worker
    # cu bi kill giua chung truoc khi ghi status cuoi) - khong duoc xep vao ca 2 nhom, tranh bao
    # nham la da xac nhan HOAC bao nham la du lieu code cu chua qua probe.
    rows = [
        {"hotel_name_hint": "Hotel B", "checkin_date": "2026-09-21", "has_evidence": True, "probe_verdict": "not_confirmed"},
    ]
    confirmed, legacy = daily_quality_monitor._classify_dead_link_rows(rows)
    assert confirmed == []
    assert legacy == []


def test_classify_dead_link_rows_mixed_batch():
    rows = [
        {"hotel_name_hint": "Confirmed Hotel", "checkin_date": "2026-09-20", "has_evidence": True, "probe_verdict": "confirmed"},
        {"hotel_name_hint": "Legacy Hotel", "checkin_date": "2026-09-17", "has_evidence": False, "probe_verdict": None},
    ]
    confirmed, legacy = daily_quality_monitor._classify_dead_link_rows(rows)
    assert confirmed == ["Confirmed Hotel@2026-09-20"]
    assert legacy == ["Legacy Hotel@2026-09-17"]


# _registry_integrity_gate() - wiring giua check_registry_integrity() va gate cua chinh monitor
# (discuss/anomaly-v2-ground-truth/ file 21 MIN1: truoc chi test check_registry_integrity() tu than
# bang fake cursor, khong co test nao xac nhan chinh monitor gan dung status "warn" khi ok=False).
def test_registry_integrity_gate_ok_true_is_pass():
    check = {"ok": True, "reason": None, "registry_file_sha256": "abc123", "source_code": "local_primary"}
    gate = daily_quality_monitor._registry_integrity_gate(check)
    assert gate["status"] == "pass"
    assert gate["registry_file_sha256"] == "abc123"
    assert "ok" not in gate


def test_registry_integrity_gate_ok_false_is_warn():
    check = {"ok": False, "reason": "chua tung sync", "source_code": "local_primary"}
    gate = daily_quality_monitor._registry_integrity_gate(check)
    assert gate["status"] == "warn"
    assert gate["reason"] == "chua tung sync"


def test_registry_integrity_gate_ok_false_with_drift_errors_is_warn_and_passes_through():
    check = {
        "ok": False, "reason": "da sync thanh cong nhung DB da drift",
        "drift_errors": ["decision 'r1': mong state='active', DB co 'retracted'"],
    }
    gate = daily_quality_monitor._registry_integrity_gate(check)
    assert gate["status"] == "warn"
    assert gate["drift_errors"] == ["decision 'r1': mong state='active', DB co 'retracted'"]
