# BAN COPY NGUYEN VAN (khong sua tay) tu `ml/regression/training/provenance.py`.
# SHA-256 (chuan hoa LF) cua nguon luc copy: 4facc91cd21ebee62d4e45419201ac35972c62294f1627748f23b52fbe47b98b
# Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y.
# Pham vi: CALENDAR_MANIFEST, CALENDAR_SNAPSHOT, CONTRACT_NAME, KNOWN_HORIZONS, DATASET_FILES, _SHA, DatasetVerificationError, file_sha256, verify_dataset, PURPOSES, SUFFICIENCY_STATUSES, _DATE_KEYS, _contract_content_problems, _read_contract, verify_frame.
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .n1_policy import snapshot_members


CALENDAR_MANIFEST = "calendar_input.json"


CALENDAR_SNAPSHOT = "inputs/vn_holidays.csv"


CONTRACT_NAME = "dataset_contract.json"


KNOWN_HORIZONS = (1, 3, 7, 14)


DATASET_FILES = ("samples.parquet", "data_dictionary.csv", "coverage_report.json", "sufficiency_report.json", CALENDAR_MANIFEST, CALENDAR_SNAPSHOT, CONTRACT_NAME)


_SHA = re.compile(r"^[0-9a-f]{64}$")


class DatasetVerificationError(RuntimeError):
    pass


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset(dataset_dir: Path | str) -> dict[str, Any]:
    """Fail-closed truoc khi doc Parquet. Tra metadata da XAC MINH (hash that, khong phai hash khai bao)."""
    dataset_dir = Path(dataset_dir)
    checks_path = dataset_dir / "output_checksums.json"
    if not checks_path.exists():
        raise DatasetVerificationError(f"thieu {checks_path} - khong xac minh duoc dataset.")
    declared = json.loads(checks_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    verified: dict[str, str] = {}
    for name in DATASET_FILES:
        entry = declared.get(name)
        path = dataset_dir / name
        if not isinstance(entry, dict) or not _SHA.match(str(entry.get("file_sha256", ""))):
            problems.append(f"{name}: thieu file_sha256 hop le trong output_checksums.json")
            continue
        if not path.exists():
            problems.append(f"{name}: file khong ton tai")
            continue
        actual = file_sha256(path)
        verified[name] = actual
        if actual != entry["file_sha256"]:
            problems.append(f"{name}: file_sha256 that {actual[:16]}… khac khai bao {entry['file_sha256'][:16]}…")
    snapshot_sha = verified.get(CALENDAR_SNAPSHOT)
    if snapshot_sha and (dataset_dir / CALENDAR_MANIFEST).exists():     # calendar_input.json phai khai bao dung hash cua bytes lich da dung
        try:
            declared_calendar = json.loads((dataset_dir / CALENDAR_MANIFEST).read_text(encoding="utf-8"))
        except ValueError as exc:
            declared_calendar = None
            problems.append(f"{CALENDAR_MANIFEST} khong doc duoc JSON: {exc}")
        if declared_calendar is not None and (not isinstance(declared_calendar, dict) or declared_calendar.get("vn_holidays_csv_sha256") != snapshot_sha):
            problems.append(f"{CALENDAR_MANIFEST}.vn_holidays_csv_sha256 khac SHA-256 that cua {CALENDAR_SNAPSHOT} ({snapshot_sha[:16]}…)")
    contract = _read_contract(dataset_dir, problems, calendar_sha=verified.get(CALENDAR_SNAPSHOT))
    if isinstance(contract.get("n1_policy"), dict):          # dataset mang n1_policy: snapshot policy + bang chung PHAI nguyen ven trong artifact (moi dataset, khong chi --official)
        n1_problems, _ = snapshot_members(dataset_dir, contract["n1_policy"], declared)
        problems.extend(f"n1_policy: {p}" for p in n1_problems)
    samples = declared.get("samples.parquet") or {}
    if not _SHA.match(str(samples.get("content_sha256", ""))):
        problems.append("samples.parquet: thieu content_sha256 hop le")
    if not isinstance(samples.get("rows"), int) or samples.get("rows", 0) <= 0:
        problems.append("samples.parquet: thieu rows")
    if problems:
        raise DatasetVerificationError("dataset KHONG qua xac minh: " + "; ".join(problems))
    return {"dataset_dir": str(dataset_dir), "dataset_name": dataset_dir.name, "samples_file_sha256": verified["samples.parquet"],
            "samples_content_sha256": samples["content_sha256"], "declared_rows": int(samples["rows"]),
            "calendar_sha256": verified[CALENDAR_SNAPSHOT], "contract": {**contract, "sha256": verified[CONTRACT_NAME]}, "verified_file_sha256": verified}


PURPOSES = ("rehearsal", "dev", "official")


SUFFICIENCY_STATUSES = ("primary_eligible", "exploratory", "not_evaluated")


_DATE_KEYS = ("train_start", "train_end", "validation_start", "validation_end", "test_start", "test_end")


def _contract_content_problems(data: dict[str, Any], dataset_dir: Path, *, calendar_sha: str | None) -> list[str]:
    """Kiem day du noi dung `dataset_contract.json` (GPT file 52 muc 3): computed horizons, purpose, thu tu/khoang cach split, trang thai sufficiency khop bao cao that,
    hash ma/config hop le, hash lich khop snapshot da xac minh. Tra danh sach loi (rong = hop le)."""
    out: list[str] = []
    horizons, purge = data.get("evaluation_horizons"), data.get("purge_gap_days")
    if data.get("computed_label_horizons") != list(KNOWN_HORIZONS):
        out.append(f"computed_label_horizons={data.get('computed_label_horizons')!r} != {list(KNOWN_HORIZONS)}")
    if data.get("purpose") not in PURPOSES:
        out.append(f"purpose={data.get('purpose')!r} ngoai {list(PURPOSES)}")
    for key in ("builder_code_sha256", "build_config_sha256"):
        if not _SHA.match(str(data.get(key, ""))):
            out.append(f"{key} khong phai 64 hex")
    if not isinstance(data.get("builder_version"), str) or not data["builder_version"]:
        out.append("builder_version thieu")
    if calendar_sha is not None and data.get("calendar_sha256") != calendar_sha:
        out.append(f"calendar_sha256 {str(data.get('calendar_sha256'))[:12]}… khac snapshot lich da xac minh {calendar_sha[:12]}…")
    plan = data.get("split_plan")
    if not isinstance(plan, dict):
        out.append("split_plan thieu")
    else:
        try:
            import datetime as _dt
            d = {k: _dt.date.fromisoformat(str(plan[k])) for k in _DATE_KEYS}
        except (KeyError, ValueError) as exc:
            out.append(f"split_plan thieu/sai ngay ISO: {exc}")
        else:
            if not (d["train_start"] <= d["train_end"] < d["validation_start"] <= d["validation_end"] < d["test_start"] <= d["test_end"]):
                out.append(f"split_plan sai thu tu: {plan}")
            elif isinstance(purge, int) and not isinstance(purge, bool):
                if (d["validation_start"] - d["train_end"]).days - 1 < purge or (d["test_start"] - d["validation_end"]).days - 1 < purge:
                    out.append(f"khoang cach giua cac split < purge_gap_days={purge}")
            if plan.get("purge_gap_days") != purge:
                out.append(f"split_plan.purge_gap_days={plan.get('purge_gap_days')!r} != purge_gap_days={purge!r}")
        policy = str(plan.get("policy_path"))
        if policy.startswith("gate_driven:H="):
            if plan.get("feasible_horizon") not in (horizons or []):
                out.append(f"policy_path {policy} nhung feasible_horizon={plan.get('feasible_horizon')!r} ngoai evaluation_horizons")
        elif policy != "fallback_ratio":
            out.append(f"split_plan.policy_path={policy!r} khong hop le")
    n1 = data.get("n1_policy")
    if n1 is None:
        if data.get("purpose") == "official":
            out.append("purpose=official nhung n1_policy thieu (khong co bang chung/danh sach loai tru da ghim)")
    elif not isinstance(n1, dict) or not _SHA.match(str(n1.get("policy_sha256", ""))) or not isinstance(n1.get("policy_version"), str) \
            or not isinstance(n1.get("excluded_hotels"), list) or not n1["excluded_hotels"]:
        out.append(f"n1_policy khong hop le: {n1!r}")
    status = data.get("sufficiency_status")
    if not isinstance(status, dict) or set(status) != {f"h{k}" for k in KNOWN_HORIZONS}:
        out.append(f"sufficiency_status phai co dung khoa h1/h3/h7/h14, nhan {status!r}")
    else:
        for k in KNOWN_HORIZONS:
            value, evaluated = status[f"h{k}"], isinstance(horizons, list) and k in horizons
            if value not in SUFFICIENCY_STATUSES or (evaluated and value == "not_evaluated") or (not evaluated and value != "not_evaluated"):
                out.append(f"sufficiency_status[h{k}]={value!r} khong khop evaluation_horizons {horizons}")
        report_path = dataset_dir / "sufficiency_report.json"
        try:
            real = {name: entry.get("status") for name, entry in json.loads(report_path.read_text(encoding="utf-8")).get("horizons", {}).items()}
        except (OSError, ValueError, AttributeError):
            real = None
        if real is not None and real != status:
            out.append(f"sufficiency_status {status} != sufficiency_report.json {real}")
    return out


def _read_contract(dataset_dir: Path, problems: list[str], *, calendar_sha: str | None = None) -> dict[str, Any]:
    """Doc + kiem hinh dang `dataset_contract.json` (GPT file 50): dataset_version == ten thu muc, evaluation_horizons hop le, purge >= max. Loi => problems."""
    path = dataset_dir / CONTRACT_NAME
    if not path.exists():
        return {}                                                            # da bao loi 'file khong ton tai' o vong lap DATASET_FILES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append(f"{CONTRACT_NAME} khong doc duoc JSON: {exc}")
        return {}
    if not isinstance(data, dict):
        problems.append(f"{CONTRACT_NAME} khong phai object JSON")
        return {}
    horizons, purge = data.get("evaluation_horizons"), data.get("purge_gap_days")
    if data.get("contract_version") != 1:
        problems.append(f"{CONTRACT_NAME}.contract_version={data.get('contract_version')!r} != 1")
    if data.get("dataset_version") != dataset_dir.name:
        problems.append(f"{CONTRACT_NAME}.dataset_version={data.get('dataset_version')!r} khac ten thu muc {dataset_dir.name!r}")
    if (not isinstance(horizons, list) or not horizons or len(set(horizons)) != len(horizons)
            or any(not isinstance(h, int) or isinstance(h, bool) or h not in KNOWN_HORIZONS for h in horizons)):
        problems.append(f"{CONTRACT_NAME}.evaluation_horizons={horizons!r} khong hop le (khong rong, khong trung, thuoc {list(KNOWN_HORIZONS)})")
    elif not isinstance(purge, int) or isinstance(purge, bool) or purge < max(horizons):
        problems.append(f"{CONTRACT_NAME}.purge_gap_days={purge!r} < max(evaluation_horizons)={max(horizons)}")
    problems.extend(f"{CONTRACT_NAME}: {p}" for p in _contract_content_problems(data, dataset_dir, calendar_sha=calendar_sha))
    return {"evaluation_horizons": sorted(horizons) if isinstance(horizons, list) else [], "purge_gap_days": purge, "purpose": data.get("purpose"),
            "split_plan": data.get("split_plan"), "sufficiency_status": data.get("sufficiency_status"), "n1_policy": data.get("n1_policy")}


def verify_frame(frame: pd.DataFrame, meta: dict[str, Any]) -> None:
    problems: list[str] = []
    if len(frame) != meta["declared_rows"]:
        problems.append(f"so dong {len(frame)} != khai bao {meta['declared_rows']}")
    versions = set(frame["dataset_version"].dropna().astype(str)) if "dataset_version" in frame.columns else set()
    if versions != {meta["dataset_name"]}:
        problems.append(f"dataset_version trong du lieu {sorted(versions)} khac ten thu muc {meta['dataset_name']!r}")
    if problems:
        raise DatasetVerificationError("dataset KHONG qua xac minh: " + "; ".join(problems))
