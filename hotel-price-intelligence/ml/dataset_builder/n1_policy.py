"""Policy N1 (bug phu dinh bua sang) ghim vao dataset (GPT file 54 muc 6c, file 56 muc 5).

`ml/policies/n1/n1_policy_v1.json` khai bao policy_version, danh sach hotel bi loai khoi primary qua MOI regime, ly do, dinh danh lan quet (database/run/scraper/selector/git/pham vi)
va danh sach bang chung kem SHA-256. Cac file bang chung nam CANH policy (bytes bat bien, nam trong git), khong phu thuoc `outputs/` hay duong dan tuong doi con song.

Vong doi (giong ghim lich):
  init   : `policy_descriptor(path)` doc + kiem moi hash => descriptor ghim vao `build_config["n1_policy"]` (doi policy/evidence = config_sha256 moi = dataset_version moi);
  moi step/phien: `verify_policy(config)` doc lai tu dia va doi chieu descriptor da ghim => thieu/lech => N1PolicyError TRUOC khi lam gi;
  export : `snapshot_files(config, dest)` copy NGUYEN bytes vao artifact `inputs/n1_policy/` + `n1_policy_input.json`; validation kiem lai.
Hotel trong policy duoc hop vao `eligibility_overrides.exclude_hotels` (co che `hotel_not_overridden` san co cua step samples_labels).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

ML_DIR = Path(__file__).resolve().parents[1]
POLICY_DIR = ML_DIR / "policies" / "n1"
POLICY_FILE = "n1_policy_v1.json"
POLICY_SCHEMA_VERSION = 1
SNAPSHOT_SUBDIR = "inputs/n1_policy"
INPUT_MANIFEST = "n1_policy_input.json"
_SCAN_KEYS = ("scan_database", "scope", "runs", "parser_original", "session_time_zone")
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_RUN_KEYS = ("run_id", "started_at_utc", "finished_at_utc", "scraper_version", "selector_version", "git_commit")


class N1PolicyError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _scan_time_problems(scan: dict[str, Any]) -> list[str]:
    """Thoi gian `*_utc` PHAI la UTC that (GPT file 60 C59-M2): session phai '+00:00', dinh dang `YYYY-MM-DD HH:MM:SS`, finished >= started; neu co `*_vn` thi dung UTC+7."""
    problems: list[str] = []
    if scan.get("session_time_zone") != "+00:00":
        problems.append(f"scan_identity.session_time_zone={scan.get('session_time_zone')!r} != '+00:00'")
    for run in scan["runs"]:
        try:
            started = dt.datetime.strptime(str(run["started_at_utc"]), _TS_FORMAT)
            finished = dt.datetime.strptime(str(run["finished_at_utc"]), _TS_FORMAT)
        except ValueError:
            problems.append(f"run {run.get('run_id')}: started_at_utc/finished_at_utc sai dinh dang {_TS_FORMAT}")
            continue
        if finished < started:
            problems.append(f"run {run.get('run_id')}: finished_at_utc < started_at_utc")
        for key in ("started", "finished"):
            vn = run.get(f"{key}_at_vn")
            if vn is not None:
                utc = started if key == "started" else finished
                try:
                    if dt.datetime.strptime(str(vn), _TS_FORMAT) - utc != dt.timedelta(hours=7):
                        problems.append(f"run {run.get('run_id')}: {key}_at_vn khong bang {key}_at_utc + 7h")
                except ValueError:
                    problems.append(f"run {run.get('run_id')}: {key}_at_vn sai dinh dang")
    return problems


def load_policy(path: Path) -> tuple[bytes, dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise N1PolicyError(f"khong tim thay policy {path}")
    raw = path.read_bytes()
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise N1PolicyError(f"policy {path.name} khong doc duoc JSON: {exc}") from exc
    problems: list[str] = []
    if not isinstance(policy, dict):
        raise N1PolicyError("policy phai la JSON object")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        problems.append(f"schema_version={policy.get('schema_version')!r} != {POLICY_SCHEMA_VERSION}")
    for key in ("policy_version", "reason"):
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            problems.append(f"{key} thieu")
    hotels = policy.get("excluded_hotels")
    if not isinstance(hotels, list) or not hotels or not all(isinstance(h, str) and h for h in hotels):
        problems.append("excluded_hotels phai la danh sach hotel_id khong rong")
    elif hotels != sorted(set(hotels)):
        problems.append("excluded_hotels phai sap xep tang dan va khong trung")
    scan = policy.get("scan_identity")
    if not isinstance(scan, dict) or any(k not in scan for k in _SCAN_KEYS):
        problems.append(f"scan_identity thieu khoa {[k for k in _SCAN_KEYS if not isinstance(scan, dict) or k not in scan]}")
    elif not isinstance(scan["runs"], list) or not scan["runs"] or any(not isinstance(r, dict) or any(k not in r for k in _RUN_KEYS) for r in scan["runs"]):
        problems.append(f"scan_identity.runs moi run phai co {list(_RUN_KEYS)}")
    else:
        problems += _scan_time_problems(scan)
    evidence = policy.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        problems.append("evidence phai la danh sach khong rong")
    else:
        names = [e.get("name") for e in evidence if isinstance(e, dict)]
        if len(names) != len(evidence) or len(set(names)) != len(names) or any(not isinstance(n, str) or "/" in n or "\\" in n or n.startswith(".") for n in names):
            problems.append("evidence[].name phai la ten file don gian, khong trung")
        for entry in evidence:
            if not isinstance(entry, dict) or not _hex64(entry.get("sha256")) or not isinstance(entry.get("scope"), str) or not entry.get("scope"):
                problems.append(f"evidence {entry!r} thieu sha256 hop le/scope")
    if problems:
        raise N1PolicyError(f"policy {path.name} khong hop le: " + "; ".join(problems))
    return raw, policy


def policy_descriptor(path: Path | None = None) -> dict[str, Any]:
    """Descriptor ghim vao config: moi file bang chung PHAI ton tai canh policy va co SHA-256 trung voi khai bao; khong co duong dan tuyet doi."""
    path = Path(path or POLICY_DIR / POLICY_FILE)
    raw, policy = load_policy(path)
    evidence = []
    for entry in policy["evidence"]:
        file = path.parent / entry["name"]
        if not file.is_file():
            raise N1PolicyError(f"thieu file bang chung {entry['name']} canh policy ({path.parent})")
        data = file.read_bytes()
        if _sha(data) != entry["sha256"]:
            raise N1PolicyError(f"bang chung {entry['name']} lech hash khai bao trong policy ({_sha(data)[:12]}... != {entry['sha256'][:12]}...)")
        evidence.append({"name": entry["name"], "sha256": entry["sha256"], "bytes": len(data), "scope": entry["scope"]})
    return {"policy_name": path.name, "policy_version": policy["policy_version"], "policy_sha256": _sha(raw), "bytes": len(raw), "reason": policy["reason"],
            "excluded_hotels": list(policy["excluded_hotels"]), "scan_identity": policy["scan_identity"], "evidence": evidence}


def contract_summary(descriptor: dict[str, Any] | None) -> dict[str, Any] | None:
    if descriptor is None:
        return None
    return {"policy_version": descriptor["policy_version"], "policy_sha256": descriptor["policy_sha256"], "excluded_hotels": list(descriptor["excluded_hotels"])}


def excluded_hotels(descriptor: dict[str, Any] | None) -> list[str]:
    return [] if descriptor is None else list(descriptor["excluded_hotels"])


def verify_policy(config: dict[str, Any], *, policy_dir: Path | None = None) -> None:
    """Doc lai policy + bang chung tu dia va doi chieu descriptor DA GHIM; None => khong dung policy (hop le cho rehearsal/dev). Lech/thieu => N1PolicyError."""
    pinned = config.get("n1_policy")
    if pinned is None:
        return
    current = policy_descriptor(Path(policy_dir or POLICY_DIR) / pinned["policy_name"])
    if current != pinned:
        changed = sorted(k for k in set(current) | set(pinned) if current.get(k) != pinned.get(k))
        raise N1PolicyError(f"policy/bang chung N1 DA DOI so voi luc init (khac: {changed}) - tao dataset_version moi.")


def snapshot_files(config: dict[str, Any], dest_root: Path, *, policy_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Copy NGUYEN bytes policy + bang chung vao `dest_root/inputs/n1_policy/` va ghi `n1_policy_input.json`; tra {relative_path: {"file_sha256": ...}} de dua vao checksum."""
    pinned = config.get("n1_policy")
    if pinned is None:
        return {}
    verify_policy(config, policy_dir=policy_dir)
    source = Path(policy_dir or POLICY_DIR)
    target = dest_root / SNAPSHOT_SUBDIR
    target.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, dict[str, Any]] = {}
    for name in [pinned["policy_name"], *(e["name"] for e in pinned["evidence"])]:
        data = (source / name).read_bytes()
        (target / name).write_bytes(data)
        hashes[f"{SNAPSHOT_SUBDIR}/{name}"] = {"file_sha256": _sha(data)}
    manifest = {"policy_version": pinned["policy_version"], "policy_sha256": pinned["policy_sha256"], "excluded_hotels": pinned["excluded_hotels"], "pinned_in_config": pinned}
    (target / INPUT_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    hashes[f"{SNAPSHOT_SUBDIR}/{INPUT_MANIFEST}"] = {"file_sha256": _sha((target / INPUT_MANIFEST).read_bytes())}
    return hashes


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def snapshot_members(dataset_dir: Path, summary: dict[str, Any] | None, checksums: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Kiem snapshot N1 TRONG ARTIFACT (khong doc policy hien hanh cua repo, khong can backend/MySQL - dung duoc tren Colab): tra (problems, relative_paths).
    `summary` = `dataset_contract.n1_policy` (None => artifact legacy khong dung policy => ([], [])). Khi co summary, MOI thieu/hong/khac deu la loi, khong chi voi `--official`:
    manifest dau vao + policy + moi bang chung phai ton tai, policy bytes SHA == summary.policy_sha256, version/hotel khop, descriptor da ghim khop noi dung policy,
    moi bang chung khop hash khai bao trong policy, va moi file co entry khop trong output_checksums."""
    if summary is None:
        return [], []
    problems: list[str] = []
    base = Path(dataset_dir) / SNAPSHOT_SUBDIR
    manifest_rel = f"{SNAPSHOT_SUBDIR}/{INPUT_MANIFEST}"
    if not (base / INPUT_MANIFEST).is_file():
        return [f"thieu {manifest_rel} (contract co n1_policy nhung artifact khong mang bang chung)"], []
    try:
        manifest = json.loads((base / INPUT_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{manifest_rel} khong doc duoc JSON: {exc}"], []
    pinned = manifest.get("pinned_in_config") if isinstance(manifest, dict) else None
    if not isinstance(pinned, dict) or not isinstance(pinned.get("evidence"), list) or not isinstance(pinned.get("policy_name"), str):
        return [f"{manifest_rel}.pinned_in_config khong hop le"], []
    for key in ("policy_version", "policy_sha256", "excluded_hotels"):
        if manifest.get(key) != summary.get(key) or pinned.get(key) != summary.get(key):
            problems.append(f"{key} khong nhat quan giua contract, input manifest va descriptor da ghim")
    members = [pinned["policy_name"], *(e.get("name") for e in pinned["evidence"])]
    if any(not isinstance(name, str) or "/" in name or "\\" in name for name in members):
        return problems + ["ten file trong descriptor khong hop le"], []
    policy_path = base / pinned["policy_name"]
    if policy_path.is_file():
        if _file_sha(policy_path) != summary.get("policy_sha256"):
            problems.append(f"{pinned['policy_name']}: sha256 that khac contract.n1_policy.policy_sha256")
        try:
            _, policy = load_policy(policy_path)
        except N1PolicyError as exc:
            problems.append(str(exc))
        else:
            declared = [(e["name"], e["sha256"], e["scope"]) for e in policy["evidence"]]
            pinned_evidence = [(e.get("name"), e.get("sha256"), e.get("scope")) for e in pinned["evidence"]]
            if declared != pinned_evidence:
                problems.append("danh sach bang chung trong policy khac descriptor da ghim")
            for field in ("policy_version", "excluded_hotels", "reason", "scan_identity"):
                if policy.get(field) != pinned.get(field):
                    problems.append(f"policy.{field} khac descriptor da ghim")
    else:
        problems.append(f"thieu {SNAPSHOT_SUBDIR}/{pinned['policy_name']}")
    for entry in pinned["evidence"]:
        path = base / str(entry.get("name"))
        if not path.is_file():
            problems.append(f"thieu bang chung {SNAPSHOT_SUBDIR}/{entry.get('name')}")
        elif _file_sha(path) != entry.get("sha256"):
            problems.append(f"{SNAPSHOT_SUBDIR}/{entry.get('name')}: sha256 that khac hash da ghim")
    relative = [manifest_rel, *(f"{SNAPSHOT_SUBDIR}/{name}" for name in members)]
    for rel in relative:
        stored = checksums.get(rel)
        path = Path(dataset_dir) / rel
        if not isinstance(stored, dict):
            problems.append(f"{rel} khong co trong output_checksums")
        elif path.is_file() and _file_sha(path) != stored.get("file_sha256"):
            problems.append(f"{rel}: sha256 that khac output_checksums")
        elif not path.is_file():
            problems.append(f"thieu {rel}")
    return problems, relative
