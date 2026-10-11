"""Runner THUAN v2 cho cac phase repair (Q0 / Ph1 / Ph3..Ph6): ke hoach (plan) BAT BIEN duoc approval ghim, deadline tuyet doi, khe receipt dat truoc, containment Job Object, tich hop Q + Q0 receipt that.
v1 (da GPT review o 71/72) GIU NGUYEN de doi chieu; v2 la ban sua theo 72: R1 plan hash toan spec, R2 deadline thuc, R3 receipt slot truoc side-effect, + Q0 receipt loader, env allowlist.

TRANG THAI: THIET KE + MA + TEST voi fake/child local; KHONG CLI, KHONG ket noi DB, KHONG quyen thuc thi. Khong bao gio tu KILL phien DB (ung vien chi duoc GHI vao receipt).

NGUYEN TAC (moi dieu deu co test):
  * KE HOACH BAT BIEN: `plan_sha256(specs, settings, expected_tooling, q0_receipt_sha256)` = hash canonical TOAN BO ke hoach (thu tu, ten, kind, argv/cwd/env, required_files, outputs, deadline, khoi db, settings an toan,
    pin cong cu, pin Q0). Hash DUOC DUYET den tu ngoai (`APPROVED_PLAN_SHA256: <hex>` trong approval). Runner chi chay chinh cac spec da gan vao no, theo thu tu; khong co duong truyen spec ngoai.
    `argv_sha256`/`spec_sha256` van ghi vao receipt de audit (khong thay the plan hash).
  * THU TU TRUOC SIDE-EFFECT: plan hash -> thu muc receipt phai TRONG -> Q0 receipt (neu co phase ghi) -> [moi phase] khe receipt dat truoc -> tooling pin -> gate -> ngan sach -> byte -> output chua ton tai ->
    baseline (neu write) -> gate + ngan sach + byte LAI sau baseline -> launch. Moi lan tu choi: launcher 0 lan, observer khong cham DB (tru baseline sau khi qua cac cong).
  * DEADLINE TUYET DOI: `hard_deadline = min(start + total_budget, window_end)`; du tru cho containment + quiescence (settle + probe + khoang cach) + receipt; probe/gate/quiescence deu kiem hard_deadline;
    evidence tre sau deadline chi de chan doan (QUARANTINE/FAIL), khong bao gio PASS.
  * RECEIPT: khe `receipt_NN_<ten>.json` dat truoc bang `xb` TRUOC moi side-effect, ghi cuoi bang temp + `os.replace`; thu muc co receipt/marker cu => REFUSED (khong resume ngam, khong xoa file cu); loi sau launch =>
    receipt QUARANTINE (write) hoac FAIL_CLEAN (readonly) toi da co the, `QUARANTINE.json` khong ghi de.
  * Q0: phase `write` chi chay voi Q0 receipt DA XAC MINH (`load_q0_receipt`: hash noi dung = pin cua plan, observer/SQL-set/model pin, tuoi, server identity khop baseline, uptime khong lui); khong co duong tiem dict selftest.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import types
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Nap phu thuoc tu CHINH byte da bam (doc MOT lan): sha ghi/pin la sha cua ma THAT SU chay."""
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


qm = _load("quiescence_model_for_runner_v2", "quiescence_model_v1.py")
PINNED_BASE_CONTROLLER_SHA256 = "491d3f4a3e220da15ed2b57eac07762351cd98c2cf95ab2058f492676cfcbbc0"
SHA64_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
PASS, FAIL_CLEAN, REFUSED, QUARANTINE = "PASS", "FAIL_CLEAN", "REFUSED", "QUARANTINE"
KINDS = ("readonly", "write")
MAX_PHASE_S = 3600.0
ENV_ALLOWLIST = frozenset({"SystemRoot", "TEMP", "TMP", "PATH", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED"})        # TEN bien duoc phep (khong chua secret; DB credentials do backend/.env nap trong child)
SECRET_LIKE = re.compile(r"(PASS|SECRET|TOKEN|KEY|CRED|AUTH)", re.IGNORECASE)
DIR_SEAL_MAX_FILES, DIR_SEAL_MAX_BYTES, DIR_SEAL_MAX_DEPTH, DIR_SEAL_BUDGET_S = 500, 2 * 1024 ** 3, 6, 30.0   # gioi han niem phong thu muc dau ra + du tru thoi gian MOI thu muc (nam trong tong ngan sach)
_REPARSE_POINT = 0x400                                                                                      # FILE_ATTRIBUTE_REPARSE_POINT (symlink/junction tren Windows)
_ABS_OUTPUT_DIR_RE = re.compile(r"^[A-Za-z]:[\\/][^\x00-\x1f]*$")
FORBIDDEN_ARGV = frozenset({"--drop-existing", "--drop-only"})                                             # co pha huy (DROP DATABASE) cua clone_warehouse_for_dev: KHONG BAO GIO trong spec
DEFAULT_SETTINGS = {"total_budget_s": 3600.0, "margin_s": 60.0, "max_probes": 4, "settle_s": 2.0, "gate_poll_s": 5.0, "window_end_vn": [0, 20], "baseline_cap_s": 20.0, "probe_cap_s": 15.0,
                    "containment_grace_s": 10.0, "receipt_reserve_s": 5.0, "q0_max_age_s": 3600.0}
SETTING_BOUNDS = {"total_budget_s": (1, 21600), "margin_s": (0, 3600), "settle_s": (0, 60), "gate_poll_s": (0.05, 60), "baseline_cap_s": (1, 600), "probe_cap_s": (1, 600),
                  "containment_grace_s": (0, 600), "receipt_reserve_s": (0, 600), "q0_max_age_s": (60, 86400)}


# ----------------------------------------------------------------------------- tien ich thuan
def _safe(value: Any) -> Any:
    """Gia tri JSON an toan: float khong huu han / kieu la => repr (receipt KHONG BAO GIO nem ngoai le vi du lieu la)."""
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str | Path) -> str:
    return sha256_hex(Path(path).read_bytes())


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def reserve_after_launch(spec: Mapping[str, Any], settings: Mapping[str, Any]) -> float:
    """Du tru thoi gian SAU launch cua mot phase (containment + receipt + niem phong moi thu muc dau ra + (write) settle/probe/khoang cach). Runner VA `repair_heavy_plan_v1.suggest_total_budget` cung dung ham nay."""
    reserve = settings["containment_grace_s"] + settings["receipt_reserve_s"] + DIR_SEAL_BUDGET_S * len(spec.get("output_dirs", []))
    if spec["kind"] == "write":
        reserve += settings["settle_s"] + (settings["max_probes"] - 1) * (qm.MIN_SPACING_S + 0.5) + settings["max_probes"] * settings["probe_cap_s"]
    return reserve


def _is_link(path: Path) -> bool:
    status = os.lstat(path)
    return stat.S_ISLNK(status.st_mode) or bool((getattr(status, "st_file_attributes", 0) or 0) & _REPARSE_POINT)


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raise_walk_error(error: OSError) -> None:
    raise error                                                                                             # `os.walk` mac dinh NUOT loi scandir cua thu muc con => seal thieu tep ma van PASS (GPT 80 H3-WALK)


def directory_seal(path: str | Path, *, max_files: int = DIR_SEAL_MAX_FILES, max_bytes: int = DIR_SEAL_MAX_BYTES, max_depth: int = DIR_SEAL_MAX_DEPTH) -> dict[str, Any]:
    """Niem phong MOT thu muc dau ra: {duong dan tuong doi (posix) -> sha256 tung tep} + hash tong. Thu muc that (khong symlink/junction ke ca ben trong, khong tep dac biet), huu han (so tep, tong byte, do sau);
    vi pham => ValueError (khong niem phong duoc != thieu). Khong theo lien ket, khong thoat khoi goc."""
    root = Path(path)
    try:
        real = not _is_link(root) and root.is_dir()
    except OSError:
        real = False
    if not real:
        raise ValueError("not_a_real_directory")
    files: dict[str, str] = {}
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=_raise_walk_error):
        here = Path(dirpath)
        if len(here.relative_to(root).parts) > max_depth:
            raise ValueError("too_deep")
        for name in [*dirnames, *filenames]:
            if _is_link(here / name):
                raise ValueError(f"link_inside: {(here / name).relative_to(root).as_posix()}")
        for name in sorted(filenames):
            file = here / name
            status = os.lstat(file)
            if not stat.S_ISREG(status.st_mode):
                raise ValueError(f"not_a_regular_file: {file.relative_to(root).as_posix()}")
            total += status.st_size
            if len(files) >= max_files or total > max_bytes:
                raise ValueError("too_large")
            files[file.relative_to(root).as_posix()] = _stream_sha256(file)
    ordered = dict(sorted(files.items()))
    return {"kind": "directory", "files": len(ordered), "bytes": total, "aggregate_sha256": sha256_hex(canonical_json(ordered)), "manifest": ordered}


