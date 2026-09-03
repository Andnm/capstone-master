"""Restart the durable crawler worker after a crash or stale heartbeat."""
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from app.core.config import settings


def heartbeat_file_is_stale(
    heartbeat_path: Path,
    *,
    child_started_at: float,
    now: float,
    timeout_seconds: int,
) -> bool:
    """Return True when a child has not emitted a heartbeat before the deadline."""
    try:
        last_heartbeat = heartbeat_path.stat().st_mtime
    except OSError:
        last_heartbeat = child_started_at
    return now - max(child_started_at, last_heartbeat) > timeout_seconds


def _spawn_child(
    worker_script: Path,
    backend_root: Path,
    heartbeat_path: Path,
    log_stream,
):
    child_env = os.environ.copy()
    child_env["WORKER_WATCHDOG_HEARTBEAT_FILE"] = str(heartbeat_path)
    kwargs = {
        "cwd": str(backend_root),
        "env": child_env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, str(worker_script), "--child"],
        stdout=log_stream,
        stderr=log_stream,
        **kwargs,
    )


def _emit(log_stream, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} {message}"
    print(line, flush=True)
    print(line, file=log_stream, flush=True)


def _terminate_process_tree(process, timeout_seconds: int = 15) -> None:
    """Terminate only this worker child and its Selenium descendants."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def supervise_worker(worker_script: Path, backend_root: Path) -> int:
    """Keep one worker child alive and replace it if heartbeat becomes stale."""
    timeout_seconds = max(
        settings.WORKER_HANG_TIMEOUT_SECONDS,
        settings.WORKER_LEASE_SECONDS + 30,
    )
    poll_seconds = max(1, settings.WORKER_SUPERVISOR_POLL_SECONDS)
    base_restart_delay = max(1, settings.WORKER_RESTART_DELAY_SECONDS)
    restart_delay = base_restart_delay
    max_restart_delay = max(base_restart_delay, settings.WORKER_RESTART_MAX_DELAY_SECONDS)
    artifact_root = Path(settings.ARTIFACT_DIR)
    if not artifact_root.is_absolute():
        artifact_root = backend_root / artifact_root
    artifact_root.mkdir(parents=True, exist_ok=True)
    supervisor_log = artifact_root / "worker_supervisor.log"

    with supervisor_log.open("a", encoding="utf-8", buffering=1) as log_stream:
        while True:
            heartbeat_path = Path(tempfile.gettempdir()) / (
                f"hotel-worker-{os.getpid()}-{uuid.uuid4().hex}.heartbeat"
            )
            child_started_at = time.time()
            child = _spawn_child(worker_script, backend_root, heartbeat_path, log_stream)
            _emit(log_stream, f"[supervisor] worker child started: pid={child.pid}")
            stale = False
            try:
                while child.poll() is None:
                    time.sleep(poll_seconds)
                    if heartbeat_file_is_stale(
                        heartbeat_path,
                        child_started_at=child_started_at,
                        now=time.time(),
                        timeout_seconds=timeout_seconds,
                    ):
                        stale = True
                        _emit(
                            log_stream,
                            f"[supervisor] heartbeat stale for >{timeout_seconds}s; "
                            f"terminating worker tree pid={child.pid}",
                        )
                        _terminate_process_tree(child)
                        break
            except KeyboardInterrupt:
                _emit(log_stream, "[supervisor] stopping worker tree")
                _terminate_process_tree(child)
                return 0
            finally:
                heartbeat_path.unlink(missing_ok=True)

            # A child that ran longer than one watchdog window was healthy before
            # this isolated failure, so do not carry crash-loop backoff forever.
            if time.time() - child_started_at >= timeout_seconds:
                restart_delay = base_restart_delay
            return_code = child.poll()
            reason = "stale heartbeat" if stale else f"exit code {return_code}"
            _emit(
                log_stream,
                f"[supervisor] worker stopped ({reason}); restarting in {restart_delay}s",
            )
            time.sleep(restart_delay)
            restart_delay = min(max_restart_delay, restart_delay * 2)
