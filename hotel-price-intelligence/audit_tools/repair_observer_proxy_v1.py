"""BoundedObserver — proxy giao dien observer (`capture_baseline`/`probe`/`selftest`/`close`) toi mot WORKER process rieng, moi lenh co timeout CUNG: het gio => giet cay worker (Job Object rieng, kill-on-close)
va nem `ObserverTimeout`; sau do worker coi nhu chet (moi lenh sau nem `ObserverWorkerDead`). Cho phep runner khong bi treo bat dinh boi mot cuoc goi DB/socket khong tra ve (hint MAX_EXECUTION_TIME cua server
khong bao dam round-trip). KHONG KILL DB, KHONG retry. Worker chay trong Job de khong the de lai process mo coi.

TRANG THAI: THIET KE + MA + TEST voi worker gia (python con cuc bo, khong DB). Chi Windows (Job Object). Khe race: worker co the sinh con TRUOC khi duoc gan vao job (worker la ma cua ta, khong sinh con).
"""
from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import threading
import time
import types
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000
MAX_LINE = 1_000_000
DEFAULT_CAPS = {"start": 20.0, "baseline": 20.0, "probe": 15.0, "selftest": 120.0, "close": 2.0}
TEARDOWN_WAIT_S = 5.0                                                                                      # cho toi da de XAC MINH root worker da chet sau khi giet


class ObserverTimeout(Exception):
    pass


class ObserverWorkerDead(Exception):
    pass


class ObserverProtocolError(Exception):
    pass


class ObserverRemoteError(Exception):
    pass


class ObserverCleanupError(ObserverWorkerDead):
    """Root worker chua duoc XAC MINH chet sau teardown (tham chieu Popen duoc GIU, khong bao 'da don')."""


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