def seconds_until_vn(now_utc: dt.datetime, hour: int = 0, minute: int = 20) -> float:
    """So giay tu `now_utc` toi lan KE TIEP dong ho Viet Nam (UTC+7) chi hour:minute (mac dinh 00:20 = cuoi cua so lam viec truoc khung crawl 00:30)."""
    vn = dt.timezone(dt.timedelta(hours=7))
    local = now_utc.astimezone(vn)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += dt.timedelta(days=1)
    return (target - local).total_seconds()


def argv_sha256(spec: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json({"argv": list(spec["argv"]), "cwd": spec["cwd"], "env": dict(spec["env"])}))


def spec_sha256(spec: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(spec))


# ----------------------------------------------------------------------------- settings / spec / plan
def validate_settings(settings: Any) -> list[str]:
    if not isinstance(settings, Mapping) or set(settings) != set(DEFAULT_SETTINGS):
        return [f"settings_keys: expected {sorted(DEFAULT_SETTINGS)}"]
    errors = []
    for key, (low, high) in SETTING_BOUNDS.items():
        if not _finite(settings[key]) or isinstance(settings[key], bool) or not (low <= settings[key] <= high):
            errors.append(key)
    if type(settings["max_probes"]) is not int or not (2 <= settings["max_probes"] <= 8):
        errors.append("max_probes")
    window = settings["window_end_vn"]
    if window is not None and not (isinstance(window, Sequence) and len(window) == 2 and all(type(x) is int for x in window) and 0 <= window[0] < 24 and 0 <= window[1] < 60):
        errors.append("window_end_vn")
    return errors


