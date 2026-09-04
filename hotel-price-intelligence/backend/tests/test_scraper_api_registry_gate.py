"""_raise_if_registry_stale() (app/api/scraper.py) - wiring giua check_registry_integrity() va HTTP
409 cua endpoint export. Test nhe, khong dung FastAPI TestClient/DB that (discuss/anomaly-v2-ground-
truth/ file 21 MIN1: truoc chi doc code de xac nhan wiring dung, khong co regression test nao cho
enforcement point that ("registry stale -> khong tai duoc export") nay).
"""
import pytest
from fastapi import HTTPException

from app.api.scraper import _raise_if_registry_stale


def test_raise_if_registry_stale_ok_true_does_not_raise():
    _raise_if_registry_stale({"ok": True, "reason": None})  # khong raise gi ca


def test_raise_if_registry_stale_ok_false_raises_409_with_reason():
    with pytest.raises(HTTPException) as exc_info:
        _raise_if_registry_stale({"ok": False, "reason": "chua tung sync tren DB nay."})
    assert exc_info.value.status_code == 409
    assert "chua tung sync tren DB nay." in exc_info.value.detail
    assert "sync_anomaly_registry.py --apply" in exc_info.value.detail


def test_raise_if_registry_stale_ok_false_drift_reason_included_in_detail():
    with pytest.raises(HTTPException) as exc_info:
        _raise_if_registry_stale({
            "ok": False,
            "reason": "da sync thanh cong nhung DB HIEN TAI da drift khoi event log (3 loi).",
        })
    assert exc_info.value.status_code == 409
    assert "drift" in exc_info.value.detail
