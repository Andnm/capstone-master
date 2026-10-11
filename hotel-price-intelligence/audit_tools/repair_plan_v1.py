"""Bo dung KE HOACH (plan) bat bien cho `repair_phase_runner_v2` — Q0 (self-test server) va Ph1 (doc ledger cu x2 + P4). Chi DUNG spec/plan + TINH hash + sinh ban nhap approval; KHONG chay gi, KHONG DB.

Dau vao la HANG SO co ten (duong dan that, pin that); khong placeholder. Doi bat ky gia tri nao => plan hash doi => approval cu vo hieu. Ph3..Ph6 (clone/init/build/ledger moi) chua co o day:
chi dung khi runner da duoc duyet va nguoi dung cap quyen cho tung target/lenh.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import types
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
REPO = PROJECT.parent


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


rr = _load("repair_phase_runner_v2_for_plan", "repair_phase_runner_v2.py")

# --- HANG SO da ghim (nguon: 61/63/66 + do truc tiep); KHONG suy tu DB
EXISTING_DB = "warehouse_dsdev_20261004_3src"
OLD_DATASETS = {
    "ds_20261006_dev1b": {"samples_sha256": "1a4a099ac6d11fcced901e662987f78e7f03e79c60911eb092b19213e693dce1", "config_sha256": "6494c1d4ece098e12855644f26780eecfe6706be32baee8a8f486b8b10069928"},
    "ds_20261006_dev3b": {"samples_sha256": "c0a3aef94e9038578b8dacc802c2d04fa59f44c3629d1c23456571f9cc7d1d03", "config_sha256": "45f2fc6ab177505de3b5a1590f6cab2988bb9e05ac72a1996127c2760289cbd8"},
}
OLD_CODE_SHA256 = "562d22af69cb66b9d2c5bbbadbff1d325bc447cc9ffdb5a5ddf102ebf75f278d"
EXPECTED_ROWS = 146431
DEFAULT_PATH = r"C:\Program Files\Git\cmd;C:\Windows\System32;C:\Windows"                              # git bat buoc (code_identity goi `git`), khong them thu muc nao khac


def child_env(*, system_root: str = r"C:\Windows", temp: str = r"C:\Windows\Temp", path: str = DEFAULT_PATH) -> dict[str, str]:
    """Moi truong child TUONG MINH (chi ten trong allowlist cua runner; khong secret — credentials DB do `backend/.env` nap ben trong child). UTF-8 de log tieng Viet khong loi ma hoa."""
    return {"SystemRoot": system_root, "TEMP": temp, "TMP": temp, "PATH": path, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def pins_for(paths: Sequence[str | Path]) -> dict[str, str]:
    """{duong dan: SHA-256 byte hien tai} (dong goi byte-closure); file thieu => loi (khong bo qua)."""
    return {str(p): rr.file_sha256(p) for p in paths}


def backend_closure(backend: Path) -> list[Path]:
    return [backend / "app" / "core" / "config.py", backend / "app" / "warehouse" / "connection.py", backend / "app" / "warehouse" / "naming.py", backend / "app" / "warehouse" / "errors.py"]


def q0_spec(*, python: str, backend: Path, out_receipt: Path, deadline_s: float = 180.0, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    scripts = [HERE / "repair_q0_v1.py", HERE / "repair_db_observer_v1.py", HERE / "quiescence_model_v1.py"]
    return {"name": "q0_selftest", "kind": "readonly", "argv": [python, str(HERE / "repair_q0_v1.py"), "--out", str(out_receipt), "--max-statements", "40", "--wait-gone-s", "10"],
            "cwd": str(backend), "env": dict(env or child_env()), "required_files": pins_for([python, *scripts, *backend_closure(backend)]), "deadline_s": deadline_s, "outputs": [str(out_receipt)]}


def producer_spec(*, name: str, dataset: str, python: str, backend: Path, datasets_dir: Path, producer_approval: Path, crawl_evidence: Path, deadline_s: float = 900.0,
                  env: Mapping[str, str] | None = None, budget_seconds: int = 600, max_queries: int = 200, max_batches: int = 160) -> dict[str, Any]:
    versions = ",".join(OLD_DATASETS)
    pins = OLD_DATASETS[dataset]
    config_args: list[str] = []
    for version, info in OLD_DATASETS.items():
        config_args += ["--expected-config-sha256", f"{version}={info['config_sha256']}"]
    producer = HERE / "warehouse_flag_ledger_v1.py"
    closure = [producer, HERE / "repair_parity_v1.py", HERE / "warehouse_audit_v3.py", producer_approval, *backend_closure(backend)]
    argv = [python, str(producer), "--db", EXISTING_DB, "--dataset", dataset, "--dataset-dir", str(datasets_dir / dataset), "--expected-samples-sha256", pins["samples_sha256"],
            "--expected-rows", str(EXPECTED_ROWS), "--snapshot-versions", versions, "--expected-old-code-sha256", OLD_CODE_SHA256, *config_args, "--run", "--gpt-approval", str(producer_approval),
            "--crawl-evidence", str(crawl_evidence), "--budget-seconds", str(budget_seconds), "--max-queries", str(max_queries), "--max-batches", str(max_batches)]
    return {"name": name, "kind": "readonly", "argv": argv, "cwd": str(backend), "env": dict(env or child_env()), "required_files": pins_for([python, *closure]), "deadline_s": deadline_s}


def ph1_specs(**common: Any) -> list[dict[str, Any]]:
    """Ph1 = hai producer doc ledger cu TUAN TU (moi dataset mot process/receipt), moi lan kem P4 snapshot cua CHINH lan chay."""
    return [producer_spec(name="ph1_old_ledger_h1", dataset="ds_20261006_dev1b", **common), producer_spec(name="ph1_old_ledger_h3", dataset="ds_20261006_dev3b", **common)]


def validate_all(specs: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]) -> list[str]:
    errors = rr.validate_settings(settings)
    for index, spec in enumerate(specs):
        errors += [f"spec[{index}]: {e}" for e in rr.validate_spec(spec)]
    return errors


def build_plan(specs: Sequence[Mapping[str, Any]], settings: Mapping[str, Any], *, q0_receipt_sha256: str | None, launcher_source_sha256: str, observer_tooling_sha256: str | None = None) -> dict[str, Any]:
    """Tai lieu ke hoach + hash. `launcher_source_sha256` = hash `windows_job_v1.py` (cong cu Job Object se chay); pin cong cu = hash runner/model Q/Job HIEN TAI."""
    errors = validate_all(specs, settings)
    if errors:
        raise ValueError(f"plan_invalid: {errors}")
    tooling = {"runner_sha256": rr.file_sha256(HERE / "repair_phase_runner_v2.py"), "quiescence_model_sha256": rr.qm.__source_sha256__, "windows_job_sha256": launcher_source_sha256,
               "observer_sha256": observer_tooling_sha256}
    document = rr.plan_document(specs, settings, tooling, q0_receipt_sha256)
    return {"document": document, "plan_sha256": rr.plan_sha256(specs, settings, tooling, q0_receipt_sha256)}


def render_approval_draft(plan: Mapping[str, Any], *, title: str) -> str:
    """Ban NHAP approval (de GPT sua/duyet): dong `APPROVED_PLAN_SHA256`, bang phase (ten/kind/deadline/argv rut gon/hash file) de nguoi doc doi chieu. KHONG chua secret."""
    doc = plan["document"]
    lines = [f"# {title} — BAN NHAP approval (chua co hieu luc)", "",
             "> NHAP khong phai authority: approval THAT la mot tep `NN-gpt-*.md` nam truc tiep trong thu muc thao luan, co dung nam dong duoi day (xem `repair_run_v1`). Hai dong cuoi can dien tu `--check-only`.", "",
             f"APPROVED_PLAN_SHA256: `{plan['plan_sha256']}`", "APPROVED_NOT_AFTER_UTC: <YYYY-MM-DDTHH:MM:SSZ: han dung, nguoi duyet dien>",
             "APPROVED_PLAN_DIR: <duong dan tuyet doi cua thu muc plan MOI/RONG cho dung lan chay nay, nguoi duyet dien>", f"APPROVED_ENTRYPOINT_SHA256: `{rr.file_sha256(HERE / 'repair_run_v1.py')}`",
             "APPROVED_INVOCATION_SHA256: <computed_invocation_sha256 do `repair_run_v1 --check-only` in ra cho dung lenh goi>", "", "## Cai dat an toan (da nam trong hash)", "", "```json",
             json.dumps(doc["settings"], indent=2, sort_keys=True), "```", "", "## Cong cu da ghim (trong hash)", "", "```json", json.dumps(doc["expected_tooling"], indent=2, sort_keys=True), "```", "",
             f"Q0 receipt pin: `{doc['q0_receipt_sha256']}`", "", "## Cac phase (theo thu tu, trong hash)", ""]
    for index, spec in enumerate(doc["specs"], 1):
        lines += [f"### {index}. `{spec['name']}` — kind `{spec['kind']}`, deadline {spec['deadline_s']} s", "", f"- spec_sha256: `{rr.spec_sha256(spec)}`", f"- argv_sha256: `{rr.argv_sha256(spec)}`",
                  f"- cwd: `{spec['cwd']}`", f"- env (ten + gia tri, khong secret): `{json.dumps(spec['env'], sort_keys=True)}`", "- argv:", "", "```", *spec["argv"], "```", "", "- required_files (byte-closure):", ""]
        lines += [f"  - `{path}` -> `{sha}`" for path, sha in sorted(spec["required_files"].items())]
        lines += ["", f"- outputs: `{json.dumps(spec.get('outputs', []))}`", ""]
    return "\n".join(lines) + "\n"


def emit(out_dir: Path, name: str, plan: Mapping[str, Any], title: str) -> dict[str, str]:
    """Ghi `<name>.plan.json` + `<name>.approval_DRAFT.md` bang `xb` (khong ghi de); tra hash tep."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for suffix, data in ((".plan.json", json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")), (".approval_DRAFT.md", render_approval_draft(plan, title=title).encode("utf-8"))):
        path = out_dir / f"{name}{suffix}"
        with open(path, "xb") as handle:
            handle.write(data)
        written[str(path)] = hashlib.sha256(data).hexdigest()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", required=True, help="interpreter builder (eda/.venv/Scripts/python.exe)")
    parser.add_argument("--datasets-dir", type=Path, required=True)
    parser.add_argument("--producer-approval", type=Path, required=True, help="tep approval cua producer (APPROVED_CONTROLLER_SHA256/SQL_SET/MODES) — phai TON TAI")
    parser.add_argument("--crawl-evidence", type=Path, required=True)
    parser.add_argument("--q0-out-receipt", type=Path, required=True)
    parser.add_argument("--q0-receipt-sha256", default=None, help="pin Q0 receipt (chi cho ke hoach co phase write)")
    args = parser.parse_args(argv)
    backend = PROJECT / "backend"
    wj_sha = rr.file_sha256(HERE / "windows_job_v1.py")
    settings = {**rr.DEFAULT_SETTINGS, "total_budget_s": 3000.0}
    q0 = build_plan([q0_spec(python=args.python, backend=backend, out_receipt=args.q0_out_receipt)], {**rr.DEFAULT_SETTINGS, "total_budget_s": 600.0}, q0_receipt_sha256=None, launcher_source_sha256=wj_sha)
    ph1 = build_plan(ph1_specs(python=args.python, backend=backend, datasets_dir=args.datasets_dir, producer_approval=args.producer_approval, crawl_evidence=args.crawl_evidence), settings,
                     q0_receipt_sha256=args.q0_receipt_sha256, launcher_source_sha256=wj_sha)
    written = emit(args.out_dir, "q0", q0, "Q0 self-test server (chi doc)")
    written.update(emit(args.out_dir, "ph1", ph1, "Ph1 doc ledger cu x2 + P4 (chi doc)"))
    print(json.dumps({"q0_plan_sha256": q0["plan_sha256"], "ph1_plan_sha256": ph1["plan_sha256"], "files": written}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
