"""Runner THUAN cho cac phase repair (Ph1/Ph3/Ph4/Ph5/Ph6...): argv-binding, kiem byte truoc launch, ngan sach, containment bang Job Object, tich hop mo hinh quiescence, receipt noi chuoi, quarantine.

TRANG THAI: THIET KE + MA + TEST voi gia lap (fake launcher/observer/clock) va process local that (Job Object). KHONG co CLI, KHONG ket noi DB, KHONG co quyen thuc thi bat ky phase nao.
`DbObserver` that (cac cau SELECT cua Q0/probe) la phan RIENG can GPT duyet; o day chi la giao dien. KHONG BAO GIO tu KILL phien DB: ung vien KILL chi duoc GHI vao receipt de nguoi co quyen xet.

MOT PHASE = MOT process (cay process trong MOT Job Object) + MOT receipt. Nguyen tac:
  * Spec la dict (khong dataclass), argv/cwd/env la HANG so da duyet: `argv_sha256(spec)` = sha256(canonical({argv, cwd, env})) phai bang gia tri trong approval; khong co override CLI sau duyet.
  * Moi file khai bao trong `required_files` duoc BAM LAI ngay truoc launch (script + phu thuoc); lech 1 byte => REFUSED, khong chay.
  * Cong (cua so gio + crawler idle) va ngan sach (deadline phase + bien an toan + tong ngan sach) duoc kiem truoc launch.
  * Child chay trong Job Object (kill-on-close, khong breakaway); sau khi root thoat, job PHAI rong (chung minh containment) truoc khi dung bang chung DB; neu khong => QUARANTINE.
  * Phase DB: bat baseline (id/uptime/user) TRUOC launch; SAU khi job rong chay probe(s) cach nhau >= MIN_SPACING_S; `evaluate_quiescence` != VERIFIED => QUARANTINE.
  * Receipt `xb` (khong ghi de), moi receipt mang SHA cua receipt truoc (chuoi); phase tiep chi chay neu receipt truoc PASS va khong co QUARANTINE.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import types
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Nap phu thuoc tu CHINH byte da bam (doc MOT lan) — giong producer: sha ghi/pin la sha cua ma THAT SU chay."""
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


qm = _load("quiescence_model_for_runner", "quiescence_model_v1.py")
PINNED_BASE_CONTROLLER_SHA256 = "491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0"
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
PASS, FAIL_CLEAN, REFUSED, QUARANTINE = "PASS", "FAIL_CLEAN", "REFUSED", "QUARANTINE"
MAX_PHASE_S = 3600.0
DEFAULT_MARGIN_S = 120.0
DEFAULT_SETTLE_S = 2.0                                                                                    # cho server don phien sau khi client chet, va dam bao probe dau THUC SU sau child_exit (dong ho tho)
MAX_PROBES = 4
ACTIVE_KINDS = ("pure", "db")


def _safe(value: Any) -> Any:
    """Chuyen sang gia tri JSON an toan: float khong huu han / kieu la => chuoi repr (receipt KHONG BAO GIO nem ngoai le vi du lieu la)."""
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, float) and not (value == value and value not in (float("inf"), float("-inf"))):
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def seconds_until_vn(now_utc, hour: int = 0, minute: int = 20) -> float:
    """So giay tu `now_utc` toi lan KE TIEP dong ho Viet Nam (UTC+7) chi hour:minute (mac dinh 00:20 = cuoi cua so lam viec truoc khung crawl 00:30). Thuan, kiem thu duoc."""
    import datetime as dt
    vn = dt.timezone(dt.timedelta(hours=7))
    local = now_utc.astimezone(vn)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += dt.timedelta(days=1)
    return (target - local).total_seconds()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path) -> str:
    return sha256_hex(Path(path).read_bytes())


def argv_sha256(spec: Mapping[str, Any]) -> str:
    """Hash canonical cua (argv, cwd, env) — cai duoc APPROVAL ghim. Doi bat ky ky tu nao => hash doi."""
    return sha256_hex(canonical_json({"argv": list(spec["argv"]), "cwd": spec["cwd"], "env": dict(spec["env"])}))


