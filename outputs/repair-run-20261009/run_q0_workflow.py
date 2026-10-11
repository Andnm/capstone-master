"""Workflow Q0 duoc 88 cho phep DUNG MOT lan: start (handshake) -> [rc0] evidence -> [rc0] q0 (check-only + toi da MOT invocation 82a), boc TOAN BO chuoi trong finally:
yeu cau stop + xac nhan ket thuc, roi collect, ke ca khi start/evidence/Q0 nem loi, bi cancel hoac tra nonzero. Khong retry. Khong tu tao lai bat ky stage nao.
Ma thoat: nonzero neu bat ky stage nao nonzero, cleanup khong xac nhan, hoac driver bi ngoai le; `collect` KHONG bao gio che failure."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

RUN = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
sys.path.insert(0, str(RUN))
import operator_q0 as op  # noqa: E402

PINS_88 = {
    op.SUPERVISOR: "1fe241c64871f64b755e5bc6f0ac2bb689d4ab4d6720e2e27cd634ef1ab38d42",
    RUN / "operator_q0.py": "a35fbccc86dcbd57effb986d220b3f52aced5f7bb9fe9ebb0c89d1c055187d70",
    op.APPROVAL: "26083fed93bcd7bf1dca62f5c66726b0c83be3b03bf1de51da90cd2bd70c92ca",
    op.RENEWAL_APPROVAL: "935b8c7c54e0a6ff89881ae15b78babdec2a7aa6c5a857ad1dd54aa181e392c0",
}
ACK = RUN / "q0_dossier_outer_cleanup_ack.md"
RESULT = RUN / "q0_workflow_result.json"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_ack() -> None:
    text = f"""# Q0 dossier — xác nhận luồng cleanup bao ngoài (88), ghi TRƯỚC khi `start` ({op.stamp()})

Tôi (Claude) xác nhận điều kiện bắt buộc của `88-gpt-pass-targeted-renewal87-and-one-explicit-q0-workflow-approval.md`:

- Toàn bộ chuỗi `start (handshake)` → [rc0] `evidence` → [rc0] `q0` chạy trong MỘT driver (`run_q0_workflow.py`, SHA `{sha(__file__)}`) với khối `try … finally`.
- `finally` luôn: (1) `ensure_stopped("outer_workflow_cleanup")` = phát STOP + chờ có hạn `stopped` verified-dead (hoặc `refused` xác nhận chưa từng spawn; không ngụy tạo `publisher_stopped`/verified-dead khi chưa launch); (2) `collect` (SHA artifact). `collect` không che failure: mã thoát = stage nonzero đầu tiên / cleanup không xác nhận / ngoại lệ driver.
- Áp dụng kể cả khi `start`/`evidence`/Q0 ném lỗi, bị cancel hoặc trả nonzero, và cả đoạn `log(supervisor_launched)` nằm giữa `Popen` và `start_handshake` (trạng thái `pending` ⇒ STOP + xác nhận kết thúc có hạn).
- Một attempt mới duy nhất sau pre-start refusal 83; không stage nào chạy lại; không attempt thứ ba. Q0 actual invocation tối đa một lần. Bắt đầu trước 11:00:00Z (18:00 VN), không quá 11:30:00Z.
- Pin 88 (kiểm trước khi start; lệch ⇒ không chạy): supervisor `{sha(op.SUPERVISOR)}`; operator `{sha(RUN / 'operator_q0.py')}`; 82a `{sha(op.APPROVAL)}`; 82c `{sha(op.RENEWAL_APPROVAL)}`; profile/nguồn/interpreter/plan-file/entry/runner do `operator_q0.preconditions()` kiểm.
- Tool/classifier từ chối ⇒ dừng, lưu denial, không vòng qua.
"""
    ACK.write_text(text, encoding="utf-8")


def main() -> int:
    drift = [f"{path.name}" for path, expected in PINS_88.items() if sha(path) != expected]
    if drift:
        op.log("workflow_refused_pin_drift", files=drift)
        return 2
    if ACK.exists() or RESULT.exists():
        op.log("workflow_refused_already_ran", ack=ACK.exists(), result=RESULT.exists())
        return 2
    problems = op.preconditions()
    if problems:
        op.log("workflow_refused_preconditions", problems=problems)
        return 2
    write_ack()
    stages: dict[str, object] = {}
    exit_code = 0
    try:
        rc = op.cmd_start()
        stages["start"] = rc
        if rc == 0:
            rc = op.cmd_evidence()
            stages["evidence"] = rc
            if rc == 0:
                rc = op.cmd_q0()
                stages["q0"] = rc
        exit_code = next((v for v in stages.values() if isinstance(v, int) and v != 0), 0)
    except SystemExit as exc:
        stages["system_exit"] = exc.code
        exit_code = exc.code if isinstance(exc.code, int) and exc.code else 3
    except BaseException as exc:                                                                                 # noqa: BLE001  (gom KeyboardInterrupt)
        stages["driver_exception"] = type(exc).__name__
        exit_code = 3
    finally:
        try:
            stop_rc = op.ensure_stopped("outer_workflow_cleanup")
        except BaseException as exc:                                                                             # noqa: BLE001
            stop_rc = 3
            stages["cleanup_exception"] = type(exc).__name__
        stages["outer_cleanup_stop_rc"] = stop_rc
        try:
            stages["collect_rc"] = op.cmd_collect()
        except BaseException as exc:                                                                             # noqa: BLE001
            stages["collect_exception"] = type(exc).__name__
        if stop_rc and not exit_code:
            exit_code = 3
        stages["exit_code"] = exit_code
        RESULT.write_text(json.dumps(stages, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