def validate_spec(spec: Any) -> list[str]:
    """Hinh dang CHINH XAC cua spec (kieu that, khong bool-la-int, khong thieu/thua khoa, env chi ten trong allowlist va khong giong secret)."""
    if not isinstance(spec, Mapping):
        return ["spec_not_mapping"]
    required = {"name", "kind", "argv", "cwd", "env", "required_files", "deadline_s"}
    allowed = required | {"db", "outputs", "output_dirs"}
    if set(spec) - allowed or required - set(spec):
        return [f"spec_keys: missing={sorted(required - set(spec))} extra={sorted(set(spec) - allowed)}"]
    errors: list[str] = []
    if type(spec["name"]) is not str or not NAME_RE.match(spec["name"]):
        errors.append("name")
    if spec["kind"] not in KINDS:
        errors.append("kind")
    if not (isinstance(spec["argv"], Sequence) and not isinstance(spec["argv"], (str, bytes)) and len(spec["argv"]) >= 1 and all(type(a) is str and a != "" for a in spec["argv"])):
        errors.append("argv")
    elif FORBIDDEN_ARGV & set(spec["argv"]):
        errors.append(f"forbidden_flag: {sorted(FORBIDDEN_ARGV & set(spec['argv']))}")
    if type(spec["cwd"]) is not str or not spec["cwd"]:
        errors.append("cwd")
    env = spec["env"]
    if not (isinstance(env, Mapping) and all(type(k) is str and k and type(v) is str for k, v in env.items())):
        errors.append("env")
    else:
        bad = sorted(k for k in env if k not in ENV_ALLOWLIST or SECRET_LIKE.search(k))
        if bad:
            errors.append(f"env_not_allowlisted: {bad}")
    files = spec["required_files"]
    if not (isinstance(files, Mapping) and files and all(type(k) is str and type(v) is str and SHA64_RE.match(v) for k, v in files.items())):
        errors.append("required_files")
    deadline = spec["deadline_s"]
    if not _finite(deadline) or isinstance(deadline, bool) or not (0 < deadline <= MAX_PHASE_S):
        errors.append("deadline_s")
    outputs = spec.get("outputs", [])
    if not (isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes)) and all(type(o) is str for o in outputs)):
        errors.append("outputs")
    output_dirs = spec.get("output_dirs", [])
    if not (isinstance(output_dirs, Sequence) and not isinstance(output_dirs, (str, bytes)) and all(type(d) is str and _ABS_OUTPUT_DIR_RE.match(d) and ".." not in re.split(r"[\\/]", d) for d in output_dirs)
            and len({os.path.normcase(d.rstrip("\\/")) for d in output_dirs}) == len(output_dirs)):
        errors.append("output_dirs")
    else:
        normalized = [os.path.normcase(d.rstrip("\\/")) for d in output_dirs]
        files_norm = [os.path.normcase(o) for o in outputs if isinstance(o, str)] if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes)) else []
        nested = any(a != b and (a + os.sep).startswith(b + os.sep) for a in normalized for b in normalized)
        inside = any((f + os.sep).startswith(d + os.sep) or f == d for f in files_norm for d in normalized)
        if nested or inside:
            errors.append("output_dirs_overlap")                                                             # thu muc dau ra long nhau / tep dau ra nam trong thu muc dau ra => mo ho
    if spec["kind"] == "write":
        db = spec.get("db")
        if not (isinstance(db, Mapping) and set(db) == {"app_user", "db_scope", "protected_dbs"} and type(db["app_user"]) is str and db["app_user"]
                and isinstance(db["db_scope"], Sequence) and not isinstance(db["db_scope"], (str, bytes)) and all(type(x) is str for x in db["db_scope"]) and db["db_scope"]
                and isinstance(db["protected_dbs"], Sequence) and not isinstance(db["protected_dbs"], (str, bytes)) and all(type(x) is str for x in db["protected_dbs"])
                and not set(db["db_scope"]) & set(db["protected_dbs"])):
            errors.append("db")
    elif "db" in spec:
        errors.append("db_not_allowed_for_readonly_phase")
    return errors


def plan_document(specs: Sequence[Mapping[str, Any]], settings: Mapping[str, Any], expected_tooling: Mapping[str, Any], q0_receipt_sha256: str | None) -> dict[str, Any]:
    return {"plan_version": 2, "specs": [dict(s) for s in specs], "settings": dict(settings), "expected_tooling": dict(expected_tooling), "q0_receipt_sha256": q0_receipt_sha256}


def plan_sha256(specs: Sequence[Mapping[str, Any]], settings: Mapping[str, Any], expected_tooling: Mapping[str, Any], q0_receipt_sha256: str | None) -> str:
    return sha256_hex(canonical_json(plan_document(specs, settings, expected_tooling, q0_receipt_sha256)))


def parse_approved_plan(markdown: str) -> str:
    """Dung MOT dong `APPROVED_PLAN_SHA256: <64 hex>`; thieu/trung/sai dinh dang => ValueError."""
    found = []
    for line in markdown.splitlines():
        if line.strip().startswith("APPROVED_PLAN_SHA256"):
            match = re.match(r"^\s*APPROVED_PLAN_SHA256:\s*`?([0-9a-f]{64})`?\s*$", line)
            if match is None:
                raise ValueError(f"approval_line_malformed: {line.strip()[:80]!r}")
            found.append(match.group(1))
    if len(found) != 1:
        raise ValueError(f"approval_plan_hash_count: {len(found)}")
    return found[0]


OUTER_APPROVAL_KEYS = ("APPROVED_PLAN_SHA256", "APPROVED_NOT_AFTER_UTC", "APPROVED_PLAN_DIR", "APPROVED_ENTRYPOINT_SHA256", "APPROVED_INVOCATION_SHA256")
_APPROVED_KEY_RE = re.compile(r"^\s*(APPROVED_[A-Z0-9_]*)")
_NOT_AFTER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ABS_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/][^\x00-\x1f]*$")


