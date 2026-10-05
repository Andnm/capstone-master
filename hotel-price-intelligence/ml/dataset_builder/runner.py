"""State machine + crash recovery + resume cho `build_dataset` (spec muc 18).

    build_dataset --dataset-version X --apply
    build_dataset --dataset-version X --rebuild-from STEP
    build_dataset --dataset-version X --retry-failed-step --reason ... --actor ...

Quy tac chinh: advisory lock `dataset_build:<version>` (khong chay song song 2 builder cung version, mat
connection thi MySQL tu nha lock); marker `active_step` ghi TRUOC khi ghi output; crash giua step -> lan
`--apply` sau cleanup (ca downstream) roi chay lai tron step, khong append vao du lieu do dang; circuit-breaker
`max_step_attempts`; heartbeat thread rieng (connection rieng) moi 60 giay; manifest PASS -> `--apply` no-op.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from . import env
from .cleanup import cleanup_from
from .db import connect, scalar, utc_now
from .manifest import (
    STEPS, ManifestError, append_retry_override, begin_retry_attempt, complete_step, fail_step, heartbeat_is_stale,
    load_manifest, mark_pass, next_step, start_step, verify_manifest, write_recovery_marker,
)

HEARTBEAT_SECONDS = 60
DATASET_BUILD_STALE_SECONDS = 300


class LockHeldError(RuntimeError):
    pass


class CircuitOpenError(RuntimeError):
    pass


class BuildFailedError(RuntimeError):
    pass


@dataclass
class StepContext:
    database: str
    dataset_version: str
    conn: Any
    config: dict[str, Any]
    output_root: Path
    reports: dict[str, Any] = field(default_factory=dict)

    @property
    def report_dir(self) -> Path:
        return self.output_root / "_reports" / self.dataset_version

    def save_report(self, step: str, report: dict[str, Any]) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"{step}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(path)


StepFunction = Callable[[StepContext], dict[str, Any]]


def lock_name(dataset_version: str) -> str:
    return f"dataset_build:{dataset_version}"


@contextmanager
def advisory_lock(database: str, dataset_version: str) -> Iterator[None]:
    """Lock tren connection RIENG giu suot lan chay; dong connection = MySQL tu nha lock (spec muc 18 buoc 8)."""
    with connect(database) as lock_conn:
        got = scalar(lock_conn, "SELECT GET_LOCK(%s, 0)", (lock_name(dataset_version),))
        if got != 1:
            raise LockHeldError(f"{lock_name(dataset_version)} dang bi giu - khong chay builder thu hai cho cung version.")
        try:
            yield
        finally:
            try:
                scalar(lock_conn, "SELECT RELEASE_LOCK(%s)", (lock_name(dataset_version),))
            except Exception:  # noqa: BLE001 - dong connection van nha lock
                pass


class _Heartbeat(threading.Thread):
    """Cap nhat `active_step_heartbeat_at` co dieu kien dung version + step (spec muc 18); loi khong lam chet build."""

    def __init__(self, database: str, dataset_version: str, step: str, interval: float) -> None:
        super().__init__(daemon=True, name=f"heartbeat-{dataset_version}")
        self.database, self.dataset_version, self.step, self.interval = database, dataset_version, step, interval
        self.stop_event = threading.Event()
        self.failures = 0

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                with connect(self.database) as conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE dataset_build_manifests SET active_step_heartbeat_at=%s "
                                   "WHERE dataset_version=%s AND active_step=%s", (utc_now(), self.dataset_version, self.step))
                    conn.commit()
                    cursor.close()
            except Exception:  # noqa: BLE001
                self.failures += 1

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=5)


def _default_steps() -> dict[str, StepFunction]:
    from .steps import STEP_FUNCTIONS
    return dict(STEP_FUNCTIONS)


def _execute_step(ctx: StepContext, step: str, steps: dict[str, StepFunction], *, heartbeat_seconds: float) -> dict[str, Any]:
    """Chay 1 step da co marker `active_step`; thanh cong -> complete (hoac mark_pass cho validation); loi -> fail_step."""
    beat = _Heartbeat(ctx.database, ctx.dataset_version, step, heartbeat_seconds)
    beat.start()
    started = time.monotonic()
    try:
        function = steps[step]
        report = function(ctx)
        report = {**report, "elapsed_s": round(time.monotonic() - started, 1), "step": step}
        ctx.reports[step] = report
        ctx.save_report(step, report)
    except Exception as exc:  # noqa: BLE001 - ghi nguyen nhan vao manifest roi nem lai
        try:
            ctx.conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        fail_step(ctx.conn, ctx.dataset_version, f"{step}: {type(exc).__name__}: {exc}")
        ctx.save_report(step, {"step": step, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]})
        raise
    finally:
        beat.stop()
    if step == "validation":
        if not report.get("ok", False):
            fail_step(ctx.conn, ctx.dataset_version, f"validation: {report.get('failed')}")
            raise BuildFailedError(f"validation FAIL: {report.get('failed')}")
        mark_pass(ctx.conn, ctx.dataset_version)
    else:
        complete_step(ctx.conn, ctx.dataset_version, step)
    return report


def _loop(ctx: StepContext, steps: dict[str, StepFunction], *, stop_after: str | None, heartbeat_seconds: float,
          resume_step: str | None = None) -> dict[str, Any]:
    pending = resume_step
    while True:
        if pending is not None:
            step, pending = pending, None
        else:
            row = load_manifest(ctx.conn, ctx.dataset_version)
            ctx.conn.commit()
            if row["status"] == "pass":
                break
            if row["active_step"]:
                step = row["active_step"]
                if row["active_step_attempt"] >= row["max_step_attempts"]:
                    fail_step(ctx.conn, ctx.dataset_version,
                              f"circuit-breaker mo: {step} da chay {row['active_step_attempt']}/{row['max_step_attempts']} lan; "
                              f"sua nguyen nhan roi dung --retry-failed-step --reason ... --actor ...")
                    raise CircuitOpenError(f"{step}: {row['active_step_attempt']} lan that bai >= {row['max_step_attempts']}")
                begin_retry_attempt(ctx.conn, ctx.dataset_version)
                cleanup_from(ctx.conn, ctx.dataset_version, step, output_root=ctx.output_root)
            else:
                step = next_step(row["last_completed_step"])
                if step is None:
                    break
                start_step(ctx.conn, ctx.dataset_version, step)
        _execute_step(ctx, step, steps, heartbeat_seconds=heartbeat_seconds)
        if stop_after == step:
            break
    return load_manifest(ctx.conn, ctx.dataset_version)


@contextmanager
def _session(database: str, dataset_version: str, output_root: Path | None) -> Iterator[StepContext]:
    with advisory_lock(database, dataset_version):
        with connect(database) as conn:
            row = load_manifest(conn, dataset_version, for_update=True)
            config = verify_manifest(conn, row)
            conn.commit()
            yield StepContext(database=database, dataset_version=dataset_version, conn=conn, config=config,
                              output_root=output_root or env.DATASET_OUTPUT_ROOT)


def apply(database: str, dataset_version: str, *, steps: dict[str, StepFunction] | None = None,
          output_root: Path | None = None, stop_after: str | None = None,
          heartbeat_seconds: float = HEARTBEAT_SECONDS) -> dict[str, Any]:
    """`--apply`: chay/resume step ke tiep; manifest PASS -> no-op. Tra ve manifest cuoi."""
    if stop_after is not None and stop_after not in STEPS:
        raise ValueError(f"stop_after {stop_after!r} khong hop le")
    with _session(database, dataset_version, output_root) as ctx:
        row = load_manifest(ctx.conn, dataset_version)
        ctx.conn.commit()
        if row["status"] == "pass":                       # no-op sau khi kiem lai file/hash (spec muc 18 buoc 7)
            from .validation import verify_pass_outputs
            problems = verify_pass_outputs(ctx.conn, dataset_version=dataset_version, config=ctx.config, manifest=row,
                                           output_root=ctx.output_root)
            if problems:
                raise ManifestError("manifest PASS nhung output khong con khop: " + "; ".join(problems))
            return row
        return _loop(ctx, steps or _default_steps(), stop_after=stop_after, heartbeat_seconds=heartbeat_seconds)


def rebuild_from(database: str, dataset_version: str, step: str, *, steps: dict[str, StepFunction] | None = None,
                 output_root: Path | None = None, stop_after: str | None = None,
                 heartbeat_seconds: float = HEARTBEAT_SECONDS) -> dict[str, Any]:
    """`--rebuild-from STEP`: ghi recovery marker TRUOC cleanup, don STEP + downstream roi build lai (khong am tham)."""
    if step not in STEPS:
        raise ValueError(f"step {step!r} khong hop le")
    with _session(database, dataset_version, output_root) as ctx:
        row = load_manifest(ctx.conn, dataset_version)
        if row["active_step"]:
            raise ManifestError("manifest co active_step khac - phuc hoi step do dang bang --apply truoc khi --rebuild-from.")
        write_recovery_marker(ctx.conn, dataset_version, step)
        cleanup_from(ctx.conn, dataset_version, step, output_root=ctx.output_root)
        return _loop(ctx, steps or _default_steps(), stop_after=stop_after, heartbeat_seconds=heartbeat_seconds, resume_step=step)


def retry_failed_step(database: str, dataset_version: str, *, reason: str, actor: str,
                      steps: dict[str, StepFunction] | None = None, output_root: Path | None = None,
                      stop_after: str | None = None, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> dict[str, Any]:
    """`--retry-failed-step`: sau khi circuit-breaker mo va nguyen nhan da sua. Bat buoc reason/actor; khong doi config."""
    if not reason.strip() or not actor.strip():
        raise ValueError("--reason va --actor la bat buoc.")
    with _session(database, dataset_version, output_root) as ctx:
        row = load_manifest(ctx.conn, dataset_version)
        step = row["active_step"]
        if not step:
            raise ManifestError("khong co active_step de retry.")
        now = utc_now()
        entry = {"step": step, "previous_attempts": row["active_step_attempt"], "reason": reason, "actor": actor,
                 "at": now.isoformat() + "Z", "code_version": _code_version()}
        append_retry_override(ctx.conn, dataset_version, entry, now=now)
        cleanup_from(ctx.conn, dataset_version, step, output_root=ctx.output_root)
        return _loop(ctx, steps or _default_steps(), stop_after=stop_after, heartbeat_seconds=heartbeat_seconds, resume_step=step)


def _code_version() -> str:
    from . import BUILDER_VERSION
    return BUILDER_VERSION


def describe(database: str, dataset_version: str) -> dict[str, Any]:
    """Trang thai hien thi: `stale` suy ra tu heartbeat; kiem IS_USED_LOCK truoc khi ket luan process da chet."""
    with connect(database) as conn:
        row = load_manifest(conn, dataset_version)
        locked = scalar(conn, "SELECT IS_USED_LOCK(%s)", (lock_name(dataset_version),)) is not None
        conn.commit()
    stale = heartbeat_is_stale(row, now=utc_now(), stale_seconds=DATASET_BUILD_STALE_SECONDS)
    return {"dataset_version": dataset_version, "status": row["status"], "last_completed_step": row["last_completed_step"],
            "active_step": row["active_step"], "active_step_attempt": row["active_step_attempt"],
            "heartbeat_stale": stale, "lock_held": locked,
            "recoverable": bool(stale and not locked) or bool(row["status"] == "fail" and row["active_step"])}
