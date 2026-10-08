"""Replay bundle train-v3 (thread discuss/model-results-improvement-20261008, GPT file 26 contract). INFERENCE-ONLY: khong fit, khong tune, khong chon/route lai, khong cham model moi.

Tai lap `h{k}_predictions_v3.parquet` da chay tu cac bundle `h{k}_bundle_*.joblib`, tren validation + TEST DA CHAM, dung dung cac module cua goi r3 (khong hien thuc lai encoder/ratio/routing).
Dat NGOAI `ml/` cua goi r3 (khong sua CODE_MANIFEST); co manifest rieng (REPLAY_MANIFEST.json) cho helper/config.

    python replay_bundles_v3.py --code-root <thu muc chua ml/> --run-dir <run> --dataset-dir <dataset> --colab-manifest <COLAB_MANIFEST.json> --output-root <thu muc moi>

Thu tu fail-closed (KHONG joblib.load truoc khi xong buoc 1-4): 1) toan ven helper/config; 2) run (manifest/hash dau ra) + dataset + code/COLAB/lineage; 3) danh tinh bundle
(hash file nam trong manifest cua run); 4) runtime (phien ban goi) == da ghi trong run, hoac --compat-probe tuong minh; roi moi nap bundle -> dung frame theo `horizon_frames`
-> predict -> so sanh. Dung sai khoa trong replay_config.json (khong co co CLI de doi). Ma thoat: 0 PASS, 1 FAIL replay, 2 tham so, 3 toan ven/provenance, 4 runtime lech.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

TOOL_VERSION = "replay-1.0.0"
HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "replay_config.json"
MANIFEST_NAME = "REPLAY_MANIFEST.json"
KEYS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date"]
CONTEXT_COLUMNS = ["current_price", "y_true", "inference_mode", "city", "lead_time_bucket"]


def _clean(obj: Any) -> Any:
    """JSON chuan: NaN/inf -> None, numpy -> Python; khong lam mat khoa."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(float(obj)) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


class ReplayError(Exception):
    exit_code = 1


class IntegrityError(ReplayError):
    exit_code = 3


class RuntimeMismatchError(ReplayError):
    exit_code = 4


class ArgumentFail(ReplayError):
    exit_code = 2


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- phan thuan (de test)
def compare_prices(replay: np.ndarray, saved: np.ndarray, *, rtol: float, atol: float) -> dict[str, Any]:
    """Moi hang: huu han, > 0 va abs(replay - saved) <= atol + rtol * abs(saved). Khong bo outlier, khong giam ty le."""
    replay, saved = np.asarray(replay, float), np.asarray(saved, float)
    if replay.shape != saved.shape:
        raise ReplayError(f"shape replay {replay.shape} != saved {saved.shape}")
    valid = np.isfinite(replay) & np.isfinite(saved) & (replay > 0) & (saved > 0)
    delta = np.abs(replay - saved)
    within = valid & (delta <= atol + rtol * np.abs(saved))
    d = delta[valid]
    return {"n": int(len(saved)), "invalid_rows": int((~valid).sum()), "failed_rows": int((~within).sum()), "exact_match_rows": int((valid & (replay == saved)).sum()),
            "max_abs_delta_vnd": float(d.max()) if d.size else None, "p50_abs_delta_vnd": float(np.percentile(d, 50)) if d.size else None,
            "p95_abs_delta_vnd": float(np.percentile(d, 95)) if d.size else None, "p99_abs_delta_vnd": float(np.percentile(d, 99)) if d.size else None,
            "max_rel_delta": float((d / np.abs(saved[valid])).max()) if d.size else None, "pass": bool(within.all()), "bitwise_exact": bool((valid & (replay == saved)).all())}, ~within


def aggregates(y: np.ndarray, cur: np.ndarray, pred: np.ndarray, tol: float) -> dict[str, float]:
    y, cur, pred = (np.asarray(a, float) for a in (y, cur, pred))
    model_err, base_err = np.abs(pred - y), np.abs(cur - y)
    return {"n": int(len(y)), "mae_vnd": float(model_err.mean()), "persistence_mae_vnd": float(base_err.mean()),
            "lift_vnd": float(1.0 - model_err.sum() / base_err.sum()) if base_err.sum() > 0 else float("nan"),
            "accuracy20": float(np.mean(model_err / y <= tol)), "accuracy20_count": int(np.sum(model_err / y <= tol))}


