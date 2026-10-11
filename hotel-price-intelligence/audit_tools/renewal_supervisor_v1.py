"""Supervisor/operator cho MOT publisher gia han evidence (82c): khoi dong DUNG mot process, moi truong hermetic 6 bien, stdout/stderr ghi THANG vao tep (khong pipe chua doc), hard wall deadline,
dung/reap/xac minh chet CHINH process do minh tao. Khong KILL phien DB/crawler/worker, khong tim process theo ten/glob.

    python renewal_supervisor_v1.py --profile <renewal_profile_*.json> --expected-profile-sha256 H --expected-source-sha256 H --expected-interpreter-sha256 H
        --not-before-utc 2026-10-09T10:00:00Z --start-deadline-utc 2026-10-09T11:00:00Z --hard-not-after-utc 2026-10-09T11:30:00Z --max-pool-size 5
        --supervisor-log <jsonl> --publisher-log <tep moi> --stop-file <tep (chua ton tai)>

Nhat ky JSON-lines: moi su kien mot dong. Ma thoat: 0 publisher ket thuc dung quy trinh + verified-dead; 2 tu choi truoc khi khoi dong (khong process nao duoc tao); 3 dung nhung KHONG xac minh duoc chet.
Dung hoac huy bang cach tao `--stop-file`. TRANG THAI: cong cu van hanh; test voi child gia (khong DB).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

CREATE_NO_WINDOW = 0x08000000
ENV_KEYS = {"SystemRoot", "TEMP", "TMP", "PATH", "PYTHONUTF8", "PYTHONIOENCODING"}
REAP_WAIT_S = 5.0
POOL_CHECK_SNIPPET = "import os, sys; sys.path.insert(0, os.getcwd()); from app.core.config import settings; print(int(settings.DB_POOL_SIZE))"                  # CHI doc so nguyen cau hinh; khong import app.core.database (tao pool), khong in gi khac


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_utc(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def creation_time_utc(pid: int) -> str | None:
    """Thoi diem tao process (Windows, GetProcessTimes); None neu khong lay duoc (ghi nhan, khong chan)."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)                                                       # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel_t, user_t = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_t), ctypes.byref(user_t)):
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return (dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=ticks // 10)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        finally:
            kernel.CloseHandle(handle)
    except Exception:                                                                                         # noqa: BLE001
        return None


class Log:
    def __init__(self, path: str | Path, clock: Callable[[], dt.datetime] = utc_now) -> None:
        self.path, self.clock = Path(path), clock

    def event(self, kind: str, **fields: Any) -> None:
        line = json.dumps({"at_utc": self.clock().strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event": kind, **fields}, ensure_ascii=False, sort_keys=True, default=str)
        with open(self.path, "ab") as handle:                                                                 # noi tiep, khong ghi de
            handle.write((line + "\n").encode("utf-8"))


def validate_profile(profile: Mapping[str, Any], *, expected_source_sha256: str, expected_interpreter_sha256: str) -> list[str]:
    problems: list[str] = []
    if profile.get("kind") != "renewal_publisher_profile":
        problems.append("profile_kind")
    argv = profile.get("argv")
    if not (isinstance(argv, list) and len(argv) == 10 and all(isinstance(a, str) and a for a in argv)):
        return problems + ["profile_argv_shape"]
    outputs = profile.get("outputs") or {}
    if not ([argv[2], argv[4], argv[6], argv[8]] == ["--out", "--renew-seconds", "--interval", "--archive-dir"] and argv[3] == outputs.get("live") and argv[9] == outputs.get("archive_dir")
            and argv[5] == str(profile.get("renew_seconds")) and argv[7] == str(profile.get("interval_s"))):
        problems.append("profile_argv_flags")
    if profile.get("source", {}).get("sha256") != expected_source_sha256 or sha256_file(profile["source"]["path"]) != expected_source_sha256 or argv[1] != profile["source"]["path"]:
        problems.append("source_sha256")
    if sha256_file(argv[0]) != expected_interpreter_sha256:
        problems.append("interpreter_sha256")
    if set(profile.get("env", {})) != ENV_KEYS:
        problems.append("profile_env_keys")
    return problems


def hermetic_env(profile: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(profile["env"][key]) for key in sorted(ENV_KEYS)}


def check_pool_size(profile: Mapping[str, Any], max_pool: int, *, run: Callable[..., Any] = subprocess.run) -> tuple[int | None, str | None]:
    """Chay mot child hermetic doc DUNG so nguyen DB_POOL_SIZE cua Settings (cwd backend, `.env` do Settings nap); khong in/luu gia tri nao khac. (so, ly do tu choi)."""
    done = run([profile["argv"][0], "-I", "-c", POOL_CHECK_SNIPPET], cwd=profile["cwd"], env=hermetic_env(profile), capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
    try:
        value = int(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, "pool_size_unreadable"
    if done.returncode != 0:
        return value, "pool_size_check_failed"
    return (value, None) if 1 <= value <= max_pool else (value, "pool_size_above_scope")


def reap(child: Any, log: Log) -> tuple[bool, bool]:
    """Giet (neu con song) + wait <= REAP_WAIT_S + xac minh chet CHINH `child` do wrapper nam handle. (killed, verified_dead). Loi ghi log khong duoc bo qua buoc giet."""
    killed = False
    if child.poll() is None:
        try:
            child.kill()
            killed = True
        except OSError as exc:
            try:
                log.event("kill_error", error=f"{type(exc).__name__}: {exc}")
            except Exception:                                                                                 # noqa: BLE001
                pass
        try:
            child.wait(timeout=REAP_WAIT_S)
        except Exception:                                                                                     # noqa: BLE001
            pass
    return killed, child.poll() is not None


def refusal_reasons(profile: Mapping[str, Any], args: argparse.Namespace, now: dt.datetime) -> list[str]:
    reasons: list[str] = []
    if not (parse_utc(args.not_before_utc) <= now < parse_utc(args.start_deadline_utc)):
        reasons.append("outside_start_window")
    if now >= parse_utc(args.hard_not_after_utc):
        reasons.append("after_hard_not_after")
    if Path(args.stop_file).exists():
        reasons.append("stop_file_already_exists")
    if Path(args.publisher_log).exists():
        reasons.append("publisher_log_already_exists")
    archive = Path(profile["outputs"]["archive_dir"])
    if archive.exists() and any(archive.iterdir()):
        reasons.append("archive_dir_not_empty")
    if Path(profile["outputs"]["live"]).exists():
        reasons.append("live_evidence_already_exists")
    return reasons


def supervise(profile: Mapping[str, Any], args: argparse.Namespace, *, log: Log, clock: Callable[[], dt.datetime] = utc_now, sleep: Callable[[float], None] = time.sleep,
              popen: Callable[..., Any] = subprocess.Popen, pool_check: Callable[..., tuple[int | None, str | None]] = check_pool_size) -> int:
    problems = validate_profile(profile, expected_source_sha256=args.expected_source_sha256, expected_interpreter_sha256=args.expected_interpreter_sha256)
    if problems:
        log.event("refused", reasons=problems)
        return 2
    reasons = refusal_reasons(profile, args, clock())
    if reasons:
        log.event("refused", reasons=reasons)
        return 2
    pool, pool_reason = pool_check(profile, args.max_pool_size)
    log.event("pool_size_checked", pool_size=pool, limit=args.max_pool_size)                               # CHI so nguyen
    if pool_reason:
        log.event("refused", reasons=[pool_reason])
        return 2
    reasons = refusal_reasons(profile, args, clock())                                                         # 84: cua so/stop/dau ra co the doi trong luc kiem pool -> kiem lai SAU pool-check
    if reasons:
        log.event("refused", reasons=reasons, stage="after_pool_check")
        return 2
    Path(profile["outputs"]["archive_dir"]).mkdir(parents=True, exist_ok=True)
    started = clock()
    reasons = refusal_reasons(profile, args, started)                                                         # 84: va NGAY truoc spawn (spawn = 0 neu het cua so)
    if reasons:
        log.event("refused", reasons=reasons, stage="before_spawn")
        return 2
    deadline = min(started + dt.timedelta(seconds=int(profile["renew_seconds"])), parse_utc(args.hard_not_after_utc))
    reason, killed, verified_dead = None, False, False
    with open(args.publisher_log, "xb") as sink:                                                            # file handle truc tiep: khong pipe chua doc nen khong the lam process ket
        child = popen(profile["argv"], cwd=profile["cwd"], env=hermetic_env(profile), stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        try:
            log.event("started", pid=child.pid, creation_time_utc=creation_time_utc(child.pid), argv=profile["argv"], profile_sha256=args.expected_profile_sha256, source_sha256=args.expected_source_sha256,
                      interpreter_sha256=args.expected_interpreter_sha256, wall_deadline_utc=deadline.strftime("%Y-%m-%dT%H:%M:%SZ"), publisher_log=str(args.publisher_log))
            while reason is None:
                sleep(1.0)
                if child.poll() is not None:
                    reason = "exited"
                elif Path(args.stop_file).exists():
                    reason = "stop_requested"
                elif clock() >= deadline:
                    reason = "wall_deadline"
        finally:                                                                                              # 84: moi duong thoat (loi log, KeyboardInterrupt, ...) deu giet+reap CHINH process do minh tao
            killed, verified_dead = reap(child, log)
            in_flight = sys.exc_info()[1] is not None
            try:
                log.event("stopped", reason=reason or "supervisor_exception", killed_by_supervisor=killed, exit_code=child.poll(), verified_dead=verified_dead, publisher_log_sha256=sha256_file(args.publisher_log),
                          live_evidence_sha256=sha256_file(profile["outputs"]["live"]) if Path(profile["outputs"]["live"]).exists() else None,
                          archive_files=sorted(p.name for p in Path(profile["outputs"]["archive_dir"]).glob("*.json")))
            except Exception:                                                                                 # noqa: BLE001
                if not in_flight:
                    raise                                                                                     # khong nuot loi ghi log o duong binh thuong; duong loi giu nguyen ngoai le goc
    return 0 if verified_dead else 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", type=Path, required=True)
    for flag in ("expected-profile-sha256", "expected-source-sha256", "expected-interpreter-sha256", "not-before-utc", "start-deadline-utc", "hard-not-after-utc"):
        parser.add_argument(f"--{flag}", required=True)
    parser.add_argument("--max-pool-size", type=int, required=True)
    parser.add_argument("--supervisor-log", type=Path, required=True)
    parser.add_argument("--publisher-log", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    args = parser.parse_args(argv)
    log = Log(args.supervisor_log)
    if sha256_file(args.profile) != args.expected_profile_sha256:
        log.event("refused", reasons=["profile_sha256"])
        return 2
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    return supervise(profile, args, log=log)


if __name__ == "__main__":
    raise SystemExit(main())
