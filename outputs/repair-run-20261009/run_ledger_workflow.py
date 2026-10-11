"""Workflow LEDGER-only (Claude, uy quyen nguoi dung; GPT chua duyet): start (handshake) -> [rc0] evidence -> [rc0] ledger (hai producer doc-chi tuan tu nf1, nf3), boc TOAN BO chuoi trong finally:
yeu cau stop + xac nhan ket thuc, roi collect. Khong retry; khong stage nao chay lai. Ma thoat nonzero neu bat ky stage nao nonzero, cleanup khong xac nhan, collect that bai, hoac driver bi ngoai le.
Ban sao co chu dich cua run_ph1_workflow.py (bo buoc archive Q0)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

TOOLS = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
sys.path.insert(0, str(TOOLS))
import operator_ledger as op  # noqa: E402

OPERATOR_LEDGER_SHA256 = "3adf192979db1176b279dc07a463b7ef4afea6b77d1e32d650b63a3745685797"
ACK = op.RUN / "ld_dossier_outer_cleanup_ack.md"
RESULT = op.RUN / "ld_workflow_result.json"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_ack(operator_module=op, ack_path: Path = ACK, driver_path: str = __file__) -> None:
    m = operator_module
    text = f"""# Ledger dossier — xác nhận luồng cleanup bao ngoài, ghi TRƯỚC khi `start` ({m.stamp()})

Claude (uỷ quyền trực tiếp của người dùng; GPT CHƯA duyệt) xác nhận:

- Chuỗi `preconditions` → `start (handshake)` → [rc0] `evidence` → [rc0] `ledger` (hai producer đọc-chỉ tuần tự nf1 rồi nf3, mỗi cái MỘT lần) chạy trong MỘT driver (`run_ledger_workflow.py`, SHA `{sha(driver_path)}`) với `try … finally`.
- `finally` luôn: (1) `ensure_stopped("outer_workflow_cleanup")` = STOP + chờ có hạn `stopped` verified-dead (hoặc `refused`/chưa từng spawn); (2) `collect`. `collect` lỗi ⇒ nonzero, không che failure của stage/cleanup.
- Một attempt duy nhất; không stage nào chạy lại; không SQL ngoài ba câu của producer; không DDL/ghi DB/SET GLOBAL.
- Pin (kiểm trước khi chạy; lệch ⇒ không chạy): operator_ledger `{sha(m.__file__)}`; supervisor `{sha(m.SUPERVISOR)}`; approval 141f `{sha(m.APPROVAL)}`; profile `{sha(m.PROFILE)}`; producer `{sha(m.PRODUCER)}`; còn lại do `operator_ledger.preconditions()` kiểm.
- Tool/classifier từ chối ⇒ dừng, lưu denial, không vòng qua.
"""
    ack_path.write_text(text, encoding="utf-8")


def run_workflow(operator_module=op, *, ack=write_ack, result_path: Path = RESULT, ack_path: Path = ACK, pre_checks=True) -> int:
    m = operator_module
    if pre_checks:
        if sha(m.__file__) != OPERATOR_LEDGER_SHA256:
            m.log("workflow_refused_pin_drift", files=["operator_ledger.py"])
            return 2
        if ack_path.exists() or result_path.exists():
            m.log("workflow_refused_already_ran", ack=ack_path.exists(), result=result_path.exists())
            return 2
    ack(m, ack_path)
    stages: dict[str, object] = {}
    exit_code = 0
    try:
        problems = m.preconditions()
        if problems:
            m.log("workflow_refused_preconditions", problems=problems)
            stages["preconditions"] = problems
            rc = 2
        else:
            rc = m.cmd_start()
            stages["start"] = rc
            if rc == 0:
                rc = m.cmd_evidence()
                stages["evidence"] = rc
                if rc == 0:
                    rc = m.cmd_ledger()
                    stages["ledger"] = rc
        exit_code = next((v for v in stages.values() if isinstance(v, int) and v != 0), 0)
        if exit_code == 0 and stages.get("preconditions"):
            exit_code = 2
    except SystemExit as exc:
        stages["system_exit"] = exc.code
        exit_code = exc.code if isinstance(exc.code, int) and exc.code else 3
    except BaseException as exc:                                                                                 # noqa: BLE001
        stages["driver_exception"] = type(exc).__name__
        exit_code = 3
    finally:
        try:
            stop_rc = m.ensure_stopped("outer_workflow_cleanup")
        except BaseException as exc:                                                                             # noqa: BLE001
            stop_rc = 3
            stages["cleanup_exception"] = type(exc).__name__
        stages["outer_cleanup_stop_rc"] = stop_rc
        collect_failed = False
        try:
            stages["collect_rc"] = m.cmd_collect()
            collect_failed = bool(stages["collect_rc"])
        except BaseException as exc:                                                                             # noqa: BLE001
            stages["collect_exception"] = type(exc).__name__
            collect_failed = True
        if stop_rc and not exit_code:
            exit_code = 3
        if collect_failed and not exit_code:
            exit_code = 4
        stages["exit_code"] = exit_code
        result_path.write_text(json.dumps(stages, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(run_workflow())
