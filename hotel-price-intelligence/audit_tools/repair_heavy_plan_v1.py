"""Bo dung KE HOACH cho cac phase NANG cua repair (clone / init + verify-config / build) cho `repair_run_v1`. THUAN: chi dung spec + plan + hash + ban nhap approval; KHONG chay, KHONG DB, KHONG quyen thuc thi.

Ba ke hoach rieng (moi ke hoach co approval ngoai rieng, thu muc plan rieng, pin Q0 rieng; chi chay ke hoach sau khi ke hoach truoc PASS):
  clone  : `ph3_clone`                                   (write)  sao `SOURCE` -> `TARGET` moi (KHONG `--drop-existing`; clone tu choi neu TARGET da ton tai)
  init   : `ph4_init_h1`, `ph4_verify_h1`, `ph4_init_h3`, `ph4_verify_h3`   (write, readonly, write, readonly)  tao dataset_version moi + so config voi bien lai du doan doc lap
  build  : `ph5_build_h1`, `ph5_build_h3`                (write)  build chinh thuc cua builder; chi dung duoc khi 2 bao cao verify da PASS (pin byte trong spec)

Moi tham so, duong dan, ten DB/version la HANG SO co ten hoac doi so tuong minh (khong placeholder); doi bat ky gia tri nao => plan hash doi => approval cu vo hieu. Ten DB/version mac dinh la ung vien (GPT 73-76),
doi ngay/ten => approval moi. `db_scope` la CHINH XAC DB ma lenh duoc phep cham; `protected_dbs` = danh sach cam toi thieu (DB van hanh) hop voi (DB nhin thay trong Q0 receipt tru scope): phan sau chi la BO SUNG, khong thay the.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import types
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
REPO = PROJECT.parent
ML = PROJECT / "ml"
BACKEND = PROJECT / "backend"


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


rp = _load("repair_plan_v1_for_heavy", "repair_plan_v1.py")
rr = rp.rr
rm = _load("repair_manifest_v1_for_heavy", "repair_manifest_v1.py")
vi = _load("repair_verify_init_for_heavy", "repair_verify_init_v1.py")

# --- HANG SO ung vien (ten/ngay co the doi => approval moi)
SOURCE_DB = "warehouse_20261004_3src"
TARGET_DB = "warehouse_dsdev_20261009_3src_nf"
VERSIONS = {"h1": ("ds_20261010_nf1", 1), "h3": ("ds_20261010_nf3", 3)}                                 # 10/10: `ds_20261009_nf1` da bi dung (init thieu --n1-policy, verify FAIL) => KHONG dung lai ten
ANOMALY_CUTOFF = "2026-10-05T00:00:00Z"
OPERATIONAL_DENY = ("hotel_price_intel", "hotel_price_intel_fullscan_20260924")                            # DB van hanh/da biet cua crawler: KHONG BAO GIO trong scope, khong phu thuoc Q0 (GPT 78 §5.1)
DEADLINES = {"clone": 900.0, "init": 300.0, "verify": 120.0, "build": 1800.0}                              # ung vien: clone do ~242 s, build do ~6 phut/version (log 06/10); chua do lai
TOTAL_SLACK_S = 120.0
SETUP_SQL = BACKEND / "app" / "database" / "setup.sql"
REGISTRY_JSON = BACKEND / "app" / "database" / "anomaly_registry.json"
HOLIDAYS_CSV = PROJECT / "data" / "vn_holidays.csv"
N1_POLICY_DIR = ML / "policies" / "n1"
N1_POLICY_FILE = N1_POLICY_DIR / "n1_policy_v1.json"                                                      # config cu (dev1b/dev3b) ghim `n1_policy` => init moi PHAI co --n1-policy de config trung du doan
DATASET_ROOT = REPO / "outputs" / "datasets"
DEV_PREFIX = "warehouse_dsdev_"
IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
VERSION_RE = re.compile(r"^ds_[0-9]{8}_[a-z0-9_]{1,24}$")


def heavy_env() -> dict[str, str]:
    """Env child: 6 bien cua Q0/Ph1 + `PYTHONUNBUFFERED=1` (lan chay cu dat bien nay de log tien trinh hien ngay). Khong secret; DB creds do `backend/.env` nap trong child."""
    return {**rp.child_env(), "PYTHONUNBUFFERED": "1"}


# ----------------------------------------------------------------------------- dong dong import tinh + tep du lieu
def _local_module_file(name: str) -> Path | None:
    parts = name.split(".")
    for base in (ML, BACKEND):
        candidate = base.joinpath(*parts)
        if candidate.with_suffix(".py").is_file():
            return candidate.with_suffix(".py")
        if (candidate / "__init__.py").is_file():
            return candidate / "__init__.py"
    return None


def _imports_of(path: Path, module_name: str | None, is_package: bool) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = (module_name or "").split(".") if is_package else (module_name or "").split(".")[:-1]
                package = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                base = ".".join(package + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def static_closure(script: Path) -> list[Path]:
    """Script + moi module dataset_builder/app.* ma no (bac cau) import, ke ca import trong ham va `__init__` cua goi cha (AST, khong theo sys.modules). Da sap xep."""
    seen: dict[str, Path] = {}
    queue: list[tuple[Path, str | None, bool]] = [(script, None, False)]
    while queue:
        path, module, is_package = queue.pop()
        for name in _imports_of(path, module, is_package):
            if name in seen:
                continue
            file = _local_module_file(name)
            if file is None:
                continue
            seen[name] = file
            queue.append((file, name, file.name == "__init__.py"))
            parent = name.rsplit(".", 1)[0] if "." in name else None
            while parent and parent not in seen:
                parent_file = _local_module_file(parent)
                if parent_file is not None:
                    seen[parent] = parent_file
                    queue.append((parent_file, parent, parent_file.name == "__init__.py"))
                parent = parent.rsplit(".", 1)[0] if "." in parent else None
    return sorted({script, *seen.values()}, key=str)


def builder_manifest_files() -> list[Path]:
    """Cac tep thuoc danh tinh ma cua builder (manifest `code_identity`, tinh bang AST, khong import package) — phase init/build kiem lai chinh tap nay."""
    return [REPO / rel for rel in rm.compute_manifest(REPO)["files"]]


def n1_policy_files() -> list[Path]:
    """Moi tep THUONG trong `ml/policies/n1/` (policy + bang chung): init ghim descriptor cua chung vao config, moi step/build doc lai tu dia va doi chieu => phai duoc ghim hash trong plan."""
    return sorted(p for p in N1_POLICY_DIR.iterdir() if p.is_file())


def required_files(python: str, script: Path, *, extra: Sequence[Path] = (), manifest: bool = False) -> dict[str, str]:
    files = [Path(python), *static_closure(script), *extra, *(builder_manifest_files() if manifest else [])]
    return rp.pins_for(sorted({str(Path(f)) for f in files}))


# ----------------------------------------------------------------------------- khoi db
def q0_facts(path: Path) -> dict[str, Any]:
    """app_user + danh sach DB DA DO trong Q0 receipt (hash byte cua tep nam trong plan qua pin Q0). Receipt khong hop le/danh sach cat => loi."""
    data = Path(path).read_bytes()
    doc = json.loads(data.decode("utf-8"))
    details = doc.get("details") if isinstance(doc, dict) else None
    if not (isinstance(doc, dict) and doc.get("kind") == "q0_selftest_receipt" and isinstance(details, Mapping)):
        raise ValueError("q0_receipt_malformed")
    user, databases = details.get("app_user"), details.get("databases")
    if not (isinstance(user, str) and user and details.get("databases_truncated") is False and isinstance(databases, list) and databases and all(isinstance(x, str) for x in databases)):
        raise ValueError("q0_receipt_missing_user_or_databases")
    return {"app_user": user, "databases": sorted(databases), "sha256": hashlib.sha256(data).hexdigest()}


def db_block(*, app_user: str, scope: Sequence[str], q0_databases: Sequence[str], deny: Sequence[str] = OPERATIONAL_DENY) -> dict[str, Any]:
    scope_set = set(scope)
    if not scope_set or any(not IDENT_RE.match(x) for x in scope_set):
        raise ValueError("db_scope_invalid")
    if scope_set & set(deny):
        raise ValueError(f"db_scope_overlaps_explicit_deny: {sorted(scope_set & set(deny))}")
    protected = sorted((set(deny) | set(q0_databases)) - scope_set)                                      # deny toi thieu + (nhin thay trong Q0 tru scope) la BO SUNG
    return {"app_user": app_user, "db_scope": sorted(scope_set), "protected_dbs": protected}


def _check_names(source: str, target: str) -> None:
    for name in (source, target):
        if not IDENT_RE.match(name):
            raise ValueError(f"database_name_invalid: {name!r}")
    if not target.startswith(DEV_PREFIX) or source == target or source.startswith(DEV_PREFIX):
        raise ValueError("clone_names_invalid: target phai bat dau bang warehouse_dsdev_, source phai la warehouse da promote khac target")


def _check_versions(versions: Mapping[str, tuple[str, int]]) -> None:
    if set(versions) != {"h1", "h3"} or any(not VERSION_RE.match(v) or not isinstance(k, int) or k < 1 for v, k in versions.values()) or len({v for v, _ in versions.values()}) != 2:
        raise ValueError("versions_invalid")


# ----------------------------------------------------------------------------- spec
def clone_spec(*, python: str, source: str, target: str, db: Mapping[str, Any]) -> dict[str, Any]:
    _check_names(source, target)
    script = ML / "scripts" / "clone_warehouse_for_dev.py"
    return {"name": "ph3_clone", "kind": "write", "argv": [python, str(script), "--source", source, "--target", target], "cwd": str(ML), "env": heavy_env(),
            "required_files": required_files(python, script, extra=[SETUP_SQL]), "deadline_s": DEADLINES["clone"], "db": dict(db)}


def init_spec(*, python: str, name: str, target: str, version: str, horizon: int, db: Mapping[str, Any]) -> dict[str, Any]:
    script = ML / "scripts" / "init_dataset_build.py"
    argv = [python, str(script), "--database", target, "--dataset-version", version, "--purpose", "dev", "--anomaly-cutoff", ANOMALY_CUTOFF, "--evaluation-horizons", str(horizon), "--n1-policy", str(N1_POLICY_FILE)]
    return {"name": name, "kind": "write", "argv": argv, "cwd": str(ML), "env": heavy_env(),
            "required_files": required_files(python, script, extra=[SETUP_SQL, REGISTRY_JSON, HOLIDAYS_CSV, *n1_policy_files()], manifest=True), "deadline_s": DEADLINES["init"], "db": dict(db)}


def verify_spec(*, python: str, name: str, target: str, version: str, prediction_receipt: Path, out_report: Path) -> dict[str, Any]:
    script = HERE / "repair_verify_init_v1.py"
    argv = [python, str(script), "--db", target, "--dataset-version", version, "--prediction-receipt", str(prediction_receipt), "--out", str(out_report)]
    return {"name": name, "kind": "readonly", "argv": argv, "cwd": str(BACKEND), "env": rp.child_env(), "required_files": required_files(python, script, extra=[prediction_receipt]),
            "deadline_s": DEADLINES["verify"], "outputs": [str(out_report)]}


def build_spec(*, python: str, name: str, target: str, version: str, verify_report: Path, db: Mapping[str, Any], h2_pins: Sequence[Path] = ()) -> dict[str, Any]:
    script = ML / "scripts" / "build_dataset.py"
    argv = [python, str(script), "--database", target, "--dataset-version", version, "--apply"]
    return {"name": name, "kind": "write", "argv": argv, "cwd": str(ML), "env": heavy_env(),
            "required_files": required_files(python, script, extra=[SETUP_SQL, REGISTRY_JSON, HOLIDAYS_CSV, *n1_policy_files(), verify_report, *h2_pins], manifest=True), "deadline_s": DEADLINES["build"], "db": dict(db),
            "output_dirs": [str(DATASET_ROOT / version)]}                                                    # thu muc dataset: KHONG duoc ton tai truoc, niem phong theo manifest sau build (runner `directory_seal`)


def suggest_total_budget(specs: Sequence[Mapping[str, Any]], settings: Mapping[str, Any], *, observer_overhead_s: float) -> float:
    """Tong ngan sach CHAC CHAN du cho moi phase dung het deadline: Σ (deadline + du tru sau launch + (write: baseline_cap + overhead observer)) + margin + slack, lam tron len 60 s. Vuot 21600 => loi."""
    total = settings["margin_s"] + TOTAL_SLACK_S
    for spec in specs:
        reserve = rr.reserve_after_launch(spec, settings)                                                   # CUNG ham du tru voi Runner (gom niem phong thu muc dau ra)
        if spec["kind"] == "write":
            reserve += settings["baseline_cap_s"] + observer_overhead_s
        total += spec["deadline_s"] + reserve
    total = math.ceil(total / 60.0) * 60.0
    if total > 21600:
        raise ValueError(f"total_budget_exceeds_cap: {total}")
    return float(total)


# ----------------------------------------------------------------------------- ke hoach
def _observer_facts() -> tuple[str, float]:
    proxy = _load("repair_observer_proxy_for_heavy", "repair_observer_proxy_v1.py")
    probe = proxy.BoundedObserver(["x"], cwd=".", env={}, stderr_path="unused.err")
    return probe.tooling_sha256, probe.overhead_s


def _plan(specs: list[dict[str, Any]], q0_path: Path) -> dict[str, Any]:
    pin, overhead = _observer_facts()
    base = dict(rr.DEFAULT_SETTINGS)
    settings = {**base, "total_budget_s": suggest_total_budget(specs, base, observer_overhead_s=overhead)}
    return rp.build_plan(specs, settings, q0_receipt_sha256=hashlib.sha256(Path(q0_path).read_bytes()).hexdigest(), launcher_source_sha256=rr.file_sha256(HERE / "windows_job_v1.py"), observer_tooling_sha256=pin)


def clone_plan(*, python: str, q0_receipt: Path, source: str = SOURCE_DB, target: str = TARGET_DB) -> dict[str, Any]:
    facts = q0_facts(q0_receipt)
    db = db_block(app_user=facts["app_user"], scope=[source, target], q0_databases=facts["databases"])        # clone doc `source` (INSERT..SELECT + dem batch) va ghi `target`
    return _plan([clone_spec(python=python, source=source, target=target, db=db)], q0_receipt)


def init_plan(*, python: str, q0_receipt: Path, prediction_receipts: Mapping[str, Path], report_dir: Path, source: str = SOURCE_DB, target: str = TARGET_DB,
              versions: Mapping[str, tuple[str, int]] = VERSIONS) -> dict[str, Any]:
    _check_versions(versions)
    if set(prediction_receipts) != {"h1", "h3"}:
        raise ValueError("prediction_receipts_must_be_h1_and_h3")
    facts = q0_facts(q0_receipt)
    db = db_block(app_user=facts["app_user"], scope=[target], q0_databases=facts["databases"], deny=(*OPERATIONAL_DENY, source))
    specs: list[dict[str, Any]] = []
    for key in ("h1", "h3"):
        version, horizon = versions[key]
        specs.append(init_spec(python=python, name=f"ph4_init_{key}", target=target, version=version, horizon=horizon, db=db))
        specs.append(verify_spec(python=python, name=f"ph4_verify_{key}", target=target, version=version, prediction_receipt=Path(prediction_receipts[key]), out_report=Path(report_dir) / f"verify_{version}.json"))
    return _plan(specs, q0_receipt)


EXPECTED_H2_PHASES = ("ph4_init_h1", "ph4_verify_h1", "ph4_init_h3", "ph4_verify_h3")


def verify_report_problems(report: Any, *, version: str, target: str, prediction: Mapping[str, Any], prediction_file_sha256: str) -> list[str]:
    """Bao cao verify PHAI la dau ra DAY DU cua `repair_verify_init_v1` cho dung version/DB/bien lai du doan: tap check chinh xac, tung gia tri `is True`, 1 hang, status/step, SQL-set cua verifier da duyet,
    hash luu/tinh lai/du kien trung bien lai doc lap, hash ma == bien lai, hash byte bien lai == pin. Khong chi tin chu `PASS`."""
    if not isinstance(report, Mapping):
        return ["report_not_a_mapping"]
    problems = [key for key, expected in (("kind", "init_config_verification"), ("verdict", "PASS"), ("dataset_version", version), ("database", target), ("status", "running"),
                                          ("last_completed_step", "initialized")) if report.get(key) != expected]
    if type(report.get("rows")) is not int or report.get("rows") != 1:
        problems.append("rows")
    checks = report.get("checks")
    if not (isinstance(checks, Mapping) and set(checks) == set(vi.REQUIRED_CHECKS) and all(value is True for value in checks.values())):
        problems.append("checks")
    if report.get("sql_set_sha256") != vi.sql_set_sha256():
        problems.append("sql_set_sha256")
    predicted = prediction.get("predicted_config_sha256")
    problems += [key for key in ("predicted_config_sha256", "stored_config_sha256", "recomputed_config_sha256") if report.get(key) != predicted]
    if report.get("expected_new_code_sha256") != prediction.get("new_code_sha256") or report.get("config_builder_code_sha256") != prediction.get("new_code_sha256"):
        problems.append("code_sha256")
    if report.get("prediction_receipt_sha256") != prediction_file_sha256:
        problems.append("prediction_receipt_sha256")
    return problems


def load_verify_report(path: Path, *, version: str, target: str, prediction_receipt: Path) -> dict[str, Any]:
    try:
        report = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        raise ValueError(f"verify_report_unreadable_for_{version}") from None
    prediction = vi.load_prediction_receipt(prediction_receipt)
    problems = verify_report_problems(report, version=version, target=target, prediction=prediction, prediction_file_sha256=prediction["file_sha256"])
    if problems:
        raise ValueError(f"verify_report_rejected_for_{version}: {problems}")
    return report


def check_h2_chain(*, h2_plan_dir: Path, h2_plan_sha256: str, python: str, target: str, versions: Mapping[str, tuple[str, int]], prediction_receipts: Mapping[str, Path],
                   report_dir: Path) -> list[Path]:
    """Chuoi receipt cua H2 DA CHAY: thu muc chi co PLAN_BOUND + 4 receipt dung ten/thu tu, moi receipt PASS, noi dai bang `prev_receipt_sha256`, cung mot plan hash DA DUYET, receipt cua `verify` khop spec
    suy lai tu chinh dau vao (spec/argv hash) va ghi DUNG hash byte bao cao. Tra danh sach tep (PLAN_BOUND + 2 receipt verify) de GHIM vao spec build."""
    directory = Path(h2_plan_dir)
    expected_names = [f"receipt_{index:02d}_{name}.json" for index, name in enumerate(EXPECTED_H2_PHASES, 1)]
    present = sorted(entry.name for entry in directory.iterdir())
    if present != sorted(["PLAN_BOUND.json", *expected_names]):
        raise ValueError(f"h2_plan_dir_unexpected_content: {present}")
    bound = json.loads((directory / "PLAN_BOUND.json").read_bytes().decode("utf-8"))
    if bound.get("computed_plan_sha256") != h2_plan_sha256 or bound.get("approved_plan_sha256") != h2_plan_sha256:
        raise ValueError("h2_plan_bound_hash_mismatch")
    previous: str | None = None
    pins: list[Path] = [directory / "PLAN_BOUND.json"]
    for index, phase in enumerate(EXPECTED_H2_PHASES, 1):
        path = directory / expected_names[index - 1]
        doc = json.loads(path.read_bytes().decode("utf-8"))
        problems = [key for key, expected in (("phase", phase), ("seq", index), ("verdict", "PASS"), ("reasons", []), ("prev_receipt_sha256", previous), ("approved_plan_sha256", h2_plan_sha256),
                                              ("computed_plan_sha256", h2_plan_sha256)) if doc.get(key) != expected]
        if problems:
            raise ValueError(f"h2_receipt_rejected: {phase}: {problems}")
        previous = rr.sha256_hex(rr.canonical_json(doc))
        if phase.startswith("ph4_verify_"):
            key = phase.rsplit("_", 1)[1]
            version = versions[key][0]
            report = Path(report_dir) / f"verify_{version}.json"
            spec = verify_spec(python=python, name=phase, target=target, version=version, prediction_receipt=Path(prediction_receipts[key]), out_report=report)
            if doc.get("spec_sha256") != rr.spec_sha256(spec) or doc.get("argv_sha256") != rr.argv_sha256(spec):
                raise ValueError(f"h2_receipt_rejected: {phase}: spec_or_argv_hash_differs_from_the_spec_rebuilt_from_these_inputs")
            if doc.get("outputs") != {str(report): rr.file_sha256(report)}:
                raise ValueError(f"h2_receipt_rejected: {phase}: report_bytes_differ_from_the_receipt_output_hash")
            pins.append(path)
    return pins


def build_plan(*, python: str, q0_receipt: Path, verify_reports: Mapping[str, Path], prediction_receipts: Mapping[str, Path], report_dir: Path, h2_plan_dir: Path, h2_plan_sha256: str,
               source: str = SOURCE_DB, target: str = TARGET_DB, versions: Mapping[str, tuple[str, int]] = VERSIONS) -> dict[str, Any]:
    _check_versions(versions)
    if set(verify_reports) != {"h1", "h3"} or set(prediction_receipts) != {"h1", "h3"}:
        raise ValueError("verify_reports_and_prediction_receipts_must_be_h1_and_h3")
    if not rr.SHA64_RE.match(str(h2_plan_sha256)):
        raise ValueError("h2_plan_sha256_malformed")
    facts = q0_facts(q0_receipt)
    db = db_block(app_user=facts["app_user"], scope=[target], q0_databases=facts["databases"], deny=(*OPERATIONAL_DENY, source))
    for key in ("h1", "h3"):                                                                                  # chi dung bao cao DAY DU, dung version/DB/bien lai, TRUOC khi dung bat ky spec nao
        load_verify_report(verify_reports[key], version=versions[key][0], target=target, prediction_receipt=prediction_receipts[key])
        if Path(verify_reports[key]) != Path(report_dir) / f"verify_{versions[key][0]}.json":
            raise ValueError(f"verify_report_path_differs_from_the_h2_output_path_for_{versions[key][0]}")
    pins = check_h2_chain(h2_plan_dir=h2_plan_dir, h2_plan_sha256=h2_plan_sha256, python=python, target=target, versions=versions, prediction_receipts=prediction_receipts, report_dir=report_dir)
    specs = []
    for key in ("h1", "h3"):
        version, _ = versions[key]
        specs.append(build_spec(python=python, name=f"ph5_build_{key}", target=target, version=version, verify_report=Path(verify_reports[key]), db=db, h2_pins=pins))
    return _plan(specs, q0_receipt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("clone", "init", "build"), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--q0-receipt", type=Path, required=True)
    parser.add_argument("--source", default=SOURCE_DB)
    parser.add_argument("--target", default=TARGET_DB)
    parser.add_argument("--prediction-receipt", action="append", default=[], metavar="h1=PATH", help="stage init/build: bien lai du doan config cho h1 va h3")
    parser.add_argument("--verify-report", action="append", default=[], metavar="h1=PATH", help="stage build: bao cao verify (dau ra cua H2) cho h1 va h3")
    parser.add_argument("--report-dir", type=Path, default=None, help="stage init/build: thu muc bao cao verify (verify_<version>.json)")
    parser.add_argument("--h2-plan-dir", type=Path, default=None, help="stage build: thu muc plan cua H2 DA CHAY (receipt chain)")
    parser.add_argument("--h2-plan-sha256", default=None, help="stage build: plan hash DA DUYET cua H2")
    args = parser.parse_args(argv)
    pairs = lambda items: dict(item.split("=", 1) for item in items)                                        # noqa: E731
    if args.stage == "clone":
        plan = clone_plan(python=args.python, q0_receipt=args.q0_receipt, source=args.source, target=args.target)
    elif args.stage == "init":
        if args.report_dir is None:
            raise SystemExit("stage init can --report-dir")
        plan = init_plan(python=args.python, q0_receipt=args.q0_receipt, prediction_receipts={k: Path(v) for k, v in pairs(args.prediction_receipt).items()}, report_dir=args.report_dir, source=args.source,
                         target=args.target)
    else:
        if args.report_dir is None or args.h2_plan_dir is None or args.h2_plan_sha256 is None:
            raise SystemExit("stage build can --report-dir --h2-plan-dir --h2-plan-sha256")
        plan = build_plan(python=args.python, q0_receipt=args.q0_receipt, verify_reports={k: Path(v) for k, v in pairs(args.verify_report).items()},
                          prediction_receipts={k: Path(v) for k, v in pairs(args.prediction_receipt).items()}, report_dir=args.report_dir, h2_plan_dir=args.h2_plan_dir, h2_plan_sha256=args.h2_plan_sha256,
                          source=args.source, target=args.target)
    written = rp.emit(args.out_dir, f"heavy_{args.stage}", plan, f"Phase nang: {args.stage}")
    print(json.dumps({"stage": args.stage, "plan_sha256": plan["plan_sha256"], "files": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
