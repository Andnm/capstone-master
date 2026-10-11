"""Workflow Ph1-only (90a/90b + ruling thuc thi cua GPT): archive live Q0 (1 lan, khong ghi de) -> start (handshake) -> [rc0] evidence -> [rc0] ph1 (check-only + toi da MOT invocation 90a),
boc TOAN BO chuoi start..ph1 trong finally: yeu cau stop + xac nhan ket thuc, roi collect. Khong retry; khong stage nao chay lai; khong dung/ghi de tep Q0 ngoai archival move da bind.
Khac driver Q0: `collect` that bai/ngoai le => nonzero (khong che failure cua stage/cleanup; cung khong de collect che chung).
Ma thoat: nonzero neu bat ky stage nao nonzero, cleanup khong xac nhan, collect that bai, hoac driver bi ngoai le."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

RUN = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
sys.path.insert(0, str(RUN))
import operator_ph1 as op  # noqa: E402

OPERATOR_PH1_SHA256 = "e8c59eb0c08fd4144dee51728f5c48dbdfdc7f3246a8f933ecf2dc7e5a99873d"
ACK = RUN / "ph1_dossier_outer_cleanup_ack.md"
RESULT = RUN / "ph1_workflow_result.json"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_ack(operator_module=op, ack_path: Path = ACK, driver_path: str = __file__) -> None:
    m = operator_module
    text = f"""# Ph1 dossier — xác nhận luồng cleanup bao ngoài (90a), ghi TRƯỚC khi `archive_q0_live`/`start` ({m.stamp()})

Tôi (Claude) xác nhận điều kiện bắt buộc của 90a/90b và ruling thực thi của GPT:

- Chuỗi `archive_q0_live` (một lần) → `start (handshake)` → [rc0] `evidence` → [rc0] `ph1` chạy trong MỘT driver (`run_ph1_workflow.py`, SHA `{sha(driver_path)}`) với `try … finally`.
- `finally` luôn: (1) `ensure_stopped("outer_workflow_cleanup")` = STOP + chờ có hạn `stopped` verified-dead (hoặc `refused` xác nhận chưa từng spawn; không ngụy tạo `publisher_stopped`); (2) `collect`. Khác Q0 driver: `collect` lỗi/ngoại lệ ⇒ **nonzero**, và `collect` không che failure của stage/cleanup.
- Áp dụng kể cả khi `start`/`evidence`/Ph1 ném lỗi, bị cancel hoặc trả nonzero, và cả đoạn giữa `Popen` và `start_handshake` (trạng thái `pending` ⇒ STOP + xác nhận kết thúc có hạn).
- Một attempt Ph1 duy nhất; không stage nào chạy lại; không Ph1 phụ/heavy/Q0. Bắt đầu 11:00Z ≤ t < 12:00Z, hết hiệu lực tuyệt đối 13:00Z (20:00 VN).
- Pin (kiểm trước khi chạy; lệch ⇒ không chạy): operator_ph1 `{sha(m.__file__)}`; supervisor `{sha(m.SUPERVISOR)}`; 90a `{sha(m.APPROVAL)}`; 90b `{sha(m.RENEWAL_APPROVAL)}`; profile `{sha(m.PROFILE)}`; plan-file `{sha(m.PLAN)}`; canonical `{m.SHA['plan_canonical']}`; invocation `{m.SHA['invocation']}`; nguồn/interpreter/parent/entry/runner do `operator_ph1.preconditions()` kiểm.
- Tool/classifier từ chối ⇒ dừng, lưu denial, không vòng qua.
"""
    ack_path.write_text(text, encoding="utf-8")


def run_workflow(operator_module=op, *, ack=write_ack, result_path: Path = RESULT, ack_path: Path = ACK, pre_checks=True) -> int:
    m = operator_module
    if pre_checks:
        if sha(m.__file__) != OPERATOR_PH1_SHA256:
            m.log("workflow_refused_pin_drift", files=["operator_ph1.py"])
            return 2
        if ack_path.exists() or result_path.exists():
            m.log("workflow_refused_already_ran", ack=ack_path.exists(), result=result_path.exists())
            return 2
    ack(m, ack_path)
    stages: dict[str, object] = {}
    exit_code = 0
    try:
        rc = m.archive_q0_live()
        stages["archive_q0_live"] = rc
        if rc == 0:
            problems = m.preconditions()
            if problems:
                m.log("workflow_refused_preconditions", problems=problems)
                stages["preconditions"] = problems
                rc = 2
        if rc == 0:
            rc = m.cmd_start()
            stages["start"] = rc
            if rc == 0:
                rc = m.cmd_evidence()
                stages["evidence"] = rc
                if rc == 0:
                    rc = m.cmd_ph1()
                    stages["ph1"] = rc
        exit_code = next((v for v in stages.values() if isinstance(v, int) and v != 0), 0)
        if exit_code == 0 and stages.get("preconditions"):
            exit_code = 2
    except SystemExit as exc:
        stages["system_exit"] = exc.code
        exit_code = exc.code if isinstance(exc.code, int) and exc.code else 3
    except BaseException as exc:                                                                                 # noqa: BLE001  (gom KeyboardInterrupt)
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
            exit_code = 3                                                                                        # cleanup khong xac nhan
        if collect_failed and not exit_code:
            exit_code = 4                                                                                        # collect that bai: nonzero, nhung KHONG ghi de ma cua stage/cleanup da nonzero
        stages["exit_code"] = exit_code
        result_path.write_text(json.dumps(stages, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(run_workflow())
