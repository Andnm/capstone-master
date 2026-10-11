"""Workflow HEAVY INIT-ONLY (MOT plan `init` gom BON phase `ph4_init_h1`/`ph4_verify_h1`/`ph4_init_h3`/`ph4_verify_h3` tren DB dich da clone PASS; khong clone, khong build): nap freeze manifest da duoc GPT duyet (SHA ghim; run-root nam
trong freeze) -> preconditions -> start (handshake) -> [rc0] evidence -> [rc0] plan init (check-only + MOT invocation + kiem chuoi receipt bon phase + bao cao verify), boc TOAN BO chuoi trong finally: yeu cau stop + xac nhan ket thuc,
roi collect. Khong retry; khong stage nao chay lai. Giong driver build/q0var/probe/Ph1/heavy: `collect`/ghi ket qua that bai => nonzero nhung KHONG ghi de ma nonzero cua stage/cleanup.
KHONG co quyen thuc thi cho den khi GPT cho phep ro rang (approval + ruling co dieu kien).

    python run_heavy_init_workflow.py --freeze <hi_freeze.json> --freeze-sha256 <sha>"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

RUN_DIR = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009")
sys.path.insert(0, str(RUN_DIR))
import operator_heavy_init as op  # noqa: E402

OPERATOR_HEAVY_INIT_SHA256 = "542f07b8fe18bbf916f36051a77658174f1696766ec4095ab8c6ba080747b1d0"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_ack(operator_module, ack_path: Path, driver_path: str = __file__) -> None:
    m = operator_module
    ack_path.write_text(f"""# Heavy init-only dossier — xác nhận luồng cleanup bao ngoài, ghi TRƯỚC khi `start` ({m.stamp()}; run-root `{m.RUN}`)

Tôi (Claude) xác nhận điều kiện bắt buộc của ruling thực thi của GPT:

- Chuỗi `load_freeze` → `preconditions` → `start (handshake)` → [rc0] `evidence` → [rc0] `plan init` (check-only + MỘT invocation + kiểm chuỗi receipt bốn phase + báo cáo verify) chạy trong MỘT driver (`run_heavy_init_workflow.py`, SHA `{sha(driver_path)}`) với `try … finally`.
- `finally` luôn: (1) `ensure_stopped("outer_workflow_cleanup")` = STOP + chờ có hạn `stopped` verified-dead (hoặc `refused` xác nhận chưa từng spawn; không ngụy tạo `publisher_stopped`); (2) `collect` (lỗi/ngoại lệ ⇒ nonzero, không che failure của stage/cleanup).
- Áp dụng kể cả khi `start`/`evidence`/plan ném lỗi, bị cancel hoặc trả nonzero, và cả đoạn giữa `Popen` và `start_handshake`; lỗi ghi log/báo cáo không bỏ cleanup và không biến failure thành success.
- Một attempt duy nhất cho run-root này; không retry; không clone, không build, không DROP/--drop-existing, không SET GLOBAL/PERSIST; không xoá/sửa metadata `{m.CFG['quarantined_version']}` (QUARANTINE); JSON không dùng để chứng minh process đã chết; outer timeout/thiếu/dở ⇒ FAIL.
- Pin (kiểm trước khi chạy; lệch ⇒ không chạy): freeze `{m.CFG['freeze_sha256']}`; operator `{sha(m.__file__)}`; supervisor `{sha(m.SUPERVISOR)}`; mọi pin còn lại do `operator_heavy_init.preconditions()` kiểm theo freeze.
- Tool/classifier từ chối ⇒ dừng, lưu denial, không vòng qua.
""", encoding="utf-8")


def run_workflow(operator_module=op, *, freeze_path: Path, freeze_sha256: str, ack=write_ack, result_path: Path | None = None, ack_path: Path | None = None, pre_checks: bool = True) -> int:
    m = operator_module
    if pre_checks and sha(m.__file__) != OPERATOR_HEAVY_INIT_SHA256:
        m.log("workflow_refused_pin_drift", files=["operator_heavy_init.py"])
        return 2
    problems = m.load_freeze(freeze_path, freeze_sha256)                                                 # dat run-root (configure) truoc moi duong dan phu thuoc run-root
    if problems:
        m.log("workflow_refused_freeze", problems=problems)
        return 2
    ack_path = ack_path or m.RUN / "hi_dossier_outer_cleanup_ack.md"
    result_path = result_path or m.RUN / "hi_workflow_result.json"
    if pre_checks and (ack_path.exists() or result_path.exists()):
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
            exit_code = 2
        else:
            rc = m.cmd_start()
            stages["start"] = rc
            if rc == 0:
                rc = m.cmd_evidence()
                stages["evidence"] = rc
                if rc == 0:
                    rc = m.cmd_plans()
                    stages["plans"] = rc
            exit_code = next((v for v in stages.values() if isinstance(v, int) and v != 0), 0)
    except SystemExit as exc:
        stages["system_exit"] = exc.code
        exit_code = exc.code if isinstance(exc.code, int) and exc.code else 3
    except BaseException as exc:                                                                         # noqa: BLE001  (gom KeyboardInterrupt)
        stages["driver_exception"] = type(exc).__name__
        exit_code = 3
    finally:
        try:
            stop_rc = m.ensure_stopped("outer_workflow_cleanup")
        except BaseException as exc:                                                                     # noqa: BLE001
            stop_rc = 3
            stages["cleanup_exception"] = type(exc).__name__
        stages["outer_cleanup_stop_rc"] = stop_rc
        collect_failed = False
        try:
            stages["collect_rc"] = m.cmd_collect()
            collect_failed = bool(stages["collect_rc"])
        except BaseException as exc:                                                                     # noqa: BLE001
            stages["collect_exception"] = type(exc).__name__
            collect_failed = True
        if stop_rc and not exit_code:
            exit_code = 3
        if collect_failed and not exit_code:
            exit_code = 4
        stages["exit_code"] = exit_code
        try:
            result_path.write_text(json.dumps(stages, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
        except OSError:
            exit_code = exit_code or 4
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--freeze-sha256", required=True)
    args = parser.parse_args()
    sys.exit(run_workflow(freeze_path=args.freeze, freeze_sha256=args.freeze_sha256))