def parse_outer_approval(markdown: str) -> dict[str, Any]:
    """Approval NGOAI (outer) cua mot lan chay: dung nam dong `KEY: value` (gia tri co the boc backtick), moi KEY dung MOT lan, KHONG key `APPROVED_*` la, gia tri dung dinh dang.
    Tra {plan_sha256, not_after_utc (datetime UTC), plan_dir (str tuyet doi), entrypoint_sha256, invocation_sha256}. Thieu/trung/la/sai => ValueError (khong co gia tri mac dinh)."""
    lines_by_key: dict[str, list[str]] = {}
    for line in markdown.splitlines():
        match = _APPROVED_KEY_RE.match(line)
        if match is None:
            continue
        if match.group(1) not in OUTER_APPROVAL_KEYS:
            raise ValueError(f"approval_unknown_key: {match.group(1)[:60]!r}")
        lines_by_key.setdefault(match.group(1), []).append(line)
    values: dict[str, str] = {}
    for key in OUTER_APPROVAL_KEYS:
        lines = lines_by_key.get(key, [])
        if len(lines) != 1:
            raise ValueError(f"approval_key_count: {key}={len(lines)}")
        match = re.match(r"^\s*" + key + r":[ \t]*`?([^`\s](?:[^`]*[^`\s])?)`?\s*$", lines[0])
        if match is None:
            raise ValueError(f"approval_line_malformed: {key}")
        values[key] = match.group(1)
    for key in ("APPROVED_PLAN_SHA256", "APPROVED_ENTRYPOINT_SHA256", "APPROVED_INVOCATION_SHA256"):
        if not SHA64_RE.match(values[key]):
            raise ValueError(f"approval_hash_malformed: {key}")
    if not _NOT_AFTER_RE.match(values["APPROVED_NOT_AFTER_UTC"]):
        raise ValueError("approval_not_after_malformed")
    try:
        not_after = dt.datetime.strptime(values["APPROVED_NOT_AFTER_UTC"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        raise ValueError("approval_not_after_malformed") from None
    plan_dir = values["APPROVED_PLAN_DIR"]
    if not _ABS_WINDOWS_PATH_RE.match(plan_dir) or ".." in re.split(r"[\\/]", plan_dir):
        raise ValueError("approval_plan_dir_not_absolute_canonical")
    return {"plan_sha256": values["APPROVED_PLAN_SHA256"], "not_after_utc": not_after, "plan_dir": plan_dir, "entrypoint_sha256": values["APPROVED_ENTRYPOINT_SHA256"],
            "invocation_sha256": values["APPROVED_INVOCATION_SHA256"]}


TOOLING_KEYS = {"runner_sha256", "quiescence_model_sha256", "windows_job_sha256", "observer_sha256"}


def tooling_for(launcher: Any, observer: Any = None) -> dict[str, Any]:
    """Hash cong cu THAT SU dang chay (runner doc lai luc goi, model Q luc nap, Job luc nap, observer = hash worker/proxy/observer/model neu co) — approval/plan ghim bon gia tri nay.
    `observer_sha256` la None khi khong co observer (plan chi readonly)."""
    return {"runner_sha256": file_sha256(__file__), "quiescence_model_sha256": qm.__source_sha256__, "windows_job_sha256": getattr(launcher, "source_sha256", None),
            "observer_sha256": getattr(observer, "tooling_sha256", None)}


# ----------------------------------------------------------------------------- Q0 receipt (xac minh tren duong execute)
def load_q0_receipt(path: str | Path, *, expected_receipt_sha256: str, expected_observer_sha256: str, expected_sql_set_sha256: str, expected_model_sha256: str,
                    now_utc: dt.datetime, max_age_s: float) -> dict[str, Any]:
    """Doc + xac minh Q0 receipt (do `repair_db_observer_v1.write_q0_receipt` ghi): hash byte tep == pin, kind, observer/SQL-set/model == pin, moi khoa self-test True, tuoi hop le (khong tuong lai), co server
    identity va uptime huu han. Tra dict da xac minh (kem `receipt_sha256`, `verified=True`). Loi => ValueError (khong tra gia tri nua tin)."""
    data = Path(path).read_bytes()
    digest = sha256_hex(data)
    if digest != expected_receipt_sha256:
        raise ValueError("q0_receipt_sha256_mismatch")
    doc = json.loads(data.decode("utf-8"))
    checks = {"kind": doc.get("kind") == "q0_selftest_receipt", "observer": doc.get("observer_script_sha256") == expected_observer_sha256, "sql_set": doc.get("sql_set_sha256") == expected_sql_set_sha256,
              "model": doc.get("quiescence_model_sha256") == expected_model_sha256}
    selftest = doc.get("selftest") if isinstance(doc.get("selftest"), Mapping) else {}
    checks["selftest_all_true"] = all(selftest.get(k) is True for k in qm.SELFTEST_KEYS)
    server = doc.get("server") if isinstance(doc.get("server"), Mapping) else {}
    checks["server_identity"] = type(server.get("server_uuid")) is str and bool(server.get("server_uuid")) and _finite(server.get("uptime_s")) and server["uptime_s"] >= 0
    details = doc.get("details") if isinstance(doc.get("details"), Mapping) else {}
    ids = details.get("successive_ids")
    checks["connection_id_sanity"] = details.get("ids_increase") is True and isinstance(ids, list) and len(ids) == 2 and all(type(i) is int and i > 0 for i in ids) and ids[0] < ids[1]
    checks["app_user"] = type(details.get("app_user")) is str and bool(details.get("app_user"))
    checks["databases"] = details.get("databases_truncated") is False and isinstance(details.get("databases"), list) and bool(details["databases"]) and all(type(x) is str for x in details["databases"])
    try:
        created = dt.datetime.strptime(str(doc.get("created_at_utc")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        age = (now_utc - created).total_seconds()
        checks["fresh"] = _finite(max_age_s) and 0 <= age <= max_age_s                                          # KHONG tu tuong lai (khong co dung sai); max_age lay tu settings cua plan
    except ValueError:
        checks["fresh"] = False
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        raise ValueError(f"q0_receipt_invalid: {failed}")
    return {**doc, "receipt_sha256": digest, "verified": True}


# ----------------------------------------------------------------------------- cong that tu controller nen DA GHIM
def make_evidence_gate(evidence_path: str | Path):
    """(check, wa): `check()` -> (ok, [ly do]) dung `EvidenceGate` cua controller nen (nap tu byte, phai bang hash ghim): khung 17:00-00:30 VN (margin 10 phut) + bang chung crawler-idle."""
    wa = _load("warehouse_audit_for_runner_v2", "warehouse_audit_v3.py")
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
    """JobLauncher that tu `windows_job_v1.py` (nap tu byte da bam); `wj` chi de TEST tiem Job gia."""
    wj = wj or _load("windows_job_for_runner_v2", "windows_job_v1.py")

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
                    if should_abort is not None and self._time.monotonic() >= next_gate:
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
                    job.kill_tree()
                    killed = True
                    contained = job.wait_empty(self.grace_s)
                ended = self._time.monotonic()
            return {"exit_code": exit_code, "timed_out": timed_out, "aborted_by_gate": aborted, "gate_polls": gate_polls, "killed_tree": killed, "contained": bool(contained),
                    "pids_seen": sorted(pids_seen), "stdout_path": str(out), "stderr_path": str(err), "stdout_sha256": file_sha256(out), "stderr_sha256": file_sha256(err), "started": started,
                    "ended": ended}

    return RealJobLauncher


# ----------------------------------------------------------------------------- runner
class Runner:
    """Dieu phoi MOT ke hoach bat bien. Doi tuong ben ngoai duoc TIEM: launcher.launch(spec, cap_s, should_abort, gate_poll_s); observer.capture_baseline()/probe(); clock.monotonic()/sleep(s);
    now_utc() -> datetime; gate() -> (ok, reasons); q0 = ket qua `load_q0_receipt` (hoac None neu ke hoach khong co phase `write`)."""

    def __init__(self, *, plan_dir: Path, specs: Sequence[Mapping[str, Any]], settings: Mapping[str, Any], expected_tooling: Mapping[str, Any], q0_receipt_sha256: str | None,
                 approved_plan_sha256: str, launcher, clock, now_utc: Callable[[], dt.datetime], gate: Callable[[], tuple[bool, list[str]]], observer=None,
                 q0: Mapping[str, Any] | None = None, not_after_utc: dt.datetime | None = None, entrypoint_sha256: str | None = None) -> None:
        self.plan_dir = Path(plan_dir)
        self.specs, self.settings, self.expected_tooling = copy.deepcopy(list(specs)), copy.deepcopy(settings), copy.deepcopy(expected_tooling)           # sao chep SAU: chu goi doi lai khong anh huong ke hoach
        self.q0_receipt_sha256, self.approved_plan_sha256 = q0_receipt_sha256, approved_plan_sha256
        self.launcher, self.clock, self.now_utc, self.gate, self.observer, self.q0 = launcher, clock, now_utc, gate, observer, q0
        self.not_after_utc, self.entrypoint_sha256 = not_after_utc, entrypoint_sha256                      # han approval (tuyet doi, UTC) + hash ma entry point da duyet: ghi vao moi receipt
        self.receipts: list[dict[str, Any]] = []
        self.prev_receipt_sha: str | None = None
        self.seq = 0
        self.computed_plan_sha: str | None = None
        self.hard_deadline: float | None = None
        self.started: float | None = None

    # ---- tien ich
    def tooling(self) -> dict[str, Any]:
        return tooling_for(self.launcher, self.observer)

    def remaining_s(self) -> float:
        return float("-inf") if self.hard_deadline is None else self.hard_deadline - self.clock.monotonic()

    def reserve_after_launch_s(self, spec: Mapping[str, Any]) -> float:
        return reserve_after_launch(spec, self.settings)

    def _record(self, name: str, spec: Mapping[str, Any] | None, body: dict[str, Any]) -> dict[str, Any]:
        full = {**body, "seq": self.seq, "phase": name, "prev_receipt_sha256": self.prev_receipt_sha, "tooling": self.tooling(), "approved_plan_sha256": self.approved_plan_sha256,
                "computed_plan_sha256": self.computed_plan_sha, "entrypoint_sha256": self.entrypoint_sha256,
                "not_after_utc": self.not_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if self.not_after_utc is not None else None, "spec_sha256": spec_sha256(spec) if spec is not None else None, "argv_sha256": argv_sha256(spec) if spec is not None else None}
        return _safe(full)

    def _slot_path(self, name: str) -> Path:
        return self.plan_dir / f"receipt_{self.seq:02d}_{name}.json"

    def _reserve(self, name: str) -> Path:
        """Dat truoc khe receipt (xb) TRUOC moi side-effect: khong ghi duoc receipt => khong chay."""
        self.seq += 1
        path = self._slot_path(name)
        with open(path, "xb") as handle:
            handle.write(json.dumps({"status": "reserved", "phase": name, "seq": self.seq}).encode("utf-8"))
        return path

    def _finalize(self, name: str, spec: Mapping[str, Any] | None, slot: Path, body: dict[str, Any]) -> dict[str, Any]:
        full = self._record(name, spec, body)
        data = canonical_json(full)
        temp = slot.with_suffix(".tmp")
        with open(temp, "xb") as handle:
            handle.write(json.dumps(full, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        os.replace(temp, slot)
        receipt = {**full, "receipt_path": str(slot), "receipt_sha256": sha256_hex(data)}
        self.prev_receipt_sha = receipt["receipt_sha256"]
        self.receipts.append(receipt)
        return receipt

    def _marker(self) -> Path:
        return self.plan_dir / "QUARANTINE.json"

    def _write_marker(self, name: str, receipt_sha: str | None, reasons: Sequence[str]) -> None:
        try:
            with open(self._marker(), "xb") as handle:
                handle.write(json.dumps({"phase": name, "receipt_sha256": receipt_sha, "reasons": list(reasons)}, ensure_ascii=False, indent=2).encode("utf-8"))
        except FileExistsError:
            pass

    def _refusal(self, reasons: list[str], extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Tu choi O MUC KE HOACH (chua dung thu muc): tra ban ghi trong bo nho + ghi `refused_NN.json` neu duoc (khong cham receipt cu)."""
        body = _safe({"verdict": REFUSED, "reasons": reasons, "approved_plan_sha256": self.approved_plan_sha256, "computed_plan_sha256": self.computed_plan_sha, **(extra or {})})
        record = {**body, "receipt_path": None}
        try:
            for n in range(1, 100):
                path = self.plan_dir / f"refused_{n:02d}.json"
                try:
                    with open(path, "xb") as handle:
                        handle.write(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
                    record["receipt_path"] = str(path)
                    break
                except FileExistsError:
                    continue
        except OSError:
            pass
        self.receipts.append(record)
        return record

    # ---- ke hoach
    def run_plan(self) -> list[dict[str, Any]]:
        pre = self._preflight()
        if pre is not None:
            return [pre]
        results: list[dict[str, Any]] = []
        try:
            for spec in self.specs:
                receipt = self._run_phase(spec)
                results.append(receipt)
                if receipt["verdict"] != PASS:
                    break                                                                                  # phase sau KHONG duoc launch
        finally:
            closer = getattr(self.observer, "close", None)                                                 # observer co worker rieng: dung worker CUA TA (khong KILL DB)
            if callable(closer):
                try:
                    closer()
                except Exception:                                                                          # noqa: BLE001
                    pass
        return results

    def _preflight(self) -> dict[str, Any] | None:
        errors = validate_settings(self.settings)
        if errors:
            return self._refusal([f"settings_invalid: {errors}"])
        if not isinstance(self.specs, list) or not self.specs:
            return self._refusal(["plan_empty_or_not_a_list"])
        for index, spec in enumerate(self.specs):
            problems = validate_spec(spec)
            if problems:
                return self._refusal([f"spec_invalid[{index}]: {problems}"])
        names = [s["name"] for s in self.specs]
        if len(set(names)) != len(names):
            return self._refusal(["duplicate_phase_names"])
        if not isinstance(self.expected_tooling, Mapping) or set(self.expected_tooling) != TOOLING_KEYS:
            return self._refusal(["expected_tooling_missing_or_malformed"])
        if self.q0_receipt_sha256 is not None and not SHA64_RE.match(str(self.q0_receipt_sha256)):
            return self._refusal(["q0_receipt_pin_malformed"])
        self.computed_plan_sha = plan_sha256(self.specs, self.settings, self.expected_tooling, self.q0_receipt_sha256)
        if not SHA64_RE.match(str(self.approved_plan_sha256)) or self.computed_plan_sha != self.approved_plan_sha256:
            return self._refusal(["plan_sha256_not_approved"])
        if not self.plan_dir.is_dir() or any(self.plan_dir.iterdir()):
            return self._refusal(["plan_dir_missing_or_not_empty: khong resume ngam, khong xoa receipt cu"])
        if any(s["kind"] == "write" for s in self.specs):
            q0 = self.q0
            if (self.observer is None or not isinstance(q0, Mapping) or q0.get("verified") is not True or self.q0_receipt_sha256 is None or q0.get("receipt_sha256") != self.q0_receipt_sha256
                    or any((q0.get("selftest") or {}).get(k) is not True for k in qm.SELFTEST_KEYS)):
                return self._refusal(["write_plan_requires_observer_and_verified_q0_receipt_matching_the_plan_pin"])
            if not SHA64_RE.match(str(self.expected_tooling.get("observer_sha256"))):                       # observer cung la cong cu phai ghim (worker/proxy/observer/model)
                return self._refusal(["write_plan_requires_a_pinned_observer_tooling_hash"])
        if self.not_after_utc is not None and self._expired():
            return self._refusal(["approval_expired"], {"not_after_utc": self.not_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ")})
        self.started = self.clock.monotonic()
        self.hard_deadline = self.started + self.settings["total_budget_s"]
        window = self.settings["window_end_vn"]
        if window is not None:
            self.hard_deadline = min(self.hard_deadline, self.started + seconds_until_vn(self.now_utc(), window[0], window[1]))
        if self.not_after_utc is not None:                                                                  # han approval cung la deadline tuyet doi
            self.hard_deadline = min(self.hard_deadline, self.started + (self.not_after_utc - self.now_utc()).total_seconds())
        bound = _safe({"kind": "plan_bound", "approved_plan_sha256": self.approved_plan_sha256, "computed_plan_sha256": self.computed_plan_sha, "settings": self.settings,
                       "entrypoint_sha256": self.entrypoint_sha256, "not_after_utc": self.not_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if self.not_after_utc is not None else None,
                       "hard_deadline_in_s": self.hard_deadline - self.started, "phases": names})
        with open(self.plan_dir / "PLAN_BOUND.json", "xb") as handle:                                       # danh dau thu muc DA DUNG: crash giua chung van de lai dau vet => lan sau REFUSED
            handle.write(json.dumps(bound, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        return None

    # ---- mot phase
    def _run_phase(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        name = spec["name"]
        try:
            slot = self._reserve(name)
        except OSError as exc:
            return self._refusal([f"receipt_slot_unavailable: {type(exc).__name__}"], {"phase": name})
        state: dict[str, Any] = {"launched": False}
        try:
            return self._phase_body(spec, slot, state)
        except BaseException as exc:                                                                       # noqa: BLE001  — moi loi/ngat: dau vet toi da roi moi tiep tuc/nem lai
            kind_write = spec["kind"] == "write"
            verdict = QUARANTINE if (state["launched"] and kind_write) else (FAIL_CLEAN if state["launched"] else REFUSED)
            reasons = [f"unexpected_{'after' if state['launched'] else 'before'}_launch: {type(exc).__name__}: {str(exc)[:160]}"]
            receipt = None
            try:
                receipt = self._finalize(name, spec, slot, {"verdict": verdict, "reasons": reasons, "kind": spec["kind"], **{k: v for k, v in state.items() if k != "launched"}})
            except BaseException:                                                                          # noqa: BLE001
                receipt = None
            if verdict == QUARANTINE:
                self._write_marker(name, receipt["receipt_sha256"] if receipt else None, reasons)
            if isinstance(exc, Exception) and receipt is not None:
                return receipt
            raise

    def _expired(self) -> bool:
        return self.not_after_utc is not None and self.now_utc() >= self.not_after_utc

    def _observer_overhead_s(self) -> float:
        """Thoi gian toi da NGOAI cap tung lenh truoc baseline (worker khoi dong + xac minh don dep); observer khong khai bao => 0; gia tri la/khong huu han => vo cuc (ngan sach khong bao gio du)."""
        value = getattr(self.observer, "overhead_s", 0.0)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else float("inf")

    def _budget_problem(self, spec: Mapping[str, Any], *, include_baseline: bool) -> str | None:
        s = self.settings
        needed = spec["deadline_s"] + self.reserve_after_launch_s(spec) + s["margin_s"] + ((s["baseline_cap_s"] + self._observer_overhead_s()) if include_baseline else 0)
        remaining = self.remaining_s()
        return None if remaining >= needed else f"budget_insufficient: remaining={round(remaining, 1)}s < needed={round(needed, 1)}s"

    def _file_hashes(self, spec: Mapping[str, Any]) -> tuple[dict[str, str], str | None]:
        out: dict[str, str] = {}
        for path, expected in spec["required_files"].items():
            try:
                out[path] = file_sha256(path)
            except OSError as exc:
                return out, f"required_file_unreadable: {path}: {type(exc).__name__}"
            if out[path] != expected:
                return out, f"required_file_hash_mismatch: {path}"
        return out, None

    def _q0_stale(self) -> str | None:
        try:
            created = dt.datetime.strptime(str((self.q0 or {}).get("created_at_utc")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            age = (self.now_utc() - created).total_seconds()
        except (ValueError, TypeError, AttributeError):
            return "q0_receipt_age_unreadable_at_phase"
        if not (_finite(age) and 0 <= age <= self.settings["q0_max_age_s"]):
            return f"q0_receipt_stale_at_phase: age_s={age:.0f} max_age_s={self.settings['q0_max_age_s']}"
        return None

    def _phase_body(self, spec: Mapping[str, Any], slot: Path, state: dict[str, Any]) -> dict[str, Any]:
        name, kind, s = spec["name"], spec["kind"], self.settings

        def refuse(reasons: list[str], **extra: Any) -> dict[str, Any]:
            return self._finalize(name, spec, slot, {"verdict": REFUSED, "reasons": reasons, "kind": kind, **extra})

        if self._marker().exists():
            return refuse(["quarantine_marker_present: khong chay phase nao sau quarantine"])
        if self._expired():                                                                                 # kiem LAI truoc moi phase, khong chi luc doc approval
            return refuse(["approval_expired_before_phase"])
        if any(r["verdict"] != PASS for r in self.receipts if r.get("phase")):
            last = [r for r in self.receipts if r.get("phase")][-1]
            return refuse([f"previous_phase_not_pass: {last['phase']}={last['verdict']}"])
        tooling = self.tooling()
        if tooling != dict(self.expected_tooling):
            return refuse(["tooling_not_approved"], expected_tooling=dict(self.expected_tooling))
        gate_ok, gate_reasons = self.gate()
        if not gate_ok:
            return refuse([f"gate_closed: {gate_reasons}"])
        problem = self._budget_problem(spec, include_baseline=(kind == "write"))
        if problem:
            return refuse([problem])
        before, bad = self._file_hashes(spec)
        if bad:
            return refuse([bad], file_hashes_before=before)
        existing = [o for o in [*spec.get("outputs", []), *spec.get("output_dirs", [])] if os.path.lexists(o)]
        if existing:
            return refuse([f"output_already_exists: {existing}"])
        baseline = None
        if kind == "write":
            stale = self._q0_stale()
            if stale:                                                                                      # Q0 phai con tuoi hop le LUC MOI phase write bat dau (khong chi luc bat dau ke hoach)
                return refuse([stale])
            try:
                baseline = self.observer.capture_baseline()
            except Exception as exc:                                                                       # noqa: BLE001
                return refuse([f"baseline_capture_failed: {type(exc).__name__}"])
            problems = _baseline_problems(baseline)
            if not problems and (baseline.get("server_uuid") != (self.q0.get("server") or {}).get("server_uuid")):
                problems.append("server_identity_differs_from_q0")
            if not problems and baseline["app_user"] != spec["db"]["app_user"]:                                # F(a) loc theo user: phai dung user DO DUOC, khong chi user trong spec
                problems.append("app_user_differs_from_spec")
            if not problems and baseline["app_user"] != (self.q0.get("details") or {}).get("app_user"):
                problems.append("app_user_differs_from_q0")
            if not problems and baseline["baseline_uptime_s"] < (self.q0.get("server") or {}).get("uptime_s", float("inf")):
                problems.append("uptime_below_q0_uptime(server_restarted_since_q0)")
            if problems:
                return refuse([f"baseline_invalid: {problems}"], baseline=baseline)
            # --- kiem lai SAU baseline (baseline co the ton thoi gian/ doi cong): cong, ngan sach, byte
            gate_ok, gate_reasons = self.gate()
            if not gate_ok:
                return refuse([f"gate_closed_after_baseline: {gate_reasons}"], baseline=baseline)
            problem = self._budget_problem(spec, include_baseline=False)
            if problem:
                return refuse([f"budget_lost_after_baseline: {problem}"], baseline=baseline)
            before, bad = self._file_hashes(spec)
            if bad:
                return refuse([f"after_baseline: {bad}"], baseline=baseline, file_hashes_before=before)
        reserve_after = self.reserve_after_launch_s(spec)
        cap = min(float(spec["deadline_s"]), self.remaining_s() - reserve_after - s["margin_s"])
        if cap <= 0:
            return refuse(["budget_cap_non_positive_after_checks"], baseline=baseline)

        def abort_check() -> tuple[bool, list[str]]:
            if self.remaining_s() < reserve_after + s["margin_s"]:
                return False, ["overall_deadline_reserve_violated"]
            return self.gate()

        launched_at = self.clock.monotonic()
        state["launched"] = True                                                                           # tu day moi loi => FAIL_CLEAN (readonly) / QUARANTINE (write)
        body: dict[str, Any] = {"kind": kind, "file_hashes_before": before, "baseline": baseline, "launched_at_monotonic": launched_at, "launch_cap_s": cap, "reserve_after_launch_s": reserve_after,
                                "q0_receipt_sha256": self.q0.get("receipt_sha256") if kind == "write" else None}
        try:
            result = self.launcher.launch(spec, cap, should_abort=abort_check, gate_poll_s=s["gate_poll_s"])
        except Exception as exc:                                                                           # noqa: BLE001  — launch loi: khong chac co process => write: QUARANTINE
            body.update(verdict=QUARANTINE if kind == "write" else FAIL_CLEAN, reasons=[f"launch_exception: {type(exc).__name__}: {str(exc)[:160]}"])
            return self._seal(name, spec, slot, body)
        body["launch"] = _public_launch(result)
        aborted = result.get("aborted_by_gate")
        orphaned = bool(result.get("killed_tree")) and not result.get("timed_out") and aborted is None
        exit_ok = result.get("exit_code") == 0 and not result.get("timed_out") and not orphaned and aborted is None
        if result.get("contained") is not True:
            body.update(verdict=QUARANTINE, reasons=["containment_not_proven: job khong rong sau grace"])
            return self._seal(name, spec, slot, body)
        child_exit = self.clock.monotonic()
        if kind == "write":
            quiescence = self._quiescence(spec, baseline, result, child_exit)
            body["quiescence"] = quiescence
            if quiescence["status"] != qm.VERIFIED:
                body.update(verdict=QUARANTINE, reasons=[f"quiescence_{quiescence['status']}"])
                return self._seal(name, spec, slot, body)
        after, bad_after = self._file_hashes(spec)
        body["file_hashes_after"] = after
        if after != before or bad_after:
            body.update(verdict=FAIL_CLEAN, reasons=["required_file_changed_during_phase"])
            return self._seal(name, spec, slot, body)
        outs = {p: (file_sha256(p) if Path(p).exists() else None) for p in spec.get("outputs", [])}
        body["outputs"] = outs
        seals: dict[str, Any] = {}
        seal_failures: list[str] = []
        for directory in spec.get("output_dirs", []):
            if not os.path.lexists(directory):
                seals[directory] = None
                continue
            try:
                seals[directory] = directory_seal(directory)
            except (ValueError, OSError) as exc:
                seals[directory] = {"kind": "seal_failed", "reason": f"{type(exc).__name__}: {str(exc)[:120]}"}
                seal_failures.append(directory)
        body["output_dirs"] = seals
        if not exit_ok:
            reason = f"child_failed: exit_code={result.get('exit_code')} timed_out={result.get('timed_out')} descendants_outlived_root={orphaned}"
            if aborted is not None:
                reason = f"aborted_by_gate: {aborted}"
            body.update(verdict=FAIL_CLEAN, reasons=[reason])
            return self._seal(name, spec, slot, body)
        if seal_failures:                                                                                   # co dau ra nhung KHONG niem phong duoc: khong biet trang thai => write QUARANTINE
            body.update(verdict=QUARANTINE if kind == "write" else FAIL_CLEAN, reasons=[f"output_seal_failed: {seal_failures}"])
            return self._seal(name, spec, slot, body)
        missing = [p for p, h in outs.items() if h is None] + [d for d, seal in seals.items() if seal is None or seal.get("files") == 0]
        if missing:
            body.update(verdict=FAIL_CLEAN, reasons=[f"expected_outputs_missing: {missing}"])
            return self._seal(name, spec, slot, body)
        body.update(verdict=PASS, reasons=[])
        return self._seal(name, spec, slot, body)

    def _seal(self, name: str, spec: Mapping[str, Any], slot: Path, body: dict[str, Any]) -> dict[str, Any]:
        """Dong dau receipt cuoi phase. Qua hard_deadline => KHONG PASS (evidence tre chi de chan doan); QUARANTINE => ghi marker."""
        if body["verdict"] == PASS and self.remaining_s() < 0:
            body.update(verdict=QUARANTINE if spec["kind"] == "write" else FAIL_CLEAN, reasons=["overall_deadline_exceeded_before_receipt"], deadline_overrun_s=round(-self.remaining_s(), 3))
        body["remaining_s_at_seal"] = round(self.remaining_s(), 3)
        receipt = self._finalize(name, spec, slot, body)
        if receipt["verdict"] == QUARANTINE:
            self._write_marker(name, receipt["receipt_sha256"], receipt["reasons"])
        return receipt

    def _quiescence(self, spec: Mapping[str, Any], baseline: Mapping[str, Any], result: Mapping[str, Any], child_exit: float) -> dict[str, Any]:
        s = self.settings
        probes: list[dict[str, Any]] = []
        verdict: dict[str, Any] = {"status": qm.UNKNOWN, "reasons": ["no_probe"], "kill_candidates": [], "assumptions": list(qm.ASSUMPTIONS)}
        for index in range(s["max_probes"]):
            wait = s["settle_s"] if index == 0 else qm.MIN_SPACING_S + 0.5
            if self.remaining_s() < wait + s["probe_cap_s"] + s["receipt_reserve_s"]:                    # khong du thoi gian cho probe ke tiep => dung, UNKNOWN (khong 'thu cho den xanh')
                verdict = {"status": qm.UNKNOWN, "reasons": ["overall_deadline_before_next_probe"], "kill_candidates": [], "assumptions": list(qm.ASSUMPTIONS)}
                break
            self.clock.sleep(wait)
            try:
                probe = self.observer.probe()
            except Exception as exc:                                                                       # noqa: BLE001
                probe = {"status": "error", "rows": [], "truncated": False, "rolling_back": None, "uptime_s": None, "error": type(exc).__name__}
            if isinstance(probe, Mapping):
                probe = {**probe, "observed_at": self.clock.monotonic()}                                  # dong ho CUA RUNNER; observer khong tu khai bao thoi gian
            probes.append(probe)
            if len(probes) >= 2:
                verdict = qm.evaluate_quiescence(selftest=self.q0["selftest"], probes=probes, child_exit_monotonic=child_exit, baseline_thread_id=baseline["baseline_thread_id"],
                                                 baseline_uptime_s=baseline["baseline_uptime_s"], app_user=spec["db"]["app_user"], db_scope=spec["db"]["db_scope"],
                                                 child_pids=result.get("pids_seen", []), probe_session_id=baseline["probe_session_id"], protected_dbs=spec["db"]["protected_dbs"])
                if verdict["status"] in (qm.VERIFIED, qm.UNKNOWN):
                    break
        late = self.remaining_s() < 0
        if late and verdict["status"] == qm.VERIFIED:                                                      # xanh nhung TRE: giu bang chung de chan doan, khong coi la hoan tat trong budget
            verdict = {**verdict, "status": qm.UNKNOWN, "reasons": ["verified_after_overall_deadline(diagnostic_only)"]}
        return {**verdict, "probe_count": len(probes), "probes": probes, "child_exit_monotonic": child_exit, "late": late}


def _baseline_problems(baseline: Any) -> list[str]:
    if not isinstance(baseline, Mapping):
        return ["not_mapping"]
    problems = []
    if type(baseline.get("baseline_thread_id")) is not int or baseline["baseline_thread_id"] < 0:
        problems.append("baseline_thread_id")
    if not _finite(baseline.get("baseline_uptime_s")) or isinstance(baseline.get("baseline_uptime_s"), bool) or baseline["baseline_uptime_s"] < 0:
        problems.append("baseline_uptime_s")
    if type(baseline.get("probe_session_id")) is not int:
        problems.append("probe_session_id")
    if type(baseline.get("app_user")) is not str or not baseline.get("app_user"):
        problems.append("app_user")
    if type(baseline.get("server_uuid")) is not str or not baseline.get("server_uuid"):
        problems.append("server_uuid")
    return problems


def _public_launch(result: Mapping[str, Any]) -> dict[str, Any]:
    keep = ("exit_code", "timed_out", "aborted_by_gate", "gate_polls", "killed_tree", "contained", "pids_seen", "stdout_path", "stderr_path", "stdout_sha256", "stderr_sha256", "started", "ended")
    return {k: result.get(k) for k in keep}