class BoundedObserver:
    def __init__(self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str], stderr_path: str | Path, caps: Mapping[str, float] | None = None, wj=None, max_line: int = MAX_LINE) -> None:
        self.argv, self.cwd, self.env, self.stderr_path = list(argv), str(cwd), dict(env), Path(stderr_path)
        self.caps = {**DEFAULT_CAPS, **(caps or {})}
        for key, value in self.caps.items():
            if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 3600):
                raise ValueError(f"cap_invalid: {key}={value!r}")
        self.max_line = max_line
        self._wj = wj or _load("windows_job_for_observer_proxy", "windows_job_v1.py")
        self.source_sha256 = getattr(self._wj, "__source_sha256__", None)
        self._job = self._proc = self._thread = None
        self._lines: "queue.Queue[Any]" = queue.Queue()
        self._next_id = 0
        self.broken: str | None = None
        self.cleanup_verified: bool | None = None                                                          # None = chua teardown; True = root da chet (poll != None); False = chua xac minh
        self.calls: list[dict[str, Any]] = []

    @property
    def tooling_sha256(self) -> str:
        """Hash cong cu cua observer co gioi han thoi gian: proxy + worker + observer + model Q + Job (de ghim vao ke hoach)."""
        parts = {"proxy": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "worker": hashlib.sha256((HERE / "repair_observer_worker_v1.py").read_bytes()).hexdigest(),
                 "observer": hashlib.sha256((HERE / "repair_db_observer_v1.py").read_bytes()).hexdigest(), "quiescence_model": hashlib.sha256((HERE / "quiescence_model_v1.py").read_bytes()).hexdigest(),
                 "windows_job": self.source_sha256}
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()

    @property
    def overhead_s(self) -> float:
        """Thoi gian toi da NGOAI cap tung lenh ma mot phase `write` co the ton truoc baseline: worker khoi dong (`start`) + xac minh don dep (`TEARDOWN_WAIT_S`). Runner cong vao ngan sach truoc-baseline."""
        return float(self.caps["start"]) + TEARDOWN_WAIT_S

    # ---- vong doi
    def _start(self) -> None:
        self._job = self._wj.Job()
        try:
            self._stderr = open(self.stderr_path, "xb")
            self._proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, cwd=self.cwd, env=self.env, creationflags=CREATE_NO_WINDOW)
            self._job.assign_process_handle(self._proc._handle)                                            # neu loi o day root KHONG thuoc Job => teardown phai giet chinh root
        except BaseException as exc:
            self.broken = self.broken or "start_failed"                                                    # khong retry, khong worker thu hai
            pid = getattr(self._proc, "pid", None)
            if not self._teardown():
                raise ObserverCleanupError(f"worker root pid={pid} not verified dead after failed start") from exc
            raise
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        ready = self._get(self.caps["start"], "start")
        if not (isinstance(ready, dict) and ready.get("ready") is True):
            self._break("bad_ready")
            raise ObserverProtocolError("worker_did_not_report_ready")

    def _read_loop(self) -> None:
        stream = self._proc.stdout
        try:
            while True:
                line = stream.readline(self.max_line + 1)
                if not line:
                    self._lines.put(("eof", None))
                    return
                if len(line) > self.max_line:
                    self._lines.put(("too_large", None))
                    return
                self._lines.put(("line", line))
        except Exception as exc:                                                                           # noqa: BLE001
            self._lines.put(("error", type(exc).__name__))

    def _get(self, cap: float, what: str) -> Any:
        try:
            kind, payload = self._lines.get(timeout=cap)
        except queue.Empty:
            self._break(f"timeout:{what}")
            raise ObserverTimeout(f"{what}: no answer within {cap}s; worker tree killed") from None
        if kind != "line":
            self._break(kind)
            raise ObserverWorkerDead(f"{what}: worker stream {kind}")
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._break("garbage")
            raise ObserverProtocolError(f"{what}: non-JSON line from worker") from None

    def _teardown(self) -> bool:
        """Giet cay (Job) roi giet CHINH root neu van song (root chua duoc gan vao Job khi assign loi), cho toi da `TEARDOWN_WAIT_S` va XAC MINH bang `poll()`.
        Tra True chi khi root da chet; neu chua xac minh duoc thi GIU tham chieu Popen (khong mat dau vet) va `cleanup_verified=False`."""
        if self._job is not None:
            try:
                self._job.kill_tree()
            except Exception:                                                                              # noqa: BLE001
                pass
            try:
                self._job.close()
            except Exception:                                                                              # noqa: BLE001
                pass
        verified = True
        proc = self._proc
        if proc is not None:
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except Exception:                                                                          # noqa: BLE001
                    pass
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:                                                                              # noqa: BLE001
                pass
            try:
                proc.wait(timeout=TEARDOWN_WAIT_S)
            except Exception:                                                                              # noqa: BLE001
                pass
            try:
                verified = proc.poll() is not None
            except Exception:                                                                              # noqa: BLE001
                verified = False
            if verified:
                self._proc = None
        try:
            self._stderr.close()
        except Exception:                                                                                  # noqa: BLE001
            pass
        self._job = None
        self.cleanup_verified = verified
        return verified

    def _break(self, reason: str) -> None:
        self.broken = self.broken or reason
        self._teardown()

    # ---- goi lenh
    def _call(self, op: str) -> Any:
        if self.broken:
            raise ObserverWorkerDead(f"worker unusable: {self.broken}")
        started = time.monotonic()
        if self._proc is None:
            self._start()
        self._next_id += 1
        request_id = self._next_id
        try:
            self._proc.stdin.write((json.dumps({"id": request_id, "op": op}) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (OSError, ValueError):
            self._break("stdin_closed")
            raise ObserverWorkerDead(f"{op}: cannot write to worker") from None
        answer = self._get(self.caps[op], op)
        self.calls.append({"op": op, "id": request_id, "elapsed_s": round(time.monotonic() - started, 3)})
        if not isinstance(answer, dict) or answer.get("id") != request_id or "ok" not in answer:
            self._break("id_mismatch")
            raise ObserverProtocolError(f"{op}: answer does not match request id {request_id}")
        if answer["ok"] is not True:
            raise ObserverRemoteError(str(answer.get("error"))[:240])
        return answer.get("result")

    def capture_baseline(self) -> Any:
        return self._call("baseline")

    def probe(self) -> Any:
        return self._call("probe")

    def selftest(self) -> Any:
        return self._call("selftest")

    def close(self) -> None:
        if self._proc is not None and not self.broken:
            try:
                self._proc.stdin.write((json.dumps({"id": self._next_id + 1, "op": "close"}) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
                self._get(self.caps["close"], "close")
            except Exception:                                                                              # noqa: BLE001
                pass
        self._teardown()
        self.broken = self.broken or "closed"
