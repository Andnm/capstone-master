"""Kiem dau vao + provenance cua mot lan huan luyen (GPT review vong 1 TR-M2/TR-M4/TR-m2). Fail-closed.

- `verify_dataset`: hash lai cac file dataset da cong bo trong `output_checksums.json` (khong tin gia tri khai bao), yeu cau metadata bat buoc
  (file_sha256 + content_sha256 + rows), kiem so dong va `dataset_version` duy nhat == ten thu muc. KHONG tinh lai `content_sha256` o day: hash
  noi dung phu thuoc phien ban pandas (Colab pandas 2.x khac may chinh pandas 3.x) nen se bao sai; do chinh xac theo byte da co file_sha256, con
  content_sha256 duoc builder xac minh trong moi truong cua no va chi duoc GHI vao bao cao de truy vet.
- `code_provenance`: tren Colab khong co Git => doc `CODE_MANIFEST.json` (do `package_for_colab.py` tao, hash tung file + `code_sha256`) va xac minh
  tung file dang chay khop manifest; tren may chinh ghi git HEAD + trang thai dirty cua `ml/`; khong xac dinh duoc => `source='unknown'`.
- `environment_manifest`: bang phien ban thuc te cua MOI goi da cai (`name==version`) + sha256 - thay cho viec ghim cung requirements.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from importlib import metadata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pandas as pd

ML_DIR = Path(__file__).resolve().parents[1]
CODE_MANIFEST_NAME = "CODE_MANIFEST.json"
CALENDAR_MANIFEST = "calendar_input.json"
CALENDAR_SNAPSHOT = "inputs/vn_holidays.csv"
CONTRACT_NAME = "dataset_contract.json"
KNOWN_HORIZONS = (1, 3, 7, 14)
# R4-m1: hai bang chung lich (R3-M1) cung duoc hash lai theo output_checksums.json - dataset thieu chung => khong qua xac minh
DATASET_FILES = ("samples.parquet", "data_dictionary.csv", "coverage_report.json", "sufficiency_report.json", CALENDAR_MANIFEST, CALENDAR_SNAPSHOT, CONTRACT_NAME)
_SHA = re.compile(r"^[0-9a-f]{64}$")


class DatasetVerificationError(RuntimeError):
    pass


class ProvenanceError(RuntimeError):
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
            "split_plan": data.get("split_plan"), "sufficiency_status": data.get("sufficiency_status")}


def verify_frame(frame: pd.DataFrame, meta: dict[str, Any]) -> None:
    problems: list[str] = []
    if len(frame) != meta["declared_rows"]:
        problems.append(f"so dong {len(frame)} != khai bao {meta['declared_rows']}")
    versions = set(frame["dataset_version"].dropna().astype(str)) if "dataset_version" in frame.columns else set()
    if versions != {meta["dataset_name"]}:
        problems.append(f"dataset_version trong du lieu {sorted(versions)} khac ten thu muc {meta['dataset_name']!r}")
    if problems:
        raise DatasetVerificationError("dataset KHONG qua xac minh: " + "; ".join(problems))


def _git_state() -> dict[str, Any]:
    try:
        repo = ML_DIR.parent
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", "--", "ml"], capture_output=True,
                               text=True, timeout=20, check=True).stdout.strip()
        return {"source": "git", "head": head, "ml_dirty": bool(dirty), "ml_dirty_files": len(dirty.splitlines()) if dirty else 0}
    except Exception as exc:  # noqa: BLE001
        return {"source": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def aggregate_sha256(files: dict[str, str]) -> str:
    """Cung cong thuc voi `package_for_colab.build_code_manifest` (R2-M2): sha256(JSON chuan hoa cua {duong_dan: sha})."""
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_code_manifest(manifest: Any, *, root: Path) -> dict[str, str]:
    """Kiem hinh dang + tinh toan ven cua CODE_MANIFEST (khong tin `code_sha256` tu khai bao): schema, hash 64-hex, duong dan tuong doi nam duoi `root`
    va `code_sha256` == aggregate tinh lai tu `files`. Sai => ProvenanceError. Tra `files` da hop le."""
    if not isinstance(manifest, dict):
        raise ProvenanceError(f"{CODE_MANIFEST_NAME} khong phai object JSON")
    files, declared = manifest.get("files"), manifest.get("code_sha256")
    if not isinstance(files, dict) or not files:
        raise ProvenanceError(f"{CODE_MANIFEST_NAME}: thieu/rong `files`")
    if not isinstance(declared, str) or not _SHA.match(declared):
        raise ProvenanceError(f"{CODE_MANIFEST_NAME}: `code_sha256` khong phai 64 ky tu hex")
    resolved_root = Path(root).resolve()
    for name, sha in files.items():
        if not isinstance(name, str) or not isinstance(sha, str) or not _SHA.match(sha):
            raise ProvenanceError(f"{CODE_MANIFEST_NAME}: muc {name!r} khong hop le (hash phai 64 hex)")
        path = PurePosixPath(name)
        if name.startswith("/") or PureWindowsPath(name).drive or ".." in path.parts or "\\" in name:
            raise ProvenanceError(f"{CODE_MANIFEST_NAME}: duong dan {name!r} phai tuong doi, dung '/', khong co '..'")
        if resolved_root not in (resolved_root / path).resolve().parents:
            raise ProvenanceError(f"{CODE_MANIFEST_NAME}: {name!r} nam ngoai goi {resolved_root}")
    recomputed = aggregate_sha256(files)
    if recomputed != declared:
        raise ProvenanceError(f"{CODE_MANIFEST_NAME}: code_sha256 khai bao {declared[:16]}… khac aggregate tinh lai {recomputed[:16]}…")
    return files


def code_provenance(ml_dir: Path | str = ML_DIR) -> dict[str, Any]:
    """Co CODE_MANIFEST.json => xac minh schema + aggregate + tung file (sai => ProvenanceError); khong co => git; khong co git => unknown."""
    ml_dir = Path(ml_dir)
    manifest_path = ml_dir / CODE_MANIFEST_NAME
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ProvenanceError(f"{CODE_MANIFEST_NAME} khong doc duoc JSON: {exc}") from exc
        root = ml_dir.parent
        files = validate_code_manifest(manifest, root=root)
        bad = [name for name, expected in files.items() if not (root / name).is_file() or file_sha256(root / name) != expected]
        if bad:
            raise ProvenanceError(f"code dang chay KHONG khop {CODE_MANIFEST_NAME}: {bad[:5]}")
        return {"source": "code_manifest", "code_sha256": manifest["code_sha256"], "manifest_sha256": file_sha256(manifest_path),
                "manifest_path": str(manifest_path), "verified_files": len(files), "created_at": manifest.get("created_at"),
                "packaged_from": manifest.get("git")}
    state = _git_state()
    return state


def require_known_provenance(provenance: dict[str, Any], *, official: bool) -> None:
    if not official:
        return
    source = provenance.get("source")
    if source == "code_manifest":
        return
    if source == "git" and provenance.get("head") and not provenance.get("ml_dirty"):
        return
    raise ProvenanceError(f"official: provenance code khong du (source={source}, dirty={provenance.get('ml_dirty')}, "
                          f"loi={provenance.get('error')}) - dung goi Colab co CODE_MANIFEST.json hoac commit ml/ roi chay.")


COLAB_MANIFEST_SCHEMA = 2


def require_lineage(provenance: dict[str, Any], colab_manifest: dict[str, Any] | None, *, official: bool, dataset_name: str | None = None) -> None:
    """R2-M2 + R3-M2: run `official` tren goi Colab (provenance = code_manifest) phai giu COLAB_MANIFEST trong lineage VA manifest do phai duoc NOI mat ma voi dung
    code dang chay - khong chi 'co truyen vao'. Kiem: schema_version, hash 64-hex, archive code + dataset, `code_manifest_sha256` == SHA-256 bytes CODE_MANIFEST.json that,
    `code_sha256` == aggregate da tinh lai, `created_at`/ten archive code cung mot goi. Sai/thieu => ProvenanceError (CLI goi TRUOC khi tao thu muc run).
    Khong xac minh lai hash zip (zip da khong con sau khi giai nen); dataset co hash file rieng. Git (may chinh) khong can."""
    if not official or provenance.get("source") != "code_manifest":
        return
    if not colab_manifest:
        raise ProvenanceError("official tren goi Colab: thieu --colab-manifest (COLAB_MANIFEST.json) - khong co hash archive trong lineage cua run.")
    content = colab_manifest.get("content") if isinstance(colab_manifest, dict) else None
    if not isinstance(content, dict):
        raise ProvenanceError("COLAB_MANIFEST.json khong phai object JSON")
    problems: list[str] = []
    if content.get("schema_version") != COLAB_MANIFEST_SCHEMA:
        problems.append(f"schema_version={content.get('schema_version')!r} != {COLAB_MANIFEST_SCHEMA} (goi tao bang packager cu - dong goi lai)")
    for key in ("code_manifest_sha256", "code_sha256"):
        if not isinstance(content.get(key), str) or not _SHA.match(content[key]):
            problems.append(f"{key} thieu hoac khong phai 64 hex")
    archives = content.get("archives")
    if not isinstance(archives, dict) or not archives:
        problems.append("archives thieu/rong")
        archives = {}
    for name, entry in archives.items():
        if not isinstance(entry, dict) or not _SHA.match(str(entry.get("sha256", ""))):
            problems.append(f"archives[{name}].sha256 khong phai 64 hex")
    code_archives = [name for name in archives if name.startswith("ml_train_pkg_") and name.endswith(".zip")]
    if len(code_archives) != 1:
        problems.append(f"can dung 1 archive code ml_train_pkg_*.zip, co {code_archives}")
    created = content.get("created_at")
    if code_archives and code_archives[0] != f"ml_train_pkg_{created}.zip":
        problems.append(f"archive code {code_archives[0]!r} khong khop created_at {created!r}")
    if created != provenance.get("created_at"):
        problems.append(f"created_at {created!r} khac created_at cua CODE_MANIFEST {provenance.get('created_at')!r} (manifest cua goi khac?)")
    expected_dataset = f"dataset_{dataset_name}.zip" if dataset_name else None
    if expected_dataset and expected_dataset not in archives:
        problems.append(f"thieu archive dataset {expected_dataset!r} (manifest cua dataset khac?)")
    elif not expected_dataset and not [n for n in archives if n.startswith("dataset_")]:
        problems.append("thieu archive dataset_*.zip")
    if content.get("code_manifest_sha256") != provenance.get("manifest_sha256"):
        problems.append("code_manifest_sha256 KHONG khop SHA-256 cua CODE_MANIFEST.json dang chay")
    if content.get("code_sha256") != provenance.get("code_sha256"):
        problems.append("code_sha256 KHONG khop aggregate da tinh lai cua CODE_MANIFEST.json dang chay")
    if problems:
        raise ProvenanceError("COLAB_MANIFEST.json khong noi duoc voi code dang chay: " + "; ".join(problems))


def environment_manifest(out_path: Path | None = None) -> dict[str, Any]:
    lines = sorted({f"{dist.metadata['Name']}=={dist.version}" for dist in metadata.distributions() if dist.metadata["Name"]}, key=str.lower)
    text = "\n".join(lines) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if out_path is not None:
        Path(out_path).write_bytes(text.encode("utf-8"))   # ghi BYTE (khong qua text mode) de hash file == hash da bao cao tren moi he dieu hanh
    return {"environment_sha256": digest, "packages": len(lines)}