def parse_approved_argv(markdown: str) -> dict[str, str]:
    """Doc cac dong `APPROVED_ARGV_SHA256[<phase>]: <64 hex>`; trung ten hoac sai dinh dang => ValueError (khong doan)."""
    found: dict[str, str] = {}
    for line in markdown.splitlines():
        match = re.match(r"^\s*APPROVED_ARGV_SHA256\[([a-z0-9_]{1,64})\]:\s*`?([0-9a-f]{64})`?\s*$", line)
        if line.strip().startswith("APPROVED_ARGV_SHA256") and match is None:
            raise ValueError(f"approval_line_malformed: {line.strip()[:80]!r}")
        if match:
            if match.group(1) in found:
                raise ValueError(f"approval_duplicate_phase: {match.group(1)}")
            found[match.group(1)] = match.group(2)
    return found


def validate_spec(spec: Any) -> list[str]:
    """Hinh dang CHINH XAC cua spec (kieu that, khong bool-la-int, khong thieu/thua khoa)."""
    if not isinstance(spec, Mapping):
        return ["spec_not_mapping"]
    errors: list[str] = []
    allowed = {"name", "kind", "argv", "cwd", "env", "required_files", "deadline_s", "db", "outputs"}
    if set(spec) - allowed or {"name", "kind", "argv", "cwd", "env", "required_files", "deadline_s"} - set(spec):
        errors.append(f"spec_keys: missing={sorted({'name','kind','argv','cwd','env','required_files','deadline_s'} - set(spec))} extra={sorted(set(spec) - allowed)}")
        return errors
    if type(spec["name"]) is not str or not NAME_RE.match(spec["name"]):
        errors.append("name")
    if spec["kind"] not in ACTIVE_KINDS:
        errors.append("kind")
    if not (isinstance(spec["argv"], Sequence) and not isinstance(spec["argv"], (str, bytes)) and len(spec["argv"]) >= 1 and all(type(a) is str and a != "" for a in spec["argv"])):
        errors.append("argv")
    if type(spec["cwd"]) is not str or not spec["cwd"]:
        errors.append("cwd")
    env = spec["env"]
    if not (isinstance(env, Mapping) and all(type(k) is str and k and type(v) is str for k, v in env.items())):
        errors.append("env")
    files = spec["required_files"]
    if not (isinstance(files, Mapping) and files and all(type(k) is str and type(v) is str and SHA64_RE.match(v) for k, v in files.items())):
        errors.append("required_files")
    deadline = spec["deadline_s"]
    if type(deadline) not in (int, float) or not (0 < deadline <= MAX_PHASE_S):
        errors.append("deadline_s")
    outputs = spec.get("outputs", [])
    if not (isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes)) and all(type(o) is str for o in outputs)):
        errors.append("outputs")
    if spec["kind"] == "db":
        db = spec.get("db")
        if not (isinstance(db, Mapping) and set(db) == {"app_user", "db_scope", "protected_dbs"} and type(db["app_user"]) is str and db["app_user"]
                and isinstance(db["db_scope"], Sequence) and not isinstance(db["db_scope"], (str, bytes)) and all(type(x) is str for x in db["db_scope"]) and db["db_scope"]
                and isinstance(db["protected_dbs"], Sequence) and not isinstance(db["protected_dbs"], (str, bytes)) and all(type(x) is str for x in db["protected_dbs"])
                and not set(db["db_scope"]) & set(db["protected_dbs"])):
            errors.append("db")
    elif "db" in spec:
        errors.append("db_not_allowed_for_pure_phase")
    return errors


# ----------------------------------------------------------------------------- cong that (cua so gio + crawler idle) tu controller nen DA GHIM
def make_evidence_gate(evidence_path: str | Path):
    """Tra (check, wa): `check()` -> (ok, [ly do]) dung CHINH `EvidenceGate` cua controller nen (nap tu byte da bam, phai bang hash ghim) — cua so gio 17:00-00:30 VN co margin 10 phut, bang chung
    crawler-idle con han/tuoi/READ ONLY/0 run/item. Controller KHONG tu gia han bang chung (nguon van hanh co tham quyen gia han)."""
    wa = _load("warehouse_audit_for_runner", "warehouse_audit_v3.py")
    if wa.__source_sha256__ != PINNED_BASE_CONTROLLER_SHA256:
        raise RuntimeError("controller nen khac ban da ghim")
    gate = wa.EvidenceGate(evidence_path)

    def check() -> tuple[bool, list[str]]:
        try:
            gate.check(force=True)
        except wa.GateLost as exc:
            return False, [str(exc)]
        except Exception as exc:                                                                           # noqa: BLE001
            return False, [f"gate_error: {type(exc).__name__}"]
        return True, []

    return check, wa


