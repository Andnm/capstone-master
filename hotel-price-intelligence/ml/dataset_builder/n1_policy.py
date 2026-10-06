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
_SCAN_KEYS = ("scan_database", "scope", "runs", "parser_original")
_RUN_KEYS = ("run_id", "started_at_utc", "finished_at_utc", "scraper_version", "selector_version", "git_commit")


class N1PolicyError(RuntimeError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


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
