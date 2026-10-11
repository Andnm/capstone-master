"""THUAN (khong DB): bien lai du doan config cho MOT version moi tu snapshot P4 DA TIN CAY cua config cu + manifest ma moi (GPT 76 §5.5).

    python repair_predict_config_v1.py --snapshot <manifest_snapshot_*.json cua producer> --old-version ds_20261006_dev1b --expected-old-config-sha256 <pin> --root <repo root> --out <receipt.json>

Dung CHINH `repair_parity_v1.predict_new_config` / `prediction_receipt` (da duoc review): config moi = config cu voi DUY NHAT `builder_code` thay bang manifest cay hien tai (`repair_manifest_v1.compute_manifest`, tinh bang AST,
khong import package builder). Tu choi (exit 1, khong ghi receipt) khi: snapshot khong `trusted`, version thieu, hash config cu != pin, tap file doi != dung tap duoc phep, v.v. Receipt chi ghi bang `xb`.
Bien lai nay la gia tri DU KIEN doc lap cho `repair_verify_init_v1` (khong lay tu DB phien build).

TRANG THAI: THIET KE + MA + TEST; KHONG quyen thuc thi.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import types
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    path = HERE / filename
    data = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = hashlib.sha256(data).hexdigest()
    return module


rp = _load("repair_parity_for_predict", "repair_parity_v1.py")
rm = _load("repair_manifest_for_predict", "repair_manifest_v1.py")


def old_config_from_snapshot(snapshot: Mapping[str, Any], version: str) -> dict[str, Any]:
    """Config cu cua `version` tu snapshot P4 TIN CAY (`trusted` va danh gia `ok`); khong tin => ValueError."""
    evaluation = snapshot.get("evaluation") if isinstance(snapshot, Mapping) else None
    if not (isinstance(snapshot, Mapping) and snapshot.get("trusted") is True and isinstance(evaluation, Mapping) and evaluation.get("ok") is True and not evaluation.get("violations")):
        raise ValueError("snapshot_not_trusted")
    entry = (evaluation.get("versions") or {}).get(version)
    config = entry.get("config") if isinstance(entry, Mapping) else None
    if not isinstance(config, dict):
        raise ValueError(f"snapshot_has_no_config_for_{version}")
    return config


def predict(snapshot: Mapping[str, Any], version: str, *, expected_old_config_sha256: str, new_manifest: Mapping[str, Any]) -> dict[str, Any]:
    old_config = old_config_from_snapshot(snapshot, version)
    result = rp.predict_new_config(old_config, new_manifest, old_config_sha256=expected_old_config_sha256)
    return {**result, "receipt": rp.prediction_receipt(result)}


def main(argv: list[str] | None = None, *, manifest_fn=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--old-version", required=True)
    parser.add_argument("--expected-old-config-sha256", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if len(args.expected_old_config_sha256) != 64 or args.out.exists():
        print(json.dumps({"ok": False, "error": "bad_pin_or_out_exists"}))
        return 2
    try:
        snapshot = json.loads(args.snapshot.read_bytes().decode("utf-8"))
        manifest = (manifest_fn or rm.compute_manifest)(args.root)
        outcome = predict(snapshot, args.old_version, expected_old_config_sha256=args.expected_old_config_sha256, new_manifest=manifest)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}))
        return 1
    receipt = outcome["receipt"]
    if not receipt["ok"]:
        print(json.dumps({"ok": False, "violations": receipt["violations"]}, ensure_ascii=False))
        return 1                                                                                          # khong ghi receipt khong dat
    with open(args.out, "xb") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    print(json.dumps({"ok": True, "predicted_config_sha256": receipt["predicted_config_sha256"], "new_code_sha256": receipt["new_code_sha256"], "receipt_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
