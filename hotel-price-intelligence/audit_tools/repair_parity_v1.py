"""THUAN (khong DB, khong ghi artifact cu): ledger raw, du doan config, va validator parity old<->new cho repair `free_cancellation`/`breakfast_included` (NULL -> <NA>) — ke hoach file 61/63.

Chay trong runtime BUILDER (`eda/.venv`: pandas 3.0.5, numpy 2.5.3, pyarrow 25.0.1) de `content_sha256` (hash_pandas_object) tai lap dung gia tri builder ghi; cac ham khac khong phu thuoc phien ban.

Nam phan (E la CONG TONG, bat buoc dung; cac phan khac la thanh phan):
  A. LEDGER raw theo `warehouse_record_id`: SQL doc-chi (co `a.id AS joined_assignment_id` de phat hien orphan — GPT file 62 L1) + `evaluate_ledger` (identity, dataset, domain, tap khoa).
  B. DU DOAN config moi: tu `build_config_json` CU day du, chi thay `builder_code` bang manifest cay hien tai, kiem file doi = dung 2 file, tinh `build_config_sha256` du kien (GPT file 62 C1).
  C. PARITY Parquet: 74 cot bat bien tung bit + missing-mask, hai co theo ledger, oracle content_sha256.
  D. ALLOWLIST JSON/CSV theo duong dan cu the, kieu co phan biet (bool/int/float), cau truc day du (ke ca object rong), mac dinh TU CHOI (GPT file 62 R1, file 64 G2).
  E. CONG TONG `validate_repair`: ghep config/ledger/Parquet/cay artifact; ctx hash lay tu DO DAC artifact moi; tra receipt gan hash nguon (GPT file 64 G1)."""
from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

BUILDER_RUNTIME = {"pandas": "3.0.5", "numpy": "2.5.3", "pyarrow": "25.0.1"}
KEY = "warehouse_record_id"
FLAGS = ("free_cancellation", "breakfast_included")
RAW_COLUMN = {"free_cancellation": "raw_free_cancellation", "breakfast_included": "raw_breakfast_included"}
METADATA_COLUMNS = ("dataset_version",)                       # DUY NHAT cot metadata duoc phep khac giua old/new (khai bao truoc)
EXPECTED_CHANGED_FILES = ("hotel-price-intelligence/ml/dataset_builder/dictionary.py", "hotel-price-intelligence/ml/dataset_builder/features.py")
EXPECTED_ROWS = 146431
EXPECTED_COLUMNS = 77

# ============================================================================ A. LEDGER
LEDGER_COLUMNS = ("record_id", "s_assignment_id", "joined_assignment_id", "a_dataset_version", "a_hotel_id", "a_checkin_date", "a_canonical_series_id",
                  "raw_free_cancellation", "raw_breakfast_included", "raw_max_occupancy")
SQL_FLAG_LEDGER = ("SELECT s.record_id AS record_id, s.ml_reference_assignment_id AS s_assignment_id, a.id AS joined_assignment_id, a.dataset_version AS a_dataset_version, "
                   "a.hotel_id AS a_hotel_id, a.checkin_date AS a_checkin_date, a.canonical_series_id AS a_canonical_series_id, a.free_cancellation AS raw_free_cancellation, "
                   "a.breakfast_included AS raw_breakfast_included, a.max_occupancy AS raw_max_occupancy "
                   "FROM ml_samples s LEFT JOIN ml_reference_assignments a ON a.id = s.ml_reference_assignment_id AND a.dataset_version = s.dataset_version "
                   "WHERE s.dataset_version = %s AND s.is_daily_snapshot_selected = 1 AND s.record_id > %s ORDER BY s.record_id LIMIT 1000")


def _is_int(value: Any) -> bool:
    return type(value) is int


def _flag_ok(value: Any) -> bool:
    return value is None or (_is_int(value) and value in (0, 1))


def _occ_ok(value: Any) -> bool:
    return value is None or (_is_int(value) and value >= 0)


def _iso_date(value: Any) -> str | None:
    return None if value is None else str(value)[:10]


def ledger_violations(row: Mapping[str, Any], *, dataset_version: str, identity: Sequence[Any] | None) -> list[str]:
    """Vi pham cua MOT hang ledger. `identity` = (hotel_id, checkin_date_iso, canonical_series_id) lay tu Parquet cho record nay (None neu record khong co trong Parquet)."""
    if set(row) != set(LEDGER_COLUMNS):
        return ["bad_columns"]
    out: list[str] = []
    if not _is_int(row["record_id"]):
        out.append("record_id_not_int")
    if identity is None:
        out.append("record_not_in_parquet")
    joined = row["joined_assignment_id"]
    if joined is None:
        out.append("missing_assignment")                                   # orphan: LEFT JOIN khong thay assignment (s-side id van co gia tri — phai nhin a.id)
    else:
        if not _is_int(joined):
            out.append("joined_assignment_id_not_int")
        if joined != row["s_assignment_id"]:
            out.append("assignment_id_mismatch")
        if row["a_dataset_version"] != dataset_version:
            out.append("wrong_dataset")
        if identity is not None and (row["a_hotel_id"], _iso_date(row["a_checkin_date"]), row["a_canonical_series_id"]) != tuple(identity):
            out.append("identity_mismatch")
    for flag in FLAGS:
        if not _flag_ok(row[RAW_COLUMN[flag]]):
            out.append(f"domain_invalid_{flag}")
    if not _occ_ok(row["raw_max_occupancy"]):
        out.append("domain_invalid_max_occupancy")
    return out