def align_frames(frame: pd.DataFrame, saved: pd.DataFrame, split: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join STRICT one-to-one theo khoa business; khong drop/nearest. Khoa phai duy nhat o ca hai ben va tap khoa bang nhau; cot nguon phai khop."""
    def keyed(df: pd.DataFrame, name: str) -> pd.DataFrame:
        missing = [c for c in KEYS if c not in df.columns]
        if missing:
            raise ReplayError(f"{name}: thieu cot khoa {missing}")
        out = df.copy()
        for c in KEYS:
            out[c] = out[c].astype(str)
        if out.duplicated(KEYS).any():
            raise ReplayError(f"{name} ({split}): khoa business bi trung ({int(out.duplicated(KEYS).sum())} dong) - khong the join one-to-one")
        return out.sort_values(KEYS, kind="mergesort").reset_index(drop=True)

    a, b = keyed(frame, "frame dung lai"), keyed(saved, "predictions da luu")
    ka, kb = a[KEYS].apply(tuple, axis=1), b[KEYS].apply(tuple, axis=1)
    only_a, only_b = set(ka) - set(kb), set(kb) - set(ka)
    if only_a or only_b:
        raise ReplayError(f"({split}) tap khoa khac nhau: chi co o frame {len(only_a)}, chi co o predictions {len(only_b)} (vd {sorted(only_a)[:2]} / {sorted(only_b)[:2]})")
    for column in CONTEXT_COLUMNS:
        if column not in a.columns or column not in b.columns:
            raise ReplayError(f"({split}) thieu cot nguon {column!r}")
        x, y = a[column], b[column]
        same = np.isclose(x.astype(float), y.astype(float), rtol=0, atol=0) if column in ("current_price", "y_true") else (x.astype(str) == y.astype(str)).to_numpy()
        if not np.all(same):
            raise ReplayError(f"({split}) cot nguon {column!r} khong khop o {int((~np.asarray(same)).sum())} dong")
    return a, b


def check_bundle_contract(bundle: Mapping[str, Any], *, name: str, horizon: int, features: list[str], report: Mapping[str, Any], run_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Tu TINH LAI cac hash/danh tinh (khong tin truong tu khai) va doi chieu voi report/run; tra bang chung. Sai => ReplayError."""
    from training.v3_contract import RATIO_BLOCK_SHA256, TRAINING_VERSION_V3, feature_list_sha256  # noqa: PLC0415

    problems: list[str] = []
    required = ("model", "name", "matrix", "features", "feature_list_sha256", "effective_features", "effective_feature_list_sha256", "n_features_in", "encoder_categories",
                "routing_policy", "horizon", "config_sha256", "training_version", "target_transform")
    missing = [k for k in required if k not in bundle]
    if missing:
        raise ReplayError(f"{name}: bundle thieu truong {missing}")
    if bundle["name"] != name:
        problems.append(f"ten bundle {bundle['name']!r} != ten file {name!r}")
    if int(bundle["horizon"]) != int(horizon):
        problems.append(f"horizon bundle {bundle['horizon']} != {horizon}")
    if bundle["training_version"] != TRAINING_VERSION_V3:
        problems.append(f"training_version {bundle['training_version']!r}")
    if bundle["config_sha256"] != run_manifest.get("config_sha256"):
        problems.append("config_sha256 cua bundle != run_manifest")
    if bundle["target_transform"] != "log_ratio":
        problems.append(f"target_transform {bundle['target_transform']!r}")
    raw_sha = feature_list_sha256(list(bundle["features"]))
    eff_sha = feature_list_sha256(list(bundle["effective_features"]))
    if raw_sha != bundle["feature_list_sha256"] or raw_sha != report["features"]["sha256"] or list(bundle["features"]) != list(features) or list(report["features"]["list"]) != list(features):
        problems.append("danh sach feature (raw) / SHA tinh lai khong khop bundle - report - dictionary")
    if eff_sha != bundle["effective_feature_list_sha256"]:
        problems.append("SHA danh sach feature hieu dung tinh lai != bundle")
    if bundle["matrix"] == "tree_block":
        block = bundle.get("ratio_block") or {}
        if block.get("sha256") != RATIO_BLOCK_SHA256 or list(bundle["effective_features"]) != list(features) + list(block.get("columns", [])):
            problems.append("khoi ty le: version/SHA/cot khong khop")
    elif bundle["matrix"] in ("tree", "raw"):
        if list(bundle["effective_features"]) != list(features):
            problems.append("effective_features phai bang feature raw voi matrix tree/raw")
    else:
        problems.append(f"matrix {bundle['matrix']!r}")
    n_in = getattr(bundle["model"], "n_features_in_", None)
    if n_in is not None and int(n_in) != len(bundle["effective_features"]):
        problems.append(f"n_features_in_ cua estimator {n_in} != so cot hieu dung {len(bundle['effective_features'])}")
    if bundle["n_features_in"] is not None and int(bundle["n_features_in"]) != len(bundle["effective_features"]):
        problems.append("n_features_in trong bundle != so cot hieu dung")
    saved_policy = (report.get("routing") or {}).get("policy_by_model", {}).get(name)
    policy = bundle["routing_policy"] or {}
    modes = set(policy) | set(saved_policy or {})
    mismatched = sorted(m for m in modes if bool((policy.get(m) or {}).get("use_model", False)) != bool(((saved_policy or {}).get(m) or {}).get("use_model", False)))
    if mismatched:
        problems.append(f"routing policy bundle != report o mode {mismatched}")
    if problems:
        raise ReplayError(f"{name}: " + "; ".join(problems))
    return {"matrix": bundle["matrix"], "n_features_in": len(bundle["effective_features"]), "feature_list_sha256": raw_sha, "effective_feature_list_sha256": eff_sha,
            "routing_modes_use_model": {m: bool((policy.get(m) or {}).get("use_model", False)) for m in sorted(policy)}}


def check_runtime(recorded: Mapping[str, Any], actual: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    keys = config["runtime"]["required_package_keys"]
    mismatch = {k: {"recorded": recorded.get(k), "actual": actual.get(k)} for k in keys if recorded.get(k) != actual.get(k)}
    rec_py, act_py = str(recorded.get("python", "")), str(actual.get("python", ""))
    if ".".join(rec_py.split(".")[:2]) != ".".join(act_py.split(".")[:2]):
        mismatch["python(major.minor)"] = {"recorded": rec_py, "actual": act_py}
    return {"mismatch": mismatch, "python_patch_recorded": rec_py, "python_patch_actual": act_py, "exact": not mismatch}


def device_verdict(bundles: Mapping[str, Mapping[str, Any]], trained_device: str | None = None) -> dict[str, Any]:
    """Thiet bi THUC TE khi replay cua cac bundle XGBoost (doc tu booster). Khong doc duoc (None) => KHONG verified: khong duoc ghi exact-runtime PASS (GPT file 26 muc 2.3).
    `device_matches_training` chi la thong tin (CPU predict model train bang GPU co the van trong dung sai) - khong thay the verdict bitwise."""
    xgb = {k: v.get("xgb_actual_device_during_replay") for k, v in bundles.items() if "xgb_actual_device_during_replay" in v}
    unknown = sorted(k for k, d in xgb.items() if d is None)
    return {"xgb_bundles": xgb, "unknown": unknown, "verified": not unknown,
            "device_matches_training": (None if (trained_device is None or not xgb or unknown) else all(d == trained_device for d in xgb.values()))}


# --------------------------------------------------------------------------- dieu phoi
def verify_helper_integrity(config_path: Path) -> dict[str, Any]:
    manifest = HERE / MANIFEST_NAME
    out = {"script_sha256": sha256_file(Path(__file__)), "config_sha256": sha256_file(config_path), "manifest": "absent"}
    if manifest.exists():
        files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        for rel, sha in files.items():
            path = HERE / rel
            if not path.is_file() or sha256_file(path) != sha:
                raise IntegrityError(f"helper/config khong khop {MANIFEST_NAME}: {rel}")
        out["manifest"] = {"sha256": sha256_file(manifest), "files": len(files)}
    return out


def load_ml(code_root: Path) -> None:
    ml = Path(code_root) / "ml"
    if not (ml / "training").is_dir():
        raise ArgumentFail(f"khong thay {ml / 'training'} (--code-root phai la thu muc chua ml/)")
    sys.path.insert(0, str(ml))


def run_replay(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    config_path = Path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {"tool": {"name": "replay_bundles_v3", "version": TOOL_VERSION, "config": config}, "mode": "compat_probe_allowed" if args.compat_probe else "exact_runtime_required",
                              "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "problems": [], "horizons": {}}
    try:
        report["tool"].update(verify_helper_integrity(config_path))
        load_ml(args.code_root)
        from training.provenance import DatasetVerificationError, ProvenanceError  # noqa: PLC0415
        from training.run_transaction import verify_run_dir  # noqa: PLC0415
        from training.runner import build_context  # noqa: PLC0415
        from training.v3_contract import load_config_v3  # noqa: PLC0415
        from training.v3_runtime import check_environment, collect_versions, verify_packaged_execution  # noqa: F401,PLC0415

        run_dir, dataset_dir = Path(args.run_dir).resolve(), Path(args.dataset_dir).resolve()
        # 2) run + dataset + code/COLAB lineage
        problems = verify_run_dir(run_dir)
        if problems:
            raise IntegrityError("run khong hop le: " + "; ".join(problems))
        run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        if run_manifest.get("official") is not False or run_manifest.get("training_version") != "training-1.3.0":
            raise IntegrityError("run khong phai train-v3 non-official")
        if bool(run_manifest.get("smoke")) and not args.allow_smoke_run:
            raise IntegrityError("run la --smoke (khong phai ket qua); dung --allow-smoke-run CHI de kiem thu cong cu")
        cfg = load_config_v3()
        if not run_manifest.get("smoke") and cfg["config_sha256"] != run_manifest.get("config_sha256"):
            raise IntegrityError(f"config_sha256 cua run {run_manifest.get('config_sha256')} != cau hinh trong goi {cfg['config_sha256']}")
        try:
            context = build_context(dataset_dir, colab_manifest=args.colab_manifest, official_run=False)
            packaged = verify_packaged_execution(context["provenance"], context["colab_manifest"], context["dataset_meta"]["dataset_name"])
        except (DatasetVerificationError, ProvenanceError, OSError, ValueError) as exc:
            raise IntegrityError(f"{type(exc).__name__}: {exc}") from exc
        if context["dataset_meta"]["dataset_name"] != run_manifest["dataset"]["dataset_name"] or context["dataset_meta"]["samples_file_sha256"] != run_manifest["dataset"]["samples_file_sha256"]:
            raise IntegrityError("dataset duoc cung cap khac dataset ma run da dung (ten/samples sha256)")
        run_prov, now_prov = run_manifest.get("provenance", {}), context["provenance"]
        if run_prov.get("source") == "code_manifest" and run_prov.get("code_sha256") != now_prov.get("code_sha256"):
            raise IntegrityError("code_sha256 cua run != code dang chay (goi code khac)")
        if run_prov.get("source") == "code_manifest" and now_prov.get("source") != "code_manifest":
            raise IntegrityError("run chay tu goi co CODE_MANIFEST nhung code dang chay khong co CODE_MANIFEST")
        if args.colab_manifest is not None and (run_dir / "COLAB_MANIFEST.json").is_file() and sha256_file(run_dir / "COLAB_MANIFEST.json") != sha256_file(args.colab_manifest):
            raise IntegrityError("COLAB_MANIFEST cung cap != ban nam trong run")
        report["inputs"] = {"run_dir": str(run_dir), "run_manifest_sha256": sha256_file(run_dir / "run_manifest.json"), "dataset_name": context["dataset_meta"]["dataset_name"],
                            "samples_file_sha256": context["dataset_meta"]["samples_file_sha256"], "code_sha256": context["provenance"].get("code_sha256"), "packaged_execution": packaged,
                            "colab_manifest_sha256": sha256_file(args.colab_manifest) if args.colab_manifest else None, "smoke_run": bool(run_manifest.get("smoke"))}
        # 3) danh tinh bundle (file nam trong manifest cua run => hash da duoc verify_run_dir) TRUOC khi load
        horizons = [int(h) for h in run_manifest["expected_horizons"]]
        reports = {h: json.loads((run_dir / f"h{h}_report.json").read_text(encoding="utf-8")) for h in horizons}
        expected_bundles: dict[int, set[str]] = {}
        for h, rep in reports.items():
            winners = {c["winner"] for c in rep["champions"].values() if c["winner"] != "persistence"}
            on_disk = {p.name[len(f"h{h}_bundle_"):-len(".joblib")] for p in run_dir.glob(f"h{h}_bundle_*.joblib")}
            if winners != on_disk:
                raise IntegrityError(f"h{h}: tap bundle tren dia {sorted(on_disk)} != champions trong report {sorted(winners)}")
            expected_bundles[h] = winners
        report["inputs"]["bundle_sha256"] = {p.name: sha256_file(p) for p in sorted(run_dir.glob("h*_bundle_*.joblib"))}
        report["inputs"]["report_sha256"] = {f"h{h}": sha256_file(run_dir / f"h{h}_report.json") for h in horizons}
        report["inputs"]["predictions_sha256"] = {f"h{h}": sha256_file(run_dir / f"h{h}_predictions_v3.parquet") for h in horizons}
        # 4) runtime
        recorded = {}
        for rep in reports.values():
            recorded = (rep.get("environment") or {}).get("actual") or {}
            break
        runtime = check_runtime(recorded, collect_versions(), config)
        runtime["actual"] = collect_versions()
        runtime["recorded_in_run"] = recorded
        report["runtime"] = runtime
        if not runtime["exact"] and not args.compat_probe:
            raise RuntimeMismatchError(f"runtime lech phien ban da ghi trong run (exact-runtime replay khong the PASS): {runtime['mismatch']}. Dung moi truong khop, hoac --compat-probe tuong minh.")
        # ---- tu day moi nap bundle
        import joblib  # noqa: PLC0415

        from training.runner import horizon_frames  # noqa: PLC0415
        from training.provenance import verify_frame  # noqa: PLC0415
        from training.schema import read_dictionary, select_features  # noqa: PLC0415
        from training.v3_bundle import predict_z, routed_z  # noqa: PLC0415
        from training.v3_contract import price_from_z, stable_sort  # noqa: PLC0415
        from training.v3_models import xgb_actual_device  # noqa: PLC0415

        samples = pd.read_parquet(dataset_dir / "samples.parquet")
        verify_frame(samples, context["dataset_meta"])
        features = select_features(read_dictionary(dataset_dir))
        rtol, atol = float(config["numeric"]["rtol"]), float(config["numeric"]["atol_vnd"])
        agg = config["aggregate"]
        tol = float(agg["accuracy_tolerance"])
        overall_ok, bitwise, train_ok, train_run = True, True, True, False
        for h in horizons:
            rep, hrep = reports[h], {"models": {}, "splits": {}}
            report["horizons"][str(h)] = hrep
            frames, _ = horizon_frames(samples, h, cfg)
            frames = {k: stable_sort(v) for k, v in frames.items()}
            saved_all = pd.read_parquet(run_dir / f"h{h}_predictions_v3.parquet")
            hrep["bundles"] = {}
            loaded = {}
            replayable = {f"pred_{n}" for n in expected_bundles[h]} | {f"pred_routed:{n}" for n in expected_bundles[h]} | {"pred_persistence"}
            hrep["saved_columns_not_replayed"] = sorted(c for c in saved_all.columns if c.startswith("pred_") and c not in replayable)      # co bundle moi duoc replay; liet ke ro phan KHONG duoc kiem
            for name in sorted(expected_bundles[h]):
                try:
                    bundle = joblib.load(run_dir / f"h{h}_bundle_{name.replace(':', '_')}.joblib")
                except Exception as exc:  # noqa: BLE001 - thieu thu vien / khong tuong thich pickle => FAIL co ly do (khong doan)
                    raise ReplayError(f"h{h}: khong nap duoc bundle {name}: {type(exc).__name__}: {exc}") from exc
                hrep["bundles"][name] = check_bundle_contract(bundle, name=name, horizon=h, features=features, report=rep, run_manifest=run_manifest)
                if hasattr(bundle["model"], "get_booster"):
                    hrep["bundles"][name]["xgb_actual_device_during_replay"] = xgb_actual_device(bundle["model"])
                loaded[name] = bundle
            for split in config["splits"]:
                frame, saved = align_frames(frames[split], saved_all[saved_all["split"] == split], split)
                shrep = hrep["splits"][split] = {"rows": int(len(frame)), "models": {}}
                cur, y = frame["current_price"].to_numpy(float), frame["y_true"].to_numpy(float)
                pers_saved = saved["pred_persistence"].to_numpy(float) if "pred_persistence" in saved else None
                if pers_saved is None or not np.array_equal(pers_saved, cur):
                    raise ReplayError(f"h{h} {split}: pred_persistence != current_price")
                for name, bundle in loaded.items():
                    z = predict_z(bundle, frame)
                    variants = {name: price_from_z(cur, z, label=f"{name} replay"), f"routed:{name}": price_from_z(cur, routed_z(bundle, frame, z), label=f"routed:{name} replay")}
                    for column, price in variants.items():
                        if f"pred_{column}" not in saved.columns:
                            if column.startswith("routed:") and split == "test" and column not in rep["test"]["table"]:
                                continue
                            raise ReplayError(f"h{h} {split}: thieu cot da luu pred_{column}")
                        saved_price = saved[f"pred_{column}"].to_numpy(float)
                        stats, bad = compare_prices(price, saved_price, rtol=rtol, atol=atol)
                        by_mode = {m: int(bad[(frame["inference_mode"].astype(str) == m).to_numpy()].sum()) for m in sorted(frame["inference_mode"].astype(str).unique())}
                        stats["failed_rows_by_mode"] = by_mode
                        if bad.any():
                            idx = np.where(bad)[0][:3]
                            stats["example_failed_keys"] = [{**{k: str(frame.iloc[i][k]) for k in KEYS}, "replay": float(price[i]), "saved": float(saved_price[i])} for i in idx]
                        a_replay, a_saved = aggregates(y, cur, price, tol), aggregates(y, cur, saved_price, tol)
                        table_row = (rep["test"]["table"].get(column) if split == "test" else ((rep["validation_table"].get(column) or {}).get("validation")))
                        rep_agg = {k: (table_row or {}).get(k) for k in ("mae_vnd", "lift_vnd", "accuracy20")}
                        agg_check = {"replay": a_replay, "saved_parquet": a_saved, "report": rep_agg}
                        agg_ok = table_row is not None
                        if table_row is not None:
                            agg_ok = (abs(a_replay["mae_vnd"] - rep_agg["mae_vnd"]) <= float(agg["mae_vnd_atol"]) + float(agg["mae_vnd_rtol"]) * abs(rep_agg["mae_vnd"])
                                      and abs(a_replay["lift_vnd"] - rep_agg["lift_vnd"]) <= float(agg["lift_abs_tol"])
                                      and abs(a_replay["accuracy20"] - rep_agg["accuracy20"]) <= float(agg["accuracy20_abs_tol"]))
                            agg_check["delta_replay_minus_report"] = {k: a_replay[k] - rep_agg[k] for k in ("mae_vnd", "lift_vnd", "accuracy20")}
                            agg_check["accuracy20_count_replay_vs_saved"] = [a_replay["accuracy20_count"], a_saved["accuracy20_count"]]
                        stats["aggregates"] = agg_check
                        stats["aggregates_pass"] = bool(agg_ok)
                        shrep["models"][column] = stats
                        overall_ok &= bool(stats["pass"] and stats["aggregates_pass"])
                        bitwise &= bool(stats["bitwise_exact"])
            if config["train_in_sample"]["enabled"]:
                train_run = True
                trf = frames["train"]
                cur_t, y_t = trf["current_price"].to_numpy(float), trf["y_true"].to_numpy(float)
                hrep["train_in_sample"] = {}
                for name, bundle in loaded.items():
                    price = price_from_z(cur_t, predict_z(bundle, trf), label=f"{name} train")
                    got = aggregates(y_t, cur_t, price, tol)
                    ref = ((rep["finalists"].get(name) or rep["controls"].get(name)) or {}).get("train_in_sample") or {}
                    ok = bool(ref) and (abs(got["mae_vnd"] - ref["mae_vnd"]) <= float(agg["mae_vnd_atol"]) + float(agg["mae_vnd_rtol"]) * abs(ref["mae_vnd"])
                                        and abs(got["lift_vnd"] - ref["lift_vnd"]) <= float(agg["lift_abs_tol"]) and abs(got["accuracy20"] - ref["accuracy20"]) <= float(agg["accuracy20_abs_tol"]))
                    hrep["train_in_sample"][name] = {"replay": got, "report": {k: ref.get(k) for k in ("mae_vnd", "lift_vnd", "accuracy20")}, "pass": ok}
                    train_ok &= ok
        expected_models = {(h, n) for h in horizons for n in expected_bundles[h]}
        replayed = {(h, n) for h in horizons for n in report["horizons"][str(h)]["bundles"]}
        complete = expected_models == replayed and all(set(report["horizons"][str(h)]["splits"]) == set(config["splits"]) for h in horizons)
        if not complete:
            raise ReplayError("khong day du model/split da replay so voi champions trong report")
        verdict_numeric = "PASS" if overall_ok else "FAIL"
        all_bundles = {f"h{h}:{n}": b for h in horizons for n, b in report["horizons"][str(h)]["bundles"].items()}
        trained_device = next(((r.get("device") or {}).get("actual_device") for r in reports.values() if (r.get("device") or {}).get("actual_device")), None)
        devices = device_verdict(all_bundles, trained_device)
        runtime["xgb_devices_during_replay"] = devices
        if not overall_ok:
            overall = "FAIL"
        elif not runtime["exact"]:
            overall = "PASS_COMPAT_PROBE_ONLY"
        elif not devices["verified"]:
            overall = "PASS_BOUNDED_DEVICE_UNVERIFIED"          # runtime khop nhung thiet bi XGBoost khi replay khong doc duoc => KHONG ghi exact-runtime
        else:
            overall = "PASS_EXACT_RUNTIME"
        report["verdict"] = {"bounded_numeric_replay": verdict_numeric, "bitwise_exact": bool(bitwise and overall_ok), "train_in_sample_recompute": ("PASS" if train_ok else "FAIL") if train_run else "NOT_RUN",
                             "runtime_exact": bool(runtime["exact"]), "device_verified": bool(devices["verified"]), "device_matches_training": devices["device_matches_training"], "overall": overall,
                             "note": "bounded PASS != bitwise PASS; PASS_COMPAT_PROBE_ONLY khong phai exact-runtime replay; khong gom train in-sample vao verdict val/test"}
        report["completeness"] = {"horizons": horizons, "bundles": sorted(f"h{h}:{n}" for h, n in replayed), "splits": config["splits"]}
        return (0 if overall != "FAIL" and (train_ok or not train_run) else 1), report
    except ReplayError as exc:
        report["problems"].append(f"{type(exc).__name__}: {exc}")
        report["verdict"] = {"overall": "FAIL", "bounded_numeric_replay": "FAIL", "bitwise_exact": False}
        return exc.exit_code, report
    except Exception as exc:  # noqa: BLE001 - loi khong luong truoc cung la FAIL co bang chung, khong im lang
        report["problems"].append(f"UNEXPECTED {type(exc).__name__}: {exc}")
        report["verdict"] = {"overall": "FAIL", "bounded_numeric_replay": "FAIL", "bitwise_exact": False}
        return 1, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", required=True, type=Path, help="thu muc chua ml/ cua goi r3 da giai nen (hoac hotel-price-intelligence/)")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--colab-manifest", type=Path, default=None)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--replay-id", default=time.strftime("replay_%Y%m%d_%H%M%S"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--compat-probe", action="store_true", help="cho phep runtime lech (kiem tra tuong thich; KHONG la exact-runtime replay)")
    parser.add_argument("--allow-smoke-run", action="store_true", help="chi de kiem thu cong cu tren run --smoke")
    args = parser.parse_args(argv)
    out_dir = args.output_root / args.replay_id
    if out_dir.exists():
        print(f"FAIL: thu muc dau ra da ton tai: {out_dir} (khong ghi de)", file=sys.stderr)
        return 2
    code, report = run_replay(args)
    out_dir.mkdir(parents=True, exist_ok=False)
    path = out_dir / "replay_report.json"
    path.write_text(json.dumps(_clean(report), ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")
    (out_dir / "replay_report.json.sha256").write_text(sha256_file(path) + "\n", encoding="utf-8")
    verdict = report.get("verdict", {})
    print(f"replay verdict: {verdict.get('overall')} | bounded_numeric={verdict.get('bounded_numeric_replay')} bitwise_exact={verdict.get('bitwise_exact')} "
          f"train_in_sample={verdict.get('train_in_sample_recompute')} runtime_exact={verdict.get('runtime_exact')} | mode={report['mode']} | report: {path}")
    for problem in report.get("problems", []):
        print("PROBLEM:", problem, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