# ----------------------------------------------------------------------------- launcher that (Job Object)
def load_job_launcher_class(wj=None):
    """Tao JobLauncher that tu `windows_job_v1.py` (nap tu byte da bam) — `wj` chi de TEST tiem Job gia (khong can Windows)."""
    wj = wj or _load("windows_job_for_runner", "windows_job_v1.py")

    class RealJobLauncher:
        def __init__(self, out_dir: Path, *, sample_s: float = 0.2, grace_s: float = 10.0, clock=None) -> None:
            import time
            self.out_dir, self.sample_s, self.grace_s = Path(out_dir), sample_s, grace_s
            self._time = clock or time
            self.source_sha256 = getattr(wj, "__source_sha256__", None)

        def launch(self, spec: Mapping[str, Any], cap_s: float, should_abort: Callable[[], tuple[bool, list[str]]] | None = None, gate_poll_s: float = 5.0) -> dict[str, Any]:
            stamp = hashlib.sha256(canonical_json(spec["name"]) + str(self._time.monotonic()).encode()).hexdigest()[:10]
            out, err = self.out_dir / f"{spec['name']}_{stamp}.stdout", self.out_dir / f"{spec['name']}_{stamp}.stderr"
            pids_seen: set[int] = set()
            started = self._time.monotonic()
            with wj.Job() as job:
                child = job.spawn(list(spec["argv"]), cwd=spec["cwd"], env=dict(spec["env"]), stdout_path=out, stderr_path=err)
                pids_seen.add(child.pid)
                exit_code, timed_out, aborted = None, False, None
                deadline = started + cap_s
                next_gate = started + gate_poll_s
                gate_polls = 0
                while True:
                    remaining = deadline - self._time.monotonic()
                    exit_code = child.wait(max(0.0, min(self.sample_s, remaining)))
                    try:
                        pids_seen.update(job.pids())
                    except OSError:
                        pass
                    if exit_code is not None:
                        break
                    if should_abort is not None and self._time.monotonic() >= next_gate:                      # cong mat giua chung (crawler hoat dong / sap het cua so gio) => giet CAY, ghi nhan
                        next_gate = self._time.monotonic() + gate_poll_s
                        gate_polls += 1
                        gate_ok, gate_reasons = should_abort()
                        if not gate_ok:
                            aborted = list(gate_reasons)
                            break
                    if remaining <= self.sample_s:
                        timed_out = True
                        break
                killed = False
                if timed_out or aborted is not None:
                    job.kill_tree()
                    killed = True
                    exit_code = child.wait(self.grace_s)
                contained = job.wait_empty(self.grace_s)
                if not contained:
                    job.kill_tree()                                                                      # root da thoat nhung con hau due: giet CAY (chi cay cua phase), roi do lai
                    killed = True
                    contained = job.wait_empty(self.grace_s)
                ended = self._time.monotonic()
            return {"exit_code": exit_code, "timed_out": timed_out, "aborted_by_gate": aborted, "gate_polls": gate_polls, "killed_tree": killed, "contained": bool(contained), "pids_seen": sorted(pids_seen),
                    "stdout_path": str(out), "stderr_path": str(err), "stdout_sha256": file_sha256(out), "stderr_sha256": file_sha256(err), "started": started, "ended": ended}

    return RealJobLauncher