def stable_ledger_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash on dinh giua old/new (KHONG gom technical assignment id): record_id + identity on dinh + raw flags + occupancy, sap theo record_id."""
    lines = sorted((int(r["record_id"]), r["a_hotel_id"], _iso_date(r["a_checkin_date"]), r["a_canonical_series_id"], r["raw_free_cancellation"], r["raw_breakfast_included"], r["raw_max_occupancy"]) for r in rows)
    return hashlib.sha256(json.dumps(lines, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def evaluate_ledger(rows: Sequence[Mapping[str, Any]], *, dataset_version: str, parquet_identity: Mapping[int, Sequence[Any]], expected_rows: int = EXPECTED_ROWS) -> dict[str, Any]:
    counts: dict[str, int] = {}
    examples: dict[str, list[int]] = {}
    seen: dict[int, int] = {}
    for row in rows:
        rid = row.get("record_id") if isinstance(row, Mapping) else None
        problems = ledger_violations(row, dataset_version=dataset_version, identity=parquet_identity.get(rid) if _is_int(rid) else None) if isinstance(row, Mapping) else ["row_not_mapping"]
        for p in problems:
            counts[p] = counts.get(p, 0) + 1
            examples.setdefault(p, [])
            if len(examples[p]) < 5:
                examples[p].append(rid)
        if _is_int(rid):
            seen[rid] = seen.get(rid, 0) + 1
    ids, expected_ids = set(seen), set(parquet_identity)
    duplicates = sum(1 for c in seen.values() if c > 1)
    missing, extra = len(expected_ids - ids), len(ids - expected_ids)
    dist = {flag: {"null": 0, "zero": 0, "one": 0} for flag in FLAGS}
    for row in rows:
        if isinstance(row, Mapping) and set(row) == set(LEDGER_COLUMNS):
            for flag in FLAGS:
                v = row[RAW_COLUMN[flag]]
                if _flag_ok(v):
                    dist[flag]["null" if v is None else "zero" if v == 0 else "one"] += 1
    ok = not counts and not duplicates and not missing and not extra and len(rows) == expected_rows == len(ids)
    return {"status": "PASS" if ok else "FAIL", "rows": len(rows), "unique_record_ids": len(ids), "expected_rows": expected_rows, "duplicate_record_ids": duplicates, "missing_vs_parquet": missing,
            "extra_vs_parquet": extra, "violations": counts, "violation_examples": examples, "raw_flag_distribution": dist,
            "stable_digest": stable_ledger_digest(rows) if not counts and rows else None}


# ============================================================================ B. DU DOAN CONFIG (C1)
def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)          # cung quy uoc `anomaly_registry_lib.canonical_json` (test doi chieu ban goc); khong `default=`: kieu la => loi


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_sha256(config: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(config))


def code_sha256_of(files: Mapping[str, str], excluded_prefixes: Sequence[str]) -> str:
    """Cong thuc `code_identity.builder_code_manifest`: sha256 cua json.dumps({files, excluded_backend_prefixes}, sort_keys, separators)."""
    return hashlib.sha256(json.dumps({"files": dict(files), "excluded_backend_prefixes": list(excluded_prefixes)}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def predict_new_config(old_config: Mapping[str, Any], new_builder_code: Mapping[str, Any], *, old_config_sha256: str | None = None,
                       expected_changed_files: Sequence[str] = EXPECTED_CHANGED_FILES) -> dict[str, Any]:
    """Config moi = config cu voi DUY NHAT `builder_code` thay bang manifest cay hien tai. Moi truong khac (label/feature config, threshold, calendar, N1, anomaly cutoff/registry, seed, horizon/purge,
    required labels...) giu nguyen tuyet doi. Tra config du kien + sha, va vi pham neu: sha cu khong khop, manifest tu mau thuan, tap file doi != dung `expected_changed_files`."""
    violations: list[str] = []
    old_code = old_config.get("builder_code")
    if not isinstance(old_code, Mapping) or "files" not in old_code:
        return {"ok": False, "violations": ["old_config_missing_builder_code"], "predicted_config_sha256": None}
    actual_old_sha = config_sha256(old_config)
    if old_config_sha256 is not None and actual_old_sha != old_config_sha256:
        violations.append("old_config_sha_mismatch")                       # canonical hoa khong tai lap hash da ghi => khong tin du doan
    for label, manifest in (("old", old_code), ("new", new_builder_code)):
        if code_sha256_of(manifest.get("files", {}), manifest.get("excluded_backend_prefixes", [])) != manifest.get("code_sha256"):
            violations.append(f"{label}_builder_code_self_hash_mismatch")
    if list(old_code.get("excluded_backend_prefixes", [])) != list(new_builder_code.get("excluded_backend_prefixes", [])):
        violations.append("excluded_backend_prefixes_changed")
    old_files, new_files = dict(old_code["files"]), dict(new_builder_code.get("files", {}))
    changed = sorted(k for k in old_files.keys() & new_files.keys() if old_files[k] != new_files[k])
    added, removed = sorted(new_files.keys() - old_files.keys()), sorted(old_files.keys() - new_files.keys())
    if changed != sorted(expected_changed_files):
        violations.append(f"changed_files_mismatch: {changed}")
    if added or removed:
        violations.append(f"files_added_or_removed: added={added[:5]} removed={removed[:5]}")
    new_config = copy.deepcopy(dict(old_config))
    new_config["builder_code"] = copy.deepcopy(dict(new_builder_code))
    return {"ok": not violations, "violations": violations, "old_config_sha256": actual_old_sha, "predicted_config_sha256": config_sha256(new_config), "predicted_config": new_config,
            "changed_files": changed, "added_files": added, "removed_files": removed, "old_code_sha256": old_code.get("code_sha256"), "new_code_sha256": new_builder_code.get("code_sha256"),
            "other_keys_identical": {k: old_config[k] == new_config[k] for k in old_config if k != "builder_code"}}


# ============================================================================ C. PARITY PARQUET
def _state(value: Any) -> str:
    """'NA' | 'T' | 'F' | 'BAD' cho gia tri co trong Parquet (bool / pd.NA / None / NaN / numpy bool)."""
    if value is None or type(value).__name__ in ("NAType", "NaTType"):
        return "NA"
    if isinstance(value, float):
        return "NA" if math.isnan(value) else "BAD"
    if type(value).__name__ in ("bool", "bool_"):
        return "T" if bool(value) else "F"
    return "BAD"


def _raw_state(value: Any) -> str:
    return "NA" if value is None else "T" if (_is_int(value) and value == 1) else "F" if (_is_int(value) and value == 0) else "BAD"


def _series_equal_bitwise(a: Any, b: Any) -> bool:
    import numpy as np
    if str(a.dtype) != str(b.dtype) or len(a) != len(b):
        return False
    if not (a.isna().to_numpy() == b.isna().to_numpy()).all():
        return False
    if str(a.dtype) == "float64":
        x, y = a.to_numpy(dtype="float64", na_value=0.0), b.to_numpy(dtype="float64", na_value=0.0)
        return bool((x.view(np.uint64) == y.view(np.uint64)).all())
    return bool(a.equals(b))


def content_sha256(frame: Any) -> str:
    """Bao sao DUNG `dataset_builder.export.content_sha256` (khong import de tranh .env): hash noi dung sap theo khoa tu nhien, khong technical ID."""
    import pandas as pd
    technical = ["warehouse_record_id", "dataset_version"]
    keys = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date"]
    columns = [c for c in frame.columns if c not in technical]
    ordered = frame[columns].sort_values(keys, kind="mergesort").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy(dtype="uint64")
    digest = hashlib.sha256()
    digest.update(json.dumps(columns).encode("utf-8"))
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def _ledger_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[int, Mapping[str, Any]]:
    return {int(r["record_id"]): r for r in rows}


DEFAULT_PINNED_DTYPES = {"max_occupancy": ("Int64", "int64")}                      # schema Parquet cu da ghim: occupancy la Int64 (hoac int64 neu khong co NA)


def parquet_identity(frame: Any) -> dict[int, tuple[Any, ...]]:
    """record_id -> (hotel_id, checkin_date ISO, canonical_series_id) lay tu CHINH Parquet, de doi chieu tung ledger voi artifact cua no."""
    return {int(rid): (h, _iso_date(c), s) for rid, h, c, s in zip(frame[KEY].tolist(), frame["hotel_id"].tolist(), frame["checkin_date"].tolist(), frame["canonical_series_id"].tolist())}


def _occupancy_value(value: Any) -> tuple[bool, int | None]:
    """(hop le, gia tri): NA -> (True, None); so nguyen that (int / numpy int, khong phai bool) -> (True, int). Float (ke ca 2.0), chuoi, bool => khong hop le (khong cat phan thap phan)."""
    if _state(value) == "NA":
        return True, None
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return False, None
    if isinstance(value, int) or type(value).__name__.startswith(("int", "uint")):
        return True, int(value)
    return False, None


def compare_samples(old: Any, new: Any, *, old_ledger: Sequence[Mapping[str, Any]], new_ledger: Sequence[Mapping[str, Any]], old_version: str, new_version: str,
                    expected_rows: int | None = EXPECTED_ROWS, expected_columns: int | None = EXPECTED_COLUMNS,
                    pinned_dtypes: Mapping[str, Sequence[str]] | None = DEFAULT_PINNED_DTYPES) -> dict[str, Any]:
    """FAIL-CLOSED: moi ngoai le (payload sai/thieu truong) => verdict FAIL kem `exception`, khong bao gio PASS."""
    try:
        return _compare_samples_impl(old, new, old_ledger=old_ledger, new_ledger=new_ledger, old_version=old_version, new_version=new_version, expected_rows=expected_rows,
                                     expected_columns=expected_columns, pinned_dtypes=pinned_dtypes)
    except Exception as exc:                                                                                    # noqa: BLE001
        return {"verdict": "FAIL", "violations": [f"exception: {type(exc).__name__}: {str(exc)[:160]}"], "notes": {}}


def _compare_samples_impl(old: Any, new: Any, *, old_ledger: Sequence[Mapping[str, Any]], new_ledger: Sequence[Mapping[str, Any]], old_version: str, new_version: str,
                          expected_rows: int | None, expected_columns: int | None, pinned_dtypes: Mapping[str, Sequence[str]] | None) -> dict[str, Any]:
    """So Parquet cu/moi (pandas DataFrame, doc cung runtime) theo khoa `warehouse_record_id` va hai ledger raw. Moi ledger duoc `evaluate_ledger` doi chieu voi CHINH Parquet + version cua no TRUOC moi so sanh khac."""
    violations: list[str] = []
    notes: dict[str, Any] = {}

    def fail(msg: str) -> None:
        violations.append(msg)

    for label, frame in (("old", old), ("new", new)):
        if KEY not in frame.columns or frame[KEY].duplicated().any():
            fail(f"{label}_key_missing_or_duplicated")
    if violations:
        return {"verdict": "FAIL", "violations": violations}
    if expected_rows is not None and (len(old) != expected_rows or len(new) != expected_rows):
        fail(f"row_count: old={len(old)} new={len(new)} expected={expected_rows}")
    if expected_columns is not None and (len(old.columns) != expected_columns or len(new.columns) != expected_columns):
        fail(f"column_count: old={len(old.columns)} new={len(new.columns)} expected={expected_columns}")
    if list(old.columns) != list(new.columns):
        fail("column_names_or_order_differ")
    old_ids, new_ids = set(old[KEY].tolist()), set(new[KEY].tolist())
    led_old, led_new = _ledger_by_id(old_ledger), _ledger_by_id(new_ledger)
    if len(led_old) != len(old_ledger) or len(led_new) != len(new_ledger):
        fail("ledger_duplicate_record_ids")
    if not (old_ids == new_ids == set(led_old) == set(led_new)):
        fail(f"key_sets_differ: old-new={len(old_ids ^ new_ids)} old-ledger_old={len(old_ids ^ set(led_old))} new-ledger_new={len(new_ids ^ set(led_new))} ledger_old-ledger_new={len(set(led_old) ^ set(led_new))}")
    notes["row_order_identical"] = bool(old[KEY].tolist() == new[KEY].tolist())
    if violations:
        return {"verdict": "FAIL", "violations": violations, "notes": notes}

    # ---- moi ledger phai hop le voi CHINH Parquet + version cua no (orphan, dataset, identity, s<->a, domain, tap khoa) — GPT file 64 G1 ----
    for label, frame, ledger, version in (("old", old, old_ledger, old_version), ("new", new, new_ledger, new_version)):
        ev = evaluate_ledger(ledger, dataset_version=version, parquet_identity=parquet_identity(frame), expected_rows=expected_rows if expected_rows is not None else len(frame))
        notes[f"ledger_{label}"] = {k: ev[k] for k in ("status", "rows", "violations", "stable_digest", "raw_flag_distribution")}
        if ev["status"] != "PASS":
            fail(f"ledger_{label}_invalid: {ev['violations'] or {'missing': ev['missing_vs_parquet'], 'extra': ev['extra_vs_parquet'], 'duplicates': ev['duplicate_record_ids']}}")
    if violations:
        return {"verdict": "FAIL", "violations": violations, "notes": notes}

    o = old.set_index(KEY, drop=False).sort_index()
    n = new.set_index(KEY, drop=False).sort_index()
    value_columns = [c for c in old.columns if c not in FLAGS and c not in METADATA_COLUMNS]
    notes["value_invariant_columns"] = len(value_columns)
    bad_columns = [c for c in value_columns if not _series_equal_bitwise(o[c], n[c])]
    if bad_columns:
        fail(f"value_invariant_columns_differ: {bad_columns[:10]}")
    for flag in FLAGS:
        if str(o[flag].dtype) != str(n[flag].dtype) and not (str(o[flag].dtype) in ("bool", "boolean") and str(n[flag].dtype) in ("bool", "boolean")):
            fail(f"flag_dtype_unexpected_{flag}: {o[flag].dtype} -> {n[flag].dtype}")
    for label, frame, version in (("old", o, old_version), ("new", n, new_version)):
        if set(frame["dataset_version"].unique()) != {version}:
            fail(f"{label}_dataset_version_column_not_constant_{version}")

    # ---- ledger old vs new (so theo identity on dinh + raw; technical id khong ep bang nhau) ----
    for rid, a in led_old.items():
        b = led_new[rid]
        if tuple(a[c] for c in ("a_hotel_id", "a_checkin_date", "a_canonical_series_id", "raw_free_cancellation", "raw_breakfast_included", "raw_max_occupancy")) != \
                tuple(b[c] for c in ("a_hotel_id", "a_checkin_date", "a_canonical_series_id", "raw_free_cancellation", "raw_breakfast_included", "raw_max_occupancy")):
            fail(f"ledger_old_new_differ_at_record_{rid}")
            break

    # ---- hai co theo ledger ----
    flag_report: dict[str, Any] = {}
    changed_expected_total = 0
    for flag in FLAGS:
        raw_col = RAW_COLUMN[flag]
        table: dict[str, int] = {}
        changed_actual, changed_expected = set(), set()
        unexpected: list[str] = []
        ov, nv = o[flag].tolist(), n[flag].tolist()
        for rid, old_value, new_value in zip(o.index.tolist(), ov, nv):
            raw, so, sn = _raw_state(led_new[rid][raw_col]), _state(old_value), _state(new_value)
            if "BAD" in (raw, so, sn):
                unexpected.append(f"domain_{rid}")
                continue
            table[f"raw={raw}|old={so}|new={sn}"] = table.get(f"raw={raw}|old={so}|new={sn}", 0) + 1
            if raw != "NA":
                if so != raw or sn != raw:
                    unexpected.append(f"known_raw_not_preserved_{rid}")
            else:
                if sn != "NA":
                    unexpected.append(f"raw_null_new_not_na_{rid}")
                if so == "F":
                    unexpected.append(f"raw_null_old_false_{rid}")
            if so != sn:
                changed_actual.add(rid)
            if raw == "NA" and so != "NA":
                changed_expected.add(rid)
        if changed_actual != changed_expected:
            unexpected.append(f"changed_set_mismatch: actual={len(changed_actual)} expected={len(changed_expected)}")
        if unexpected:
            fail(f"{flag}: {unexpected[:6]}")
        changed_expected_total += len(changed_expected)
        flag_report[flag] = {"cells": dict(sorted(table.items())), "changed_rows": len(changed_actual), "expected_changed_rows": len(changed_expected), "unexpected": unexpected[:10]}
    notes["flags"] = flag_report

    # ---- occupancy: schema ghim + gia tri CHINH XAC (khong cat phan thap phan) == raw ledger (NULL <-> NA) ----
    if "max_occupancy" in n.columns:
        if pinned_dtypes is not None:
            for label, frame in (("old", o), ("new", n)):
                if str(frame["max_occupancy"].dtype) not in tuple(pinned_dtypes.get("max_occupancy", ())):
                    fail(f"max_occupancy_dtype_unexpected_{label}: {frame['max_occupancy'].dtype}")
        occ_bad, occ_not_exact = [], []
        for rid, v in zip(n.index.tolist(), n["max_occupancy"].tolist()):
            ok_value, exact = _occupancy_value(v)
            if not ok_value:
                occ_not_exact.append(rid)
            elif exact != led_new[rid]["raw_max_occupancy"]:
                occ_bad.append(rid)
        if occ_not_exact:
            fail(f"max_occupancy_not_exact_integer: {len(occ_not_exact)} rows, first {occ_not_exact[:3]}")
        if occ_bad:
            fail(f"max_occupancy_differs_from_raw_ledger: {len(occ_bad)} rows, first {occ_bad[:3]}")

    # ---- oracle C: old voi hai co thay bang raw (NULL -> NA) phai bang new, va content_sha256 du kien ----
    import pandas as pd
    oracle = o.copy()
    for flag in FLAGS:
        oracle[flag] = pd.array([None if led_new[rid][RAW_COLUMN[flag]] is None else bool(led_new[rid][RAW_COLUMN[flag]]) for rid in o.index.tolist()], dtype="boolean")
    oracle["dataset_version"] = new_version
    flag_states_equal = all([_state(a) for a in oracle[f].tolist()] == [_state(b) for b in n[f].tolist()] for f in FLAGS)
    for flag in FLAGS:                                                               # dung dtype cua ban moi de hash so sanh duoc (bool khong null vs boolean)
        oracle[flag] = oracle[flag].astype(n[flag].dtype) if not oracle[flag].isna().any() or str(n[flag].dtype) == "boolean" else oracle[flag]
    oracle_ok = flag_states_equal and list(oracle.columns) == list(n.columns) and all(_series_equal_bitwise(oracle[c], n[c]) for c in oracle.columns if c not in FLAGS and c != "dataset_version")
    if not oracle_ok:
        fail("oracle_c_differs_from_new")
    notes["oracle_content_sha256"] = content_sha256(oracle.reset_index(drop=True))
    notes["new_content_sha256_recomputed"] = content_sha256(n.reset_index(drop=True))
    notes["old_content_sha256_recomputed"] = content_sha256(o.reset_index(drop=True))
    if notes["oracle_content_sha256"] != notes["new_content_sha256_recomputed"]:
        fail("oracle_content_sha256_differs_from_new_recomputed")
    return {"verdict": "PASS_PARITY" if not violations else "FAIL", "violations": violations, "notes": notes, "changed_rows_total": changed_expected_total}


# ============================================================================ D. ALLOWLIST JSON / CSV (cau truc day du, co kieu, mac dinh tu choi)
_SENTINEL = object()
PINNED_DICTIONARY_HEADER = ["column", "group", "description", "formula_or_source", "leakage_note"]


def _esc(token: Any) -> str:
    if isinstance(token, int):
        return f"[{token}]"
    return "/" + str(token).replace("~", "~0").replace("/", "~1").replace("[", "~2").replace("]", "~3")


def _unesc(text: str) -> str:
    return text.replace("~3", "]").replace("~2", "[").replace("~1", "/").replace("~0", "~")


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "other"


def flatten(obj: Any, prefix: str = "") -> dict[str, tuple[str, Any]]:
    """Moi NODE -> (kind, value): mapping => ("dict", keyset da sap) (ke ca object RONG), list => ("list", len), la => (kind, value) voi bool != int != float. Khoa chua '/', '[', ']' duoc thoat (khong nhap nhang)."""
    out: dict[str, tuple[str, Any]] = {}
    if isinstance(obj, Mapping):
        out[prefix] = ("dict", tuple(sorted(str(k) for k in obj)))
        for k, v in obj.items():
            out.update(flatten(v, prefix + _esc(str(k))))
    elif isinstance(obj, list):
        out[prefix] = ("list", len(obj))
        for i, v in enumerate(obj):
            out.update(flatten(v, prefix + _esc(i)))
    else:
        out[prefix] = (_kind(obj), obj)
    return out


def _nonneg_number(entry: Any) -> bool:
    return isinstance(entry, tuple) and entry[0] in ("int", "float") and math.isfinite(entry[1]) and entry[1] >= 0


def compare_json_trees(old_dir: Path, new_dir: Path, ctx: Mapping[str, Any]) -> dict[str, Any]:
    """So cac JSON/CSV artifact theo allowlist DUONG DAN CU THE, CO KIEU, MAC DINH TU CHOI. ctx: new_version, new_code_sha256, new_config_sha256, new_output_dir, new_samples_file_sha256,
    new_samples_content_sha256, new_samples_path (cac gia tri hash phai lay tu DO DAC artifact moi, xem `validate_repair`). File set phai y het nhau."""
    violations: list[str] = []
    allowed: dict[str, int] = {}
    old_files = {p.relative_to(old_dir).as_posix() for p in old_dir.rglob("*") if p.is_file()}
    new_files = {p.relative_to(new_dir).as_posix() for p in new_dir.rglob("*") if p.is_file()}
    if old_files != new_files:
        violations.append(f"file_set_differs: only_old={sorted(old_files - new_files)[:5]} only_new={sorted(new_files - old_files)[:5]}")
    common = sorted(old_files & new_files)
    actual_sha = {rel: hashlib.sha256((new_dir / rel).read_bytes()).hexdigest() for rel in common}
    byte_identical_required = [rel for rel in common if rel.startswith("inputs/") or rel in ("calendar_input.json", "sufficiency_report.json")]       # coverage_report.json co `builder_code_sha256` => so theo JSON, khong theo byte
    for rel in byte_identical_required:
        if (old_dir / rel).read_bytes() != (new_dir / rel).read_bytes():
            violations.append(f"must_be_byte_identical_but_differs: {rel}")
    for rel in common:
        if not rel.endswith(".json") or rel in byte_identical_required:
            continue
        old_json, new_json = json.loads((old_dir / rel).read_text(encoding="utf-8")), json.loads((new_dir / rel).read_text(encoding="utf-8"))
        fo, fn = flatten(old_json), flatten(new_json)
        for path in sorted(set(fo) | set(fn)):
            ov, nv = fo.get(path, _SENTINEL), fn.get(path, _SENTINEL)
            rule = _json_rule(rel, path, ov, nv, ctx, actual_sha, fn)
            if rule is None:
                if ov != nv:
                    violations.append(f"unallowlisted_diff: {rel}:{path}: {str(ov)[:70]} -> {str(nv)[:70]}")
                continue
            ok, why = rule
            allowed[why.split(":")[0]] = allowed.get(why.split(":")[0], 0) + 1
            if not ok:
                violations.append(f"allowlisted_rule_failed: {rel}:{path}: {why}")
    # must-differ (neu khong khac thi ban moi khong the la ban moi)
    for rel, path in (("dataset_contract.json", "/dataset_version"), ("dataset_contract.json", "/build_config_sha256"), ("dataset_contract.json", "/builder_code_sha256"),
                      ("reports/features_labels.json", "/file_sha256")):
        if rel in common:
            fo, fn = flatten(json.loads((old_dir / rel).read_text(encoding="utf-8"))), flatten(json.loads((new_dir / rel).read_text(encoding="utf-8")))
            if fo.get(path) == fn.get(path):
                violations.append(f"must_differ_but_equal: {rel}:{path}")
    violations += _dictionary_violations(old_dir, new_dir)
    return {"violations": violations, "allowed_observed": allowed, "files_compared": len(common)}


def _json_rule(rel: str, path: str, ov: Any, nv: Any, ctx: Mapping[str, Any], actual_sha: Mapping[str, str], new_flat: Mapping[str, Any]) -> tuple[bool, str] | None:
    """None = khong co quy tac (mac dinh: phai bang nhau, ke ca KIEU va cau truc). (ok, ten_quy_tac) = quy tac ap dung (kiem gia tri MOI voi artifact moi that), ap dung ke ca khi cu == moi."""
    S = lambda text: ("str", text)                                                                               # noqa: E731
    if path.endswith("/elapsed_s"):
        return (_nonneg_number(ov) and _nonneg_number(nv)), "timing"
    if path.endswith("/builder_code_sha256"):                                                                      # o moi do sau: top-level cua report va `/split/...` nhung trong coverage_report.json
        return (nv == S(ctx["new_code_sha256"])), "code_sha_new"
    if rel == "dataset_contract.json":
        if path == "/build_config_sha256":
            return (nv == S(ctx["new_config_sha256"])), "config_sha_new"
        if path == "/dataset_version":
            return (nv == S(ctx["new_version"])), "version_new"
    if rel == "reports/REPORTS_MANIFEST.json":
        if path == "/build_config_sha256":
            return (nv == S(ctx["new_config_sha256"])), "config_sha_new"
        if path == "/dataset_version":
            return (nv == S(ctx["new_version"])), "version_new"
        m = re.fullmatch(r"/reports/([A-Za-z_]+\.json)/file_sha256", path)
        if m:
            target = actual_sha.get(f"reports/{m.group(1)}")
            return (target is not None and nv == S(target)), "self_file_sha"
    if rel == "reports/features_labels.json":
        if path == "/output_dir":
            return (nv == S(ctx["new_output_dir"])), "output_dir_new"
        if path == "/file_sha256":
            return (nv == S(ctx["new_samples_file_sha256"])), "samples_file_sha_new"
        if path == "/content_sha256":
            return (nv == S(ctx["new_samples_content_sha256"])), "samples_content_sha_new"
    if rel == "reports/validation.json":
        m = re.fullmatch(r"/checks\[(\d+)\]/detail", path)
        if m:
            name_entry = new_flat.get(f"/checks[{m.group(1)}]/name")
            name = name_entry[1] if isinstance(name_entry, tuple) and name_entry[0] == "str" else None
            if name == "parquet_ton_tai":
                return (nv == S(ctx["new_samples_path"])), "validation_samples_path_new"
            if isinstance(name, str) and name.startswith("hash_file_"):                  # detail = 16 hex dau cua sha256 file THAT trong thu muc moi
                target = actual_sha.get(name[len("hash_file_"):])
                return (target is not None and nv == S(target[:16])), "validation_hash_file_prefix_self"
            if name == "hash_noi_dung_khop":
                return (nv == S(str(ctx["new_samples_content_sha256"])[:16])), "validation_content_hash_prefix_new"
    if rel == "output_checksums.json":
        m = re.fullmatch(r"/(.+)/file_sha256", path)
        if m and _unesc(m.group(1)) != "samples.parquet":
            target = actual_sha.get(_unesc(m.group(1)))
            return (target is not None and nv == S(target)), "self_file_sha"
        if path == "/samples.parquet/file_sha256":
            return (nv == S(ctx["new_samples_file_sha256"]) and ctx["new_samples_file_sha256"] == actual_sha.get("samples.parquet")), "samples_file_sha_new"
        if path == "/samples.parquet/content_sha256":
            return (nv == S(ctx["new_samples_content_sha256"])), "samples_content_sha_new"
    return None


def _dictionary_violations(old_dir: Path, new_dir: Path) -> list[str]:
    rel = "data_dictionary.csv"
    if not (old_dir / rel).is_file() or not (new_dir / rel).is_file():
        return []
    def read(directory: Path) -> list[list[str]]:
        with (directory / rel).open(encoding="utf-8", newline="") as handle:
            return list(csv.reader(handle))

    old_rows, new_rows = read(old_dir), read(new_dir)
    out: list[str] = []
    if not old_rows or not new_rows:
        return ["dictionary_empty"]
    if old_rows[0] != PINNED_DICTIONARY_HEADER or new_rows[0] != PINNED_DICTIONARY_HEADER:
        return ["dictionary_header_not_pinned"]
    width = len(PINNED_DICTIONARY_HEADER)
    for label, rows in (("old", old_rows), ("new", new_rows)):
        wrong = [(i + 1, len(r)) for i, r in enumerate(rows) if len(r) != width]
        if wrong:
            out.append(f"dictionary_row_width_{label}: line/width {wrong[:3]}")
    if len(old_rows) != len(new_rows):
        out.append("dictionary_row_count_differs")
    if out:
        return out
    names_new = [r[0] for r in new_rows[1:]]
    if len(set(names_new)) != len(names_new):
        out.append("dictionary_duplicate_column_names")
    if [r[0] for r in old_rows[1:]] != names_new:
        out.append("dictionary_row_order_changed")
    desc_idx = PINNED_DICTIONARY_HEADER.index("description")
    differing = [i for i in range(1, len(old_rows)) if old_rows[i] != new_rows[i]]
    names = sorted(new_rows[i][0] for i in differing)
    if names != sorted(FLAGS):
        out.append(f"dictionary_rows_differ_for: {names}")
    for i in differing:
        for j in range(width):
            if j != desc_idx and old_rows[i][j] != new_rows[i][j]:
                out.append(f"dictionary_non_description_column_differs: {new_rows[i][0]}.{PINNED_DICTIONARY_HEADER[j]}")
        if "NA" not in new_rows[i][desc_idx]:
            out.append(f"dictionary_description_lacks_NA_semantics: {new_rows[i][0]}")
    return out


# ============================================================================ E. CONG TONG (bat buoc dung) + receipt gan hash nguon
def _native(value: Any) -> Any:
    if value is None or type(value).__name__ in ("NAType", "NaTType"):
        return None
    if isinstance(value, float) and value != value:
        return None
    if type(value).__module__ == "numpy" and hasattr(value, "item"):
        return value.item()
    return value


def ledger_arrow_schema():
    """Schema Arrow CO DINH cua ledger (khong de pandas ep NULL thanh NaN/float): raw flag int8 nullable, occupancy int64 nullable, id int64, ngay date32."""
    import pyarrow as pa
    return pa.schema([("record_id", pa.int64(), False), ("s_assignment_id", pa.int64()), ("joined_assignment_id", pa.int64()), ("a_dataset_version", pa.string()), ("a_hotel_id", pa.string()),
                      ("a_checkin_date", pa.date32()), ("a_canonical_series_id", pa.string()), ("raw_free_cancellation", pa.int8()), ("raw_breakfast_included", pa.int8()), ("raw_max_occupancy", pa.int64())])


def ledger_row_shape_errors(row: Any) -> list[str]:
    """Hinh dang + kieu CHINH XAC cua mot hang ledger TRUOC khi chuyen sang Arrow (Arrow tu ep 1.5->1, 2.5->2, 1000.5->1000, tu them NULL cho khoa thieu, bo khoa thua): keyset dung y het, id/flag/occupancy
    la `int` that (khong bool, khong float, khong chuoi), flag in {0,1}, occupancy >= 0, chuoi la `str`, ngay la `datetime.date` (khong phai datetime)."""
    if not isinstance(row, Mapping):
        return ["row_not_mapping"]
    errors: list[str] = []
    if set(row) != set(LEDGER_COLUMNS):
        errors.append(f"keys: missing={sorted(set(LEDGER_COLUMNS) - set(row))} extra={sorted(set(row) - set(LEDGER_COLUMNS))}")
    is_str = lambda v: type(v) is str                                                                           # noqa: E731
    rules = {"record_id": (False, _is_int), "s_assignment_id": (False, _is_int), "joined_assignment_id": (True, _is_int), "a_dataset_version": (True, is_str), "a_hotel_id": (True, is_str),
             "a_checkin_date": (True, lambda v: type(v) is dt.date), "a_canonical_series_id": (True, is_str),
             "raw_free_cancellation": (True, lambda v: _is_int(v) and v in (0, 1)), "raw_breakfast_included": (True, lambda v: _is_int(v) and v in (0, 1)),
             "raw_max_occupancy": (True, lambda v: _is_int(v) and v >= 0)}
    for column, (nullable, valid) in rules.items():
        if column not in row:
            continue
        value = row[column]
        if value is None:
            if not nullable:
                errors.append(f"{column}: null_not_allowed")
        elif not valid(value):
            errors.append(f"{column}: invalid_{type(value).__name__}")
    return errors


def write_ledger(rows: Sequence[Mapping[str, Any]], path: Path) -> str:
    """Ghi ledger Parquet voi schema co dinh; KHONG ghi de (mo 'xb'); tra sha256 cua byte da ghi. MOI hang duoc kiem hinh dang/kieu chinh xac (`ledger_row_shape_errors`) TRUOC khi chuyen Arrow va TRUOC khi mo
    tep: hang sai => ValueError, khong co tep. Khong ep kieu im lang, khong dien khoa thieu."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    problems = [(i, errs) for i, errs in ((i, ledger_row_shape_errors(r)) for i, r in enumerate(rows)) if errs]
    if problems:
        raise ValueError(f"ledger_rows_invalid: {len(problems)} rows, first {problems[:3]}")
    table = pa.Table.from_pylist([dict(r) for r in rows], schema=ledger_arrow_schema())
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    data = sink.getvalue().to_pybytes()
    with open(path, "xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Doc ledger Parquet theo schema CO DINH (ten + kieu phai dung y het); tra hang Python thuan (int / None / date / str), khong qua pandas."""
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    expected = ledger_arrow_schema()
    if [(f.name, str(f.type)) for f in table.schema] != [(f.name, str(f.type)) for f in expected]:
        raise ValueError(f"ledger_schema_unexpected: {[(f.name, str(f.type)) for f in table.schema]}")
    return table.to_pylist()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


PROVENANCE_KEYS = ("old_ledger_report", "new_ledger_report", "config_prediction", "old_config_snapshot")


def prediction_receipt(result: Mapping[str, Any]) -> dict[str, Any]:
    """Bien lai cua `predict_new_config` de dua vao provenance cua cong tong (khong gom config day du)."""
    return {"kind": "config_prediction_receipt", "ok": bool(result.get("ok")), "violations": list(result.get("violations", [])), "old_config_sha256": result.get("old_config_sha256"),
            "predicted_config_sha256": result.get("predicted_config_sha256"), "old_code_sha256": result.get("old_code_sha256"), "new_code_sha256": result.get("new_code_sha256"),
            "changed_files": list(result.get("changed_files", []))}


def tree_digest(directory: Path) -> dict[str, Any]:
    """Seal cay artifact: relative path (posix, da sap) -> sha256 cua TUNG file + digest tong. Receipt gan cay NAY, nen doi bat ky byte nao (ke ca timing duoc phep) doi receipt."""
    directory = Path(directory)
    files = {p.relative_to(directory).as_posix(): _file_sha(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    return {"files": files, "aggregate_sha256": sha256_hex(canonical_json(files))}


def _read_json_file(path: Path) -> tuple[Any, str]:
    data = Path(path).read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()


def check_provenance(paths: Mapping[str, Path] | None, *, old_version: str, new_version: str, old_samples_sha256: str, new_samples_sha256: str, old_ledger_sha256: str, new_ledger_sha256: str,
                     expected_code_sha256: str, expected_config_sha256: str, expected_old_config_sha256: str, expected_old_code_sha256: str, expected_producer_script_sha256: str, expected_producer_sql_set_sha256: str,
                     expected_base_controller_sha256: str, tool_sha256: str) -> tuple[list[str], dict[str, Any]]:
    """Bien lai tin cay cua cac buoc tien nhiem (Ph1/Ph2/Ph6), do DAC (sha tinh tu tep thuc) va doi chieu voi chinh artifact dang xet: bao cao producer ledger cu/moi (LEDGER_PASS, dung dataset, sha samples/ledger
    khop), bien lai du doan config (ok, sha moi/cu va code moi/cu == cac gia tri DOC LAP do nguoi goi cung cap), snapshot P4 cu (trusted, evaluation.ok, sha config canonical va sha code == gia tri cu DOC LAP —
    khong suy tu bien lai du doan de tranh vong tron). Sha config/code CU doc lap la hang so ghim ngoai DB (h1 `6494c1d4…`, h3 `45f2fc6a…`, code `562d22af…`).
    TUPLE PRODUCER DA DUYET (script / SQL set / base controller) la neo NGOAI do approval/runner cung cap: CA HAI bao cao phai bang tuple do ("cung producer" khac "cung producer DA DUYET"). Day la
    PRECONDITION bat buoc cua cong tong: mot receipt PASS don le khong thay the viec outer runner doi chieu tuple voi approval."""
    violations: list[str] = []
    summary: dict[str, Any] = {}
    paths = dict(paths or {})
    missing = [k for k in PROVENANCE_KEYS if k not in paths]
    unknown = sorted(set(paths) - set(PROVENANCE_KEYS))
    if missing:
        violations.append(f"provenance_missing: {missing}")
    if unknown:
        violations.append(f"provenance_unknown_keys: {unknown}")
    loaded: dict[str, Any] = {}
    for name in PROVENANCE_KEYS:
        if name in paths:
            try:
                loaded[name], digest = _read_json_file(paths[name])
                summary[name] = {"sha256": digest}
            except Exception as exc:                                                                            # noqa: BLE001
                violations.append(f"provenance_unreadable_{name}: {type(exc).__name__}")
    prediction = loaded.get("config_prediction")
    snapshot = loaded.get("old_config_snapshot")
    pins_expected = {"old_ledger_report": expected_old_config_sha256, "new_ledger_report": expected_config_sha256}
    for name, version, samples_sha, ledger_sha in (("old_ledger_report", old_version, old_samples_sha256, old_ledger_sha256), ("new_ledger_report", new_version, new_samples_sha256, new_ledger_sha256)):
        rep = loaded.get(name)
        if rep is None:
            continue
        verdict = rep.get("verdict") if isinstance(rep.get("verdict"), dict) else {}
        ledger = rep.get("ledger") if isinstance(rep.get("ledger"), dict) else {}
        file_entry = ledger.get("file") if isinstance(ledger.get("file"), dict) else {}
        parquet = rep.get("parquet") if isinstance(rep.get("parquet"), dict) else {}
        step0 = rep.get("step0") if isinstance(rep.get("step0"), dict) else {}
        step0_checks = step0.get("checks") if isinstance(step0.get("checks"), dict) else {}
        pins = rep.get("expected_config_sha256s") if isinstance(rep.get("expected_config_sha256s"), dict) else {}
        checks = {"tool": rep.get("tool") == "warehouse_flag_ledger_v1", "mode_run": rep.get("mode") == "run", "overall_ledger_pass": verdict.get("overall") == "LEDGER_PASS",
                  "dataset": rep.get("dataset") == version, "expected_samples_sha": rep.get("expected_samples_sha256") == samples_sha, "parquet_sha": parquet.get("sha256") == samples_sha,
                  "ledger_status_pass": ledger.get("status") == "PASS", "ledger_file_sha": file_entry.get("sha256") == ledger_sha, "no_refusals": not rep.get("refusals"),
                  "step0_ok_with_config_pin": step0.get("ok") is True and step0_checks.get("build_config_sha256_pin") is True,
                  "config_pin_matches_expected": pins.get(version) == pins_expected[name],
                  "repair_parity_sha_is_this_tool": rep.get("repair_parity_sha256") == tool_sha256 and rep.get("repair_parity_source_sha256") == tool_sha256,
                  "producer_script_is_approved": rep.get("script_sha256") == expected_producer_script_sha256,
                  "producer_sql_set_is_approved": rep.get("sql_set_sha256") == expected_producer_sql_set_sha256,
                  "base_controller_is_approved": rep.get("base_controller_sha256") == expected_base_controller_sha256 and rep.get("base_source_sha256") == expected_base_controller_sha256}
        if name == "old_ledger_report":                                                                          # snapshot P4 phai la cua CHINH lan chay nay
            snap_state = rep.get("snapshot") if isinstance(rep.get("snapshot"), dict) else {}
            snap_file = snap_state.get("file") if isinstance(snap_state.get("file"), dict) else {}
            checks.update(snapshot_status_pass=snap_state.get("status") == "PASS" and snap_file.get("trusted") is True,
                          snapshot_file_sha_is_provided=bool(snapshot) and snap_file.get("sha256") == summary.get("old_config_snapshot", {}).get("sha256"),
                          snapshot_run_id_is_this_run=bool(snapshot) and snapshot.get("run_id") == rep.get("run_id"))
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            violations.append(f"provenance_{name}_mismatch: {failed}")
        summary[name].update(run_id=rep.get("run_id"), script_sha256=rep.get("script_sha256"), sql_set_sha256=rep.get("sql_set_sha256"), repair_parity_sha256=rep.get("repair_parity_sha256"),
                             base_controller_sha256=rep.get("base_controller_sha256"))
    if prediction is not None:
        checks = {"kind": prediction.get("kind") == "config_prediction_receipt", "ok": prediction.get("ok") is True, "predicted_config_sha": prediction.get("predicted_config_sha256") == expected_config_sha256,
                  "new_code_sha": prediction.get("new_code_sha256") == expected_code_sha256, "old_config_sha": prediction.get("old_config_sha256") == expected_old_config_sha256,
                  "old_code_sha": prediction.get("old_code_sha256") == expected_old_code_sha256}
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            violations.append(f"provenance_config_prediction_mismatch: {failed}")
    if snapshot is not None:
        evaluation = snapshot.get("evaluation") if isinstance(snapshot.get("evaluation"), dict) else {}
        entry = (evaluation.get("versions") or {}).get(old_version) or {}
        checks = {"trusted": snapshot.get("trusted") is True, "evaluation_ok": evaluation.get("ok") is True, "old_version_present": bool(entry),
                  "canonical_sha_matches_expected_old_config_sha": entry.get("canonical_config_sha256") == expected_old_config_sha256 and entry.get("build_config_sha256_in_db") == expected_old_config_sha256,
                  "builder_code_sha_matches_expected_old_code": (entry.get("builder_code") or {}).get("code_sha256") == expected_old_code_sha256}
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            violations.append(f"provenance_old_config_snapshot_mismatch: {failed}")
    return violations, summary


def validate_repair(*, old_dir: Path, new_dir: Path, old_ledger_path: Path, new_ledger_path: Path, old_version: str, new_version: str, expected_code_sha256: str, expected_config_sha256: str,
                    expected_old_config_sha256: str, expected_old_code_sha256: str, expected_old_samples_sha256: str, expected_producer_script_sha256: str, expected_producer_sql_set_sha256: str,
                    expected_base_controller_sha256: str, provenance_paths: Mapping[str, Path] | None = None, expected_rows: int | None = EXPECTED_ROWS, expected_columns: int | None = EXPECTED_COLUMNS,
                    enforce_builder_runtime: bool = True) -> dict[str, Any]:
    """CONG TONG: ghep ledger x2 (doi chieu voi chinh Parquet), Parquet old/new, oracle, va cay artifact. `expected_code_sha256` = manifest ma ĐO doc lap cua cay hien tai; `expected_config_sha256` =
    sha DU DOAN tu `predict_new_config` (khong lay tu artifact moi). Hash samples file/content/path/output_dir cua cay moi lay tu DO DAC artifact (khong tham so tu do). Ngoai le => FAIL."""
    violations: list[str] = []
    components: dict[str, Any] = {}
    receipt_inputs: dict[str, Any] = {"tool_sha256": _file_sha(Path(__file__)), "old_version": old_version, "new_version": new_version, "expected_code_sha256": expected_code_sha256,
                                      "expected_config_sha256": expected_config_sha256, "expected_old_config_sha256": expected_old_config_sha256,
                                      "expected_old_code_sha256": expected_old_code_sha256, "expected_old_samples_sha256": expected_old_samples_sha256,
                                      "approved_producer": {"script_sha256": expected_producer_script_sha256, "sql_set_sha256": expected_producer_sql_set_sha256,
                                                            "base_controller_sha256": expected_base_controller_sha256}, "options": {"expected_rows": expected_rows, "expected_columns": expected_columns, "enforce_builder_runtime": enforce_builder_runtime}}
    anchors = {"expected_code_sha256": expected_code_sha256, "expected_config_sha256": expected_config_sha256, "expected_old_config_sha256": expected_old_config_sha256,
               "expected_old_code_sha256": expected_old_code_sha256, "expected_old_samples_sha256": expected_old_samples_sha256,
               "expected_producer_script_sha256": expected_producer_script_sha256, "expected_producer_sql_set_sha256": expected_producer_sql_set_sha256,
               "expected_base_controller_sha256": expected_base_controller_sha256}
    bad_anchors = sorted(name for name, value in anchors.items() if not (type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)))
    if bad_anchors:                                                                                          # neo phai la SHA-256 hex thuong 64 ky tu; sai => FAIL NGAY, khong tinh gi them
        violations.append(f"bad_anchor: {bad_anchors}")
        receipt = {**receipt_inputs, "verdict": "FAIL", "violation_count": len(violations)}
        return {"verdict": "FAIL", "violations": violations, "components": components, "receipt": receipt, "receipt_sha256": sha256_hex(canonical_json(receipt))}
    try:
        import numpy as np
        import pandas as pd
        import pyarrow
        receipt_inputs["runtime"] = {"pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pyarrow.__version__}
        if enforce_builder_runtime and receipt_inputs["runtime"] != BUILDER_RUNTIME:
            violations.append(f"runtime_not_builder: {receipt_inputs['runtime']} != {BUILDER_RUNTIME}")
        old_dir, new_dir = Path(old_dir), Path(new_dir)
        old_samples, new_samples = old_dir / "samples.parquet", new_dir / "samples.parquet"
        old, new = pd.read_parquet(old_samples), pd.read_parquet(new_samples)
        old_ledger, new_ledger = read_ledger(Path(old_ledger_path)), read_ledger(Path(new_ledger_path))
        receipt_inputs.update(old_samples_file_sha256=_file_sha(old_samples), new_samples_file_sha256=_file_sha(new_samples), old_ledger_file_sha256=_file_sha(Path(old_ledger_path)),
                              new_ledger_file_sha256=_file_sha(Path(new_ledger_path)), old_tree=tree_digest(old_dir), new_tree=tree_digest(new_dir))
        if receipt_inputs["old_samples_file_sha256"] != expected_old_samples_sha256:                           # artifact cu phai la dung tep da ghim ngoai (h1 1a4a099a…, h3 c0a3aef9…)
            violations.append(f"old_samples_sha_not_pinned: measured {receipt_inputs['old_samples_file_sha256']} != pinned {expected_old_samples_sha256}")
        prov_violations, prov_summary = check_provenance(provenance_paths, old_version=old_version, new_version=new_version, old_samples_sha256=receipt_inputs["old_samples_file_sha256"],
                                                         new_samples_sha256=receipt_inputs["new_samples_file_sha256"], old_ledger_sha256=receipt_inputs["old_ledger_file_sha256"],
                                                         new_ledger_sha256=receipt_inputs["new_ledger_file_sha256"], expected_code_sha256=expected_code_sha256, expected_config_sha256=expected_config_sha256,
                                                         expected_old_config_sha256=expected_old_config_sha256, expected_old_code_sha256=expected_old_code_sha256,
                                                         expected_producer_script_sha256=expected_producer_script_sha256, expected_producer_sql_set_sha256=expected_producer_sql_set_sha256,
                                                         expected_base_controller_sha256=expected_base_controller_sha256, tool_sha256=receipt_inputs["tool_sha256"])
        receipt_inputs["provenance"] = prov_summary
        violations += prov_violations
        samples = compare_samples(old, new, old_ledger=old_ledger, new_ledger=new_ledger, old_version=old_version, new_version=new_version, expected_rows=expected_rows, expected_columns=expected_columns)
        notes = samples.get("notes", {})
        components["samples"] = {"verdict": samples["verdict"], "violations": samples["violations"], "changed_rows_total": samples.get("changed_rows_total"),
                                 "ledger_old": notes.get("ledger_old"), "ledger_new": notes.get("ledger_new"), "flags": notes.get("flags")}
        violations += [f"samples: {v}" for v in samples["violations"]]
        measured_content = notes.get("new_content_sha256_recomputed")
        receipt_inputs.update(new_content_sha256_measured=measured_content, oracle_content_sha256=notes.get("oracle_content_sha256"), old_content_sha256_measured=notes.get("old_content_sha256_recomputed"))
        if measured_content is None:
            violations.append("content_sha256_not_measured")
        else:
            ctx = {"new_version": new_version, "new_code_sha256": expected_code_sha256, "new_config_sha256": expected_config_sha256, "new_output_dir": str(new_dir),
                   "new_samples_file_sha256": receipt_inputs["new_samples_file_sha256"], "new_samples_content_sha256": measured_content, "new_samples_path": str(new_samples)}
            tree = compare_json_trees(old_dir, new_dir, ctx)
            components["artifacts"] = {"violations": tree["violations"], "allowed_observed": tree["allowed_observed"], "files_compared": tree["files_compared"]}
            violations += [f"artifacts: {v}" for v in tree["violations"]]
            features_report = json.loads((new_dir / "reports/features_labels.json").read_text(encoding="utf-8"))
            samples_entry = json.loads((new_dir / "output_checksums.json").read_text(encoding="utf-8")).get("samples.parquet", {})
            reported = (features_report.get("rows"), features_report.get("columns"), samples_entry.get("rows"), samples_entry.get("columns"))
            measured_shape = (len(new), len(new.columns), len(new), len(new.columns))
            if reported != measured_shape:                                                                       # so dong/cot ghi trong bao cao moi phai BANG so do tu chinh Parquet moi
                violations.append(f"artifacts: rows_columns_mismatch: reported={reported} measured={measured_shape}")
    except Exception as exc:                                                                                    # noqa: BLE001
        violations.append(f"exception: {type(exc).__name__}: {str(exc)[:200]}")
    verdict = "PASS_REPAIR_PARITY" if not violations else "FAIL"
    receipt = {**receipt_inputs, "verdict": verdict, "violation_count": len(violations)}
    return {"verdict": verdict, "violations": violations, "components": components, "receipt": receipt, "receipt_sha256": sha256_hex(canonical_json(receipt))}


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Cong tong validate_repair (pure, doc-chi): ledger x2 + Parquet old/new + cay artifact.")
    parser.add_argument("--old-dir", type=Path, required=True)
    parser.add_argument("--new-dir", type=Path, required=True)
    parser.add_argument("--old-ledger", type=Path, required=True)
    parser.add_argument("--new-ledger", type=Path, required=True)
    parser.add_argument("--old-version", required=True)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--expected-code-sha256", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--expected-old-config-sha256", required=True, help="hang so ghim ngoai DB cua config cu (h1 6494c1d4…, h3 45f2fc6a…)")
    parser.add_argument("--expected-old-code-sha256", required=True, help="hang so ghim cua code builder cu (562d22af…)")
    parser.add_argument("--expected-old-samples-sha256", required=True, help="hang so ghim cua samples.parquet cu (h1 1a4a099a…, h3 c0a3aef9…)")
    parser.add_argument("--expected-producer-script-sha256", required=True, help="tuple producer DA DUYET (approval/runner): hash script warehouse_flag_ledger_v1.py")
    parser.add_argument("--expected-producer-sql-set-sha256", required=True, help="tuple producer DA DUYET: hash tap SQL")
    parser.add_argument("--expected-base-controller-sha256", required=True, help="tuple producer DA DUYET: hash controller nen warehouse_audit_v3.py")
    parser.add_argument("--provenance", action="append", default=[], metavar="NAME=PATH", help=f"bat buoc du: {', '.join(PROVENANCE_KEYS)}")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    provenance = dict(item.split("=", 1) for item in args.provenance)
    result = validate_repair(old_dir=args.old_dir, new_dir=args.new_dir, old_ledger_path=args.old_ledger, new_ledger_path=args.new_ledger, old_version=args.old_version, new_version=args.new_version,
                             expected_code_sha256=args.expected_code_sha256, expected_config_sha256=args.expected_config_sha256,
                             expected_old_config_sha256=args.expected_old_config_sha256, expected_old_code_sha256=args.expected_old_code_sha256,
                             expected_old_samples_sha256=args.expected_old_samples_sha256, expected_producer_script_sha256=args.expected_producer_script_sha256,
                             expected_producer_sql_set_sha256=args.expected_producer_sql_set_sha256, expected_base_controller_sha256=args.expected_base_controller_sha256,
                             provenance_paths={k: Path(v) for k, v in provenance.items()})
    with open(args.out, "xb") as handle:                                                                         # khong ghi de
        handle.write(json.dumps(result, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    print(json.dumps({"verdict": result["verdict"], "violations": len(result["violations"]), "receipt_sha256": result["receipt_sha256"]}))
    return 0 if result["verdict"] == "PASS_REPAIR_PARITY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