# ----------------------------------------------------------------------------- runner
class Runner:
    """Dieu phoi MOT chuoi phase. Moi thanh phan ben ngoai la doi tuong duoc TIEM vao:
    launcher.launch(spec, cap_s) -> dict ; observer.capture_baseline() / observer.probe() ; clock.monotonic()/sleep(s) ; gate() -> (ok, reasons) ; selftest (dict + sha)."""

    def __init__(self, *, plan_dir: Path, approvals: Mapping[str, str], launcher, clock, gate: Callable[[], tuple[bool, list[str]]], observer=None, selftest: Mapping[str, Any] | None = None,
                 selftest_sha256: str | None = None, total_budget_s: float = 3600.0, margin_s: float = DEFAULT_MARGIN_S, max_probes: int = MAX_PROBES, settle_s: float = DEFAULT_SETTLE_S,
                 gate_poll_s: float = 5.0, window_end_monotonic: float | None = None, expected_tooling: Mapping[str, Any] | None = None) -> None:
        self.plan_dir, self.approvals, self.launcher, self.clock, self.gate, self.observer = Path(plan_dir), dict(approvals), launcher, clock, gate, observer
        self.selftest, self.selftest_sha256 = selftest, selftest_sha256
        self.total_budget_s, self.margin_s, self.max_probes, self.settle_s, self.gate_poll_s = total_budget_s, margin_s, max_probes, settle_s, gate_poll_s
        self.window_end_monotonic, self.expected_tooling = window_end_monotonic, (dict(expected_tooling) if expected_tooling is not None else None)
        self.started = clock.monotonic()
        self.prev_receipt_sha: str | None = None
        self.seq = 0
        self.receipts: list[dict[str, Any]] = []

    # ---- tien ich
    def remaining_s(self) -> float:
        remaining = self.total_budget_s - (self.clock.monotonic() - self.started)
        if self.window_end_monotonic is not None:                                                          # khong bao gio vuot qua cuoi cua so lam viec (00:20 VN)
            remaining = min(remaining, self.window_end_monotonic - self.clock.monotonic())
        return remaining

    def tooling(self) -> dict[str, Any]:
        """Hash cong cu THAT SU dang chay (runner doc lai luc goi, mo hinh quiescence luc nap, Job Object cua launcher): approval phai ghim ba gia tri nay."""
        return {"runner_sha256": file_sha256(__file__), "quiescence_model_sha256": qm.__source_sha256__, "windows_job_sha256": getattr(self.launcher, "source_sha256", None)}

    def _write_receipt(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        body = _safe({**body, "seq": self.seq, "phase": name, "prev_receipt_sha256": self.prev_receipt_sha, "tooling": self.tooling()})
        data = canonical_json(body)
        path = self.plan_dir / f"receipt_{self.seq:02d}_{name}.json"
        with open(path, "xb") as handle:                                                                  # khong ghi de
            handle.write(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8"))
        receipt = {**body, "receipt_path": str(path), "receipt_sha256": sha256_hex(data)}
        self.prev_receipt_sha = receipt["receipt_sha256"]
        self.receipts.append(receipt)
        return receipt

    def _quarantine_marker(self) -> Path:
        return self.plan_dir / "QUARANTINE.json"

    def _refuse(self, name: str, reasons: list[str], extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._write_receipt(name, {"verdict": REFUSED, "reasons": reasons, **(extra or {})})

    # ---- mot phase
    def run_phase(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        name = spec.get("name") if isinstance(spec, Mapping) and type(spec.get("name")) is str and NAME_RE.match(spec["name"]) else "invalid_spec"
        errors = validate_spec(spec)
        if errors:
            return self._refuse(name, [f"spec_invalid: {errors}"])
        if self._quarantine_marker().exists():
            return self._refuse(name, ["quarantine_marker_present: khong chay phase nao sau quarantine"])
        if self.receipts and self.receipts[-1]["verdict"] != PASS:
            return self._refuse(name, [f"previous_phase_not_pass: {self.receipts[-1]['phase']}={self.receipts[-1]['verdict']}"])
        computed = argv_sha256(spec)
        approved = self.approvals.get(spec["name"])
        if approved != computed:                                                                           # None/sai dinh dang/khac hash deu khong bang hash tinh duoc (hex 64) => tu choi
            return self._refuse(name, ["argv_sha256_not_approved"], {"computed_argv_sha256": computed, "approved_argv_sha256": approved})
        tooling = self.tooling()
        if self.expected_tooling is None or tooling != self.expected_tooling:                              # cong cu phai dung la cai da duyet (bat buoc co pin)
            return self._refuse(name, ["tooling_not_approved"], {"argv_sha256": computed, "expected_tooling": self.expected_tooling})
        gate_ok, gate_reasons = self.gate()
        if not gate_ok:
            return self._refuse(name, [f"gate_closed: {gate_reasons}"], {"argv_sha256": computed})
        needed = float(spec["deadline_s"]) + self.margin_s
        if self.remaining_s() < needed:
            return self._refuse(name, [f"budget_insufficient: remaining={round(self.remaining_s(), 1)}s < deadline+margin={needed}s"], {"argv_sha256": computed})
        before: dict[str, str] = {}
        for path, expected in spec["required_files"].items():
            try:
                actual = file_sha256(path)
            except OSError as exc:
                return self._refuse(name, [f"required_file_unreadable: {path}: {type(exc).__name__}"], {"argv_sha256": computed})
            before[path] = actual
            if actual != expected:
                return self._refuse(name, [f"required_file_hash_mismatch: {path}"], {"argv_sha256": computed, "file_hashes_before": before})
        existing = [o for o in spec.get("outputs", []) if Path(o).exists()]
        if existing:
            return self._refuse(name, [f"output_already_exists: {existing}"], {"argv_sha256": computed})                  # khong ghi de, khong tai dung dau ra cu
        baseline = None
        if spec["kind"] == "db":
            if self.observer is None or not isinstance(self.selftest, Mapping) or any(self.selftest.get(k) is not True for k in qm.SELFTEST_KEYS) or not self.selftest_sha256:
                return self._refuse(name, ["db_phase_requires_observer_and_all_true_selftest_receipt"], {"argv_sha256": computed})
            try:
                baseline = self.observer.capture_baseline()
            except Exception as exc:                                                                       # noqa: BLE001
                return self._refuse(name, [f"baseline_capture_failed: {type(exc).__name__}"], {"argv_sha256": computed})
            problems = _baseline_problems(baseline)
            if problems:
                return self._refuse(name, [f"baseline_invalid: {problems}"], {"argv_sha256": computed, "baseline": baseline})
        launched_at = self.clock.monotonic()
        try:
            result = self.launcher.launch(spec, float(spec["deadline_s"]), should_abort=self.gate, gate_poll_s=self.gate_poll_s)
        except Exception as exc:                                                                           # noqa: BLE001  — launch loi: khong biet co process nao khong => QUARANTINE neu phase DB
            return self._finish_unknown_launch(name, spec, computed, before, baseline, f"launch_exception: {type(exc).__name__}: {str(exc)[:160]}")
        body: dict[str, Any] = {"argv_sha256": computed, "approved_argv_sha256": approved, "file_hashes_before": before, "baseline": baseline, "launch": _public_launch(result),
                                "launched_at_monotonic": launched_at, "selftest_sha256": self.selftest_sha256 if spec["kind"] == "db" else None, "kind": spec["kind"]}
        contained = result.get("contained") is True
        aborted = result.get("aborted_by_gate")
        orphaned = bool(result.get("killed_tree")) and not result.get("timed_out") and aborted is None      # root thoat nhung con hau due phai bi giet => workload sai quy uoc
        exit_ok = result.get("exit_code") == 0 and not result.get("timed_out") and not orphaned and aborted is None
        if not contained:
            body.update(verdict=QUARANTINE, reasons=["containment_not_proven: job khong rong sau grace"])
            return self._quarantine(name, body)
        child_exit = self.clock.monotonic()
        quiescence = None
        if spec["kind"] == "db":
            quiescence = self._quiescence(spec, baseline, result, child_exit)
            body["quiescence"] = quiescence
            if quiescence["status"] != qm.VERIFIED:
                body.update(verdict=QUARANTINE, reasons=[f"quiescence_{quiescence['status']}"])
                return self._quarantine(name, body)
        after: dict[str, str] = {}
        for path, expected in spec["required_files"].items():
            after[path] = file_sha256(path)
        body["file_hashes_after"] = after
        if after != before:
            body.update(verdict=FAIL_CLEAN, reasons=["required_file_changed_during_phase"])
            return self._write_receipt(name, body)
        outs: dict[str, str | None] = {}
        for path in spec.get("outputs", []):
            outs[path] = file_sha256(path) if Path(path).exists() else None
        body["outputs"] = outs
        if not exit_ok:
            reason = f"child_failed: exit_code={result.get('exit_code')} timed_out={result.get('timed_out')} descendants_outlived_root={orphaned}"
            if aborted is not None:
                reason = f"aborted_by_gate: {aborted}"
            body.update(verdict=FAIL_CLEAN, reasons=[reason])
            return self._write_receipt(name, body)
        missing = [p for p, h in outs.items() if h is None]
        if missing:
            body.update(verdict=FAIL_CLEAN, reasons=[f"expected_outputs_missing: {missing}"])
            return self._write_receipt(name, body)
        body.update(verdict=PASS, reasons=[])
        return self._write_receipt(name, body)

    def _finish_unknown_launch(self, name, spec, computed, before, baseline, reason) -> dict[str, Any]:
        body = {"argv_sha256": computed, "file_hashes_before": before, "baseline": baseline, "kind": spec["kind"], "reasons": [reason]}
        body["verdict"] = QUARANTINE if spec["kind"] == "db" else FAIL_CLEAN                              # pure khong ghi DB: khong can quarantine, nhung van la loi
        return self._quarantine(name, body) if body["verdict"] == QUARANTINE else self._write_receipt(name, body)

    def _quarantine(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        receipt = self._write_receipt(name, body)
        with open(self._quarantine_marker(), "xb") as handle:
            handle.write(json.dumps({"phase": name, "receipt_sha256": receipt["receipt_sha256"], "reasons": receipt["reasons"]}, ensure_ascii=False, indent=2).encode("utf-8"))
        return receipt

    def _quiescence(self, spec: Mapping[str, Any], baseline: Mapping[str, Any], result: Mapping[str, Any], child_exit: float) -> dict[str, Any]:
        probes: list[dict[str, Any]] = []
        verdict: dict[str, Any] = {"status": qm.UNKNOWN, "reasons": ["no_probe"], "kill_candidates": [], "assumptions": list(qm.ASSUMPTIONS)}
        for index in range(self.max_probes):
            self.clock.sleep(self.settle_s if index == 0 else qm.MIN_SPACING_S + 0.5)
            try:
                probe = self.observer.probe()
            except Exception as exc:                                                                       # noqa: BLE001
                probe = {"status": "error", "rows": [], "truncated": False, "rolling_back": None, "observed_at": self.clock.monotonic(), "uptime_s": None, "error": type(exc).__name__}
            if isinstance(probe, Mapping):
                probe = {**probe, "observed_at": self.clock.monotonic()}                                  # dong ho CUA RUNNER; observer khong tu khai bao thoi gian
            probes.append(probe)
            if len(probes) >= 2:
                verdict = qm.evaluate_quiescence(selftest=self.selftest, probes=probes, child_exit_monotonic=child_exit, baseline_thread_id=baseline["baseline_thread_id"],
                                                 baseline_uptime_s=baseline["baseline_uptime_s"], app_user=spec["db"]["app_user"], db_scope=spec["db"]["db_scope"],
                                                 child_pids=result.get("pids_seen", []), probe_session_id=baseline["probe_session_id"], protected_dbs=spec["db"]["protected_dbs"])
                if verdict["status"] in (qm.VERIFIED, qm.UNKNOWN) and len(probes) >= 2:
                    break                                                                                  # VERIFIED: xong; UNKNOWN: khong "thu lai cho den khi xanh"
        return {**verdict, "probe_count": len(probes), "probes": probes, "child_exit_monotonic": child_exit}

    # ---- ca chuoi
    def run_plan(self, specs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for spec in specs:
            receipt = self.run_phase(spec)
            out.append(receipt)
            if receipt["verdict"] != PASS:
                break                                                                                      # phase sau KHONG duoc launch
        return out


def _baseline_problems(baseline: Any) -> list[str]:
    if not isinstance(baseline, Mapping):
        return ["not_mapping"]
    problems = []
    if type(baseline.get("baseline_thread_id")) is not int or baseline["baseline_thread_id"] < 0:
        problems.append("baseline_thread_id")
    if type(baseline.get("baseline_uptime_s")) not in (int, float) or isinstance(baseline.get("baseline_uptime_s"), bool) or not (baseline["baseline_uptime_s"] >= 0 and baseline["baseline_uptime_s"] < float("inf")):
        problems.append("baseline_uptime_s")
    if type(baseline.get("probe_session_id")) is not int:
        problems.append("probe_session_id")
    return problems


def _public_launch(result: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("exit_code", "timed_out", "aborted_by_gate", "gate_polls", "killed_tree", "contained", "pids_seen", "stdout_path", "stderr_path", "stdout_sha256", "stderr_sha256", "started", "ended")
    return {k: result.get(k) for k in keep}
