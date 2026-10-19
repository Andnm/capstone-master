"""Chay MOT horizon (EVAL_PROTOCOL §2, §5, §8): CV purged -> finalist -> champion (validation) -> KHOA -> ablation A1/A2 -> mo test -> cham allowlist -> ghi giao dich.

Bat bien:
- Khung `test` (va test cua hotel unseen) nam sau `TestGate`: `open()` chi chay MOT lan voi ban ghi khoa da XAC MINH (`selection.verify_freeze`: tinh lai hash than, config, champion/finalist/allowlist/vai tro/trung lap);
  moi du doan tren test phai qua `gate.authorize(model_id, role)`; model ngoai allowlist => loi TRUOC khi truy cap test.
- **Han chot DUNG CHUNG** `deadline = t0 + soft_cap` (tinh tu dau horizon): kiem truoc MOI fit duoc len lich (CV, refit finalist, A1, A2); het han thi khong bat dau fit moi (`not_run`/`aborted`). Fit dang chay duoc phep vuot;
  neu fit cuoi ket thuc sau han thi `contract_complete=false` (ly do ghi ro) du moi thu da hoan tat. Cac buoc ghi diagnostic sau han khong fit them.
- Allowlist test = finalist (champion nam trong do, KHONG nhan doi) + A1 + A2; A1/A2 cung ho/sieu tham so/loai trong so voi champion, chi khac tap feature, fit CHI tren train.
- Khong co ho hoan tat/hop le => `no_valid_model`: khong champion, khong ablation, khong mo test (khong bien C0 thanh classifier).
- Dinh danh run lay tu pin THAT cua dataset da nap (`loaded.pin`), doi chieu cau hinh; fixture tong hop mang dinh danh tong hop ro rang (`synthetic=true`), khong bao gio dinh danh nf1/nf3 that.
- `training_success`: run chi la cache THANH CONG khi status=complete, contract_complete=true, du artifact, report/analysis nhat quan. Run diagnostic van luu de audit, KHONG dung de resume.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import metrics as M
from .artifact import ArtifactError, validate_prediction_artifact
from .candidates import Candidate, sample_candidates, verify_candidate_set
from .codeid import code_manifest, environment_summary
from .config import dataset_pin
from .data import LoadedDataset, dataset_sufficiency, describe_folds, make_folds, with_test_dates
from .features import feature_sets, prepare_frame
from .labels import LABELS, class_index
from .selection import FittedFinalist, choose_champion, fit_on_train, freeze_record, predict_frame, run_cv, select_finalists, verify_freeze
from .transaction import RunTransaction, find_complete_run
from .upstream.ratio import stable_sort

REPORT_SCHEMA = "cls-report-1"
BUNDLE_SCHEMA = "cls-bundle-1"
STRATA_COLUMNS = ("hotel_id", "vn_observation_date", "checkin_date", "canonical_series_id", "inference_mode", "lead_time_bucket", "city", "prediction_match_status", "price_velocity")


class RunnerError(RuntimeError):
    pass


class TestGate:
    """Giu khung test (+ test unseen). `open(freeze)` MOT lan, voi ban ghi khoa xac minh; `authorize(model_id, role)` truoc moi du doan test."""
    __test__ = False                                            # khong phai lop test cua pytest

    def __init__(self, frame: pd.DataFrame, events: list[dict[str, Any]], clock: Callable[[], float], t0: float, cfg: dict[str, Any] | None = None, unseen: pd.DataFrame | None = None):
        self._frame, self._events, self._clock, self._t0, self._cfg, self._unseen = frame, events, clock, t0, cfg, unseen
        self._roles: dict[str, str] | None = None
        self._freeze_sha: str | None = None
        self._unseen_opened = False

    def open(self, freeze: dict[str, Any]) -> pd.DataFrame:
        if self._roles is not None:
            raise RunnerError("test da duoc mo; khong mo lai")
        roles = verify_freeze(freeze, self._cfg)                 # FreezeError neu gia mao / sua / sai cau truc => khong truy cap khung test
        self._roles, self._freeze_sha = roles, freeze["freeze_sha256"]
        self._events.append({"event": "test_open", "t": round(self._clock() - self._t0, 3), "freeze_sha256": self._freeze_sha})
        return self._frame

    def authorize(self, model_id: str, role: str) -> None:
        if self._roles is None:
            raise RunnerError("chua mo test")
        if self._roles.get(model_id) != role:
            raise RunnerError(f"model {model_id!r} (vai tro {role!r}) khong thuoc allowlist da khoa {self._roles}")

    def authorized_models(self) -> dict[str, str]:
        if self._roles is None:
            raise RunnerError("chua mo test")
        return dict(self._roles)

    def open_unseen(self, freeze: dict[str, Any]) -> pd.DataFrame | None:
        """Hang test cua hotel unseen (mau so all-hotels): phai SAU open(), cung ban ghi khoa, mot lan."""
        if self._roles is None or freeze.get("freeze_sha256") != self._freeze_sha:
            raise RunnerError("test unseen chi mo sau open() voi CHINH ban ghi khoa do")
        if self._unseen_opened:
            raise RunnerError("test unseen da duoc mo")
        verify_freeze(freeze, self._cfg)
        self._unseen_opened = True
        self._events.append({"event": "test_open_unseen", "t": round(self._clock() - self._t0, 3), "freeze_sha256": self._freeze_sha})
        return self._unseen


@dataclass
class HorizonResult:
    report: dict[str, Any]
    predictions: pd.DataFrame | None
    bundle: dict[str, Any] | None
    events: list[dict[str, Any]] = field(default_factory=list)


def _model_meta(cand: Candidate, feature_set: str, columns: list[str], roles: list[str], device: str, model_id: str) -> dict[str, Any]:
    return {"model_id": model_id, "family": cand.family, "candidate_id": cand.id, "params": dict(cand.params), "feature_set": feature_set, "n_features": len(columns),
            "columns_sha256": hashlib.sha256("\n".join(columns).encode("utf-8")).hexdigest(), "roles": roles, "device": device, "fitted_on": "train_only"}


def _freeze_role(meta: dict[str, Any]) -> str:
    return "champion" if "champion" in meta["roles"] else meta["roles"][0]


def _prediction_rows(split: str, frame: pd.DataFrame, y: np.ndarray, proba: np.ndarray, meta: dict[str, Any], horizon: int, *, is_primary: bool = True) -> pd.DataFrame:
    pred = M.argmax_class(proba)
    out = pd.DataFrame({"warehouse_record_id": frame["warehouse_record_id"].to_numpy("int64"), "horizon": horizon, "split": split, "model_id": meta["model_id"],
                        "role": ",".join(meta["roles"]), "family": meta["family"], "feature_set": meta["feature_set"],
                        "true_class": y.astype("int8"), "pred_class": pred.astype("int8"), "p_down": proba[:, 0], "p_stable": proba[:, 1], "p_up": proba[:, 2],
                        "is_primary": is_primary})
    for col in (*STRATA_COLUMNS, f"label_match_status_h{horizon}", f"y_pct_change_h{horizon}"):
        if col in frame.columns:
            out[col] = frame[col].to_numpy()
    return out


def _summary(y: np.ndarray, proba: np.ndarray) -> dict[str, Any]:
    s = M.summarize(y, M.argmax_class(proba))
    s["log_loss"], s["brier"] = M.log_loss(y, proba), M.brier_multiclass(y, proba)
    s["probability_status"] = "uncalibrated"
    return {k: s[k] for k in ("n", "macro_f1", "mcc", "balanced_accuracy", "predicted_change_rate", "support", "confusion", "per_class", "null_reason", "log_loss", "brier", "probability_status")}


def _attempts(scheduled: int) -> dict[str, Any]:
    return {"scheduled": scheduled, "completed": 0, "failed": 0, "skipped": 0, "seconds": {}}


def run_horizon(cfg: dict[str, Any], loaded: LoadedDataset, *, clock: Callable[[], float] = time.monotonic) -> HorizonResult:
    h = int(loaded.horizon)
    t0 = clock()
    budget = float(cfg["run"]["wall_clock_soft_cap_seconds"])
    deadline = t0 + budget                                           # han chot DUNG CHUNG cho moi fit duoc len lich
    events: list[dict[str, Any]] = []

    def ev(name: str, **kw: Any) -> None:
        events.append({"event": name, "t": round(clock() - t0, 3), **kw})

    target = f"y_direction_h{h}"
    sets = feature_sets(loaded.features, loaded.dictionary)
    if len(sets["a2_no_history"]) != int(cfg["ablation"]["a2_n_features"]):
        raise RunnerError(f"A2 co {len(sets['a2_no_history'])} cot, config yeu cau {cfg['ablation']['a2_n_features']}")
    train, val = stable_sort(loaded.frames["train"]), stable_sort(loaded.frames["validation"])
    unseen = getattr(loaded, "unseen_frames", None)
    gate = TestGate(loaded.frames["test"], events, clock, t0, cfg, None if unseen is None else unseen["test"])
    y_tr, y_va = class_index(train[target]), class_index(val[target])
    folds = make_folds(train, h, cfg["cv"])
    fold_info = describe_folds(train, folds, h)
    verify_candidate_set(cfg)
    candidates = sample_candidates(cfg)
    by_id = {c.id: c for c in candidates}

    ev("cv_start", n_candidates=len(candidates), n_folds=len(folds))
    cv = run_cv(candidates, train, y_tr, folds, sets["base36"], cfg, clock=clock, deadline=deadline)
    selection = select_finalists(cv, cfg)
    ev("cv_end", budget_exhausted=cv["budget_exhausted"], finalists=selection["finalists"])

    fitted: dict[str, FittedFinalist] = {}
    val_proba: dict[str, np.ndarray] = {}
    finalist_scores: list[dict[str, Any]] = []
    finalist_failures: dict[str, str] = {}
    refits = _attempts(len(selection["finalists"]))
    for cid in selection["finalists"]:
        if clock() >= deadline:
            finalist_failures[cid] = "not_run: wall_clock_soft_cap_exceeded"
            refits["skipped"] += 1
            continue
        t1 = clock()
        cand = by_id[cid]
        try:
            ft = fit_on_train(cand, train, y_tr, sets["base36"], cfg)
            proba = predict_frame(ft, val, cfg)
        except Exception as exc:  # noqa: BLE001 - finalist refit loi: ghi ly do, khong dung run
            finalist_failures[cid] = f"{type(exc).__name__}: {exc}"[:500]
            refits["failed"] += 1
        else:
            fitted[cid], val_proba[cid] = ft, proba
            finalist_scores.append({"id": cid, "family": cand.family, "order": cand.order, "validation": _summary(y_va, proba)})
            refits["completed"] += 1
        refits["seconds"][cid] = round(clock() - t1, 3)
    champion = choose_champion(finalist_scores, cfg)
    ev("validation_scored", champion=None if champion is None else champion["id"])

    incomplete = [f"family {f}: {i['status']}" for f, i in selection["families"].items() if i["status"] != "complete"]
    incomplete += [f"finalist {c}: {why}" for c, why in finalist_failures.items()]
    if cv["budget_exhausted"]:
        incomplete.append("ngan sach thoi gian mem het trong CV")
    ledger: dict[str, Any] = {"soft_cap_seconds": budget, "cv": cv["attempts"], "finalist_refits": refits, "ablation_fits": _attempts(2), "devices": list(cv["devices"]), "probe": None}
    identity = run_identity(cfg, loaded)                              # pin THAT cua dataset da nap (assert khop cau hinh); fixture tong hop mang dinh danh tong hop
    report: dict[str, Any] = {"schema": REPORT_SCHEMA, "horizon": h, "identity": identity, "dataset_version": loaded.pin["dataset_version"], "synthetic": identity["synthetic"],
                              "config_sha256": cfg["config_sha256"], "seed": cfg["seed"], "feature_sets": {k: {"n": len(v)} for k, v in sets.items()},
                              "label_contract": {"classes": list(LABELS), "threshold": 0.02}, "rows": {"train": len(train), "validation": len(val)},
                              "data_sufficiency": dataset_sufficiency(loaded, {"train": train, "validation": val}), "cv": {**cv, "folds": fold_info}, "selection": selection, "finalist_failures": finalist_failures,
                              "finalists": [{"id": r["id"], "family": r["family"], "validation": r["validation"]} for r in finalist_scores], "events": events,
                              "claim_level_hint": "dev_exploratory", "probability_status": "uncalibrated"}
    if champion is None:
        ledger["elapsed_seconds"] = round(clock() - t0, 3)
        report.update(status="no_valid_model", champion=None, contract_complete=False, incomplete_reasons=incomplete or ["khong co finalist hop le"], freeze=None, ablation=None, test=None, models={}, ledger=ledger)
        ev("finish", status="no_valid_model")
        return HorizonResult(report, None, None, events)

    champ_id = champion["id"]
    champ_cand = by_id[champ_id]
    a1_id, a2_id = f"{champ_id}+{cfg['ablation']['a1']}", f"{champ_id}+{cfg['ablation']['a2']}"
    allowlist = [{"model_id": r["id"], "role": "champion" if r["id"] == champ_id else "finalist"} for r in finalist_scores]
    allowlist += [{"model_id": a1_id, "role": "ablation_a1"}, {"model_id": a2_id, "role": "ablation_a2"}]
    freeze = freeze_record(cv, selection, finalist_scores, champion, cfg, allowlist)
    ev("freeze", freeze_sha256=freeze["freeze_sha256"], champion=champ_id)

    models: dict[str, dict[str, Any]] = {}
    for r in finalist_scores:
        cid = r["id"]
        models[cid] = _model_meta(by_id[cid], "base36", sets["base36"], ["champion", "finalist"] if cid == champ_id else ["finalist"], fitted[cid].device, cid)
    ablation: dict[str, Any] = {}
    abl_ledger = ledger["ablation_fits"]
    last_fit_end = clock()
    for key, model_id in (("a1", a1_id), ("a2", a2_id)):
        fs = cfg["ablation"][key]
        if clock() >= deadline:
            ablation[key] = {"status": "not_run", "reason": "wall_clock_soft_cap_exceeded", "model_id": model_id, "feature_set": fs}
            abl_ledger["skipped"] += 1
            continue
        t1 = clock()
        try:
            ft = fit_on_train(champ_cand, prepare_frame(train, fs), y_tr, sets[fs], cfg)
            proba = predict_frame(ft, prepare_frame(val, fs), cfg)
        except Exception as exc:  # noqa: BLE001
            ablation[key] = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:500], "model_id": model_id, "feature_set": fs}
            abl_ledger["failed"] += 1
        else:
            fitted[model_id], val_proba[model_id] = ft, proba
            models[model_id] = _model_meta(champ_cand, fs, sets[fs], [f"ablation_{key}"], ft.device, model_id)
            ablation[key] = {"status": "ok", "model_id": model_id, "feature_set": fs, "n_features": len(sets[fs]), "validation": _summary(y_va, proba)}
            abl_ledger["completed"] += 1
        abl_ledger["seconds"][model_id] = round(clock() - t1, 3)
        last_fit_end = clock()
    ev("ablations_done", statuses={k: v["status"] for k, v in ablation.items()})
    overrun = last_fit_end - t0 > budget
    ledger["devices"] = sorted({*ledger["devices"], *[m["device"] for m in models.values()]})
    ledger["last_fit_end_seconds"] = round(last_fit_end - t0, 3)

    planned = verify_freeze(freeze, cfg)                            # ban ghi do chinh ta tao phai qua validator TRUOC khi mo test; tap model da fit phai nam trong allowlist VA dung vai tro
    outside = sorted(set(fitted) - set(planned))
    wrong_role = sorted(m for m in set(fitted) & set(planned) if planned[m] != _freeze_role(models[m]))
    if outside or wrong_role:
        raise RunnerError(f"model ngoai allowlist da khoa: {outside}; sai vai tro: {wrong_role}")
    test_frame = stable_sort(gate.open(freeze))                    # <- DUY NHAT noi test duoc doc, sau khi khoa va xac minh
    y_te = class_index(test_frame[target])
    report["data_sufficiency"] = with_test_dates(report["data_sufficiency"], int(test_frame["vn_observation_date"].nunique()))
    test_summaries: dict[str, Any] = {}
    parts = []
    for model_id, ft in fitted.items():
        gate.authorize(model_id, _freeze_role(models[model_id]))
        fs = models[model_id]["feature_set"]
        proba_te = predict_frame(ft, prepare_frame(test_frame, fs), cfg)
        test_summaries[model_id] = _summary(y_te, proba_te)
        parts.append(_prediction_rows("validation", val, y_va, val_proba[model_id], models[model_id], h))
        parts.append(_prediction_rows("test", test_frame, y_te, proba_te, models[model_id], h))
    ev("test_scored", models=sorted(test_summaries))
    unseen_info: dict[str, Any] = {"validation": 0, "test": 0}
    if unseen is not None:                                                  # mau so all-hotels: champion du doan them hang cua hotel unseen (khong dung de chon gi)
        unseen_test = gate.open_unseen(freeze)
        gate.authorize(champ_id, "champion")
        for split_name, uframe in (("validation", unseen["validation"]), ("test", unseen_test)):
            if uframe is not None and len(uframe):
                uframe = stable_sort(uframe)
                parts.append(_prediction_rows(split_name, uframe, class_index(uframe[target]), predict_frame(fitted[champ_id], uframe, cfg), models[champ_id], h, is_primary=False))
                unseen_info[split_name] = int(len(uframe))
    predictions = pd.concat(parts, ignore_index=True)

    if overrun:
        incomplete.append(f"wall_clock_soft_cap_exceeded: fit cuoi ket thuc o {last_fit_end - t0:.1f}s > han {budget:.0f}s")
    incomplete += [f"ablation {k}: {v['status']} ({v.get('reason')})" for k, v in ablation.items() if v["status"] != "ok"]
    ledger["elapsed_seconds"] = round(clock() - t0, 3)
    report.update(status="complete" if not incomplete else "incomplete", champion=champ_id, contract_complete=not incomplete, incomplete_reasons=incomplete, freeze=freeze, ablation=ablation,
                  test={"opened_after_freeze": True, "freeze_sha256": freeze["freeze_sha256"], "rows": len(test_frame), "models": test_summaries, "unseen_hotel_rows_champion_only": unseen_info},
                  models=models, ledger=ledger)
    bundle = {"schema": BUNDLE_SCHEMA, "model_id": champ_id, "family": champ_cand.family, "params": dict(champ_cand.params), "feature_set": "base36", "columns": list(sets["base36"]),
              "matrix": fitted[champ_id].matrix, "estimator": fitted[champ_id].estimator, "classes": list(LABELS), "probability_status": "uncalibrated", "fitted_on": "train_only",
              "dataset_version": loaded.pin["dataset_version"], "horizon": h, "config_sha256": cfg["config_sha256"], "identity": identity, "label_contract_threshold": 0.02, "claim_level_hint": "dev_exploratory",
              "device": fitted[champ_id].device_info}
    ev("finish", status=report["status"])
    return HorizonResult(report, predictions, bundle, events)


def run_identity(cfg: dict[str, Any], loaded: LoadedDataset) -> dict[str, Any]:
    """Dinh danh run tu pin THAT cua dataset da nap (doi chieu cau hinh), khong tu dict pin moi cua cfg gan cho khung khac. Fixture tong hop => `synthetic=true`, khong mang hash nf1/nf3."""
    pin = loaded.pin
    code = code_manifest(exclude_tests=True, suffixes=(".py", ".yaml", ".json"))
    real = "output_checksums_sha256" in pin
    if real:
        expected = dataset_pin(cfg, loaded.horizon)
        mismatch = [k for k in ("dataset_version", "output_checksums_sha256", "dataset_contract_sha256", "samples_file_sha256", "feature_list_sha256") if pin.get(k) != expected.get(k)]
        if mismatch:
            raise RunnerError(f"pin cua dataset da nap khac cau hinh: {mismatch}")
    return {"horizon": int(loaded.horizon), "synthetic": not real, "dataset_version": pin["dataset_version"], "output_checksums_sha256": pin.get("output_checksums_sha256"),
            "dataset_contract_sha256": pin.get("dataset_contract_sha256"), "samples_file_sha256": pin.get("samples_file_sha256"), "feature_list_sha256": pin.get("feature_list_sha256"),
            "config_sha256": cfg["config_sha256"], "runtime_code_sha256": code["code_sha256"], "classes": list(LABELS), "seed": int(cfg["seed"])}


def training_success(manifest: dict[str, Any], run_dir: Path, *, require_analysis: bool, snap: pd.DataFrame | None = None) -> bool:
    """Run la cache THANH CONG chi khi huan luyen hoan tat dung hop dong (khong chi ghi xong byte).
    Co `snap` (baseline snapshot DA GHIM da nap): validator doi chieu predictions voi snapshot (tap record, nhan, metadata hotel/ngay/series) va `analysis.artifact_validation` phai BANG ket qua kiem hien tai
    (analysis da luu van tuong ung dung artifact hien co). Khong co `snap` thi chi kiem noi bo (yeu hon; chi dung khi khong co ngu canh snapshot)."""
    if manifest.get("status") != "complete" or manifest.get("contract_complete") is not True:
        return False
    ident = manifest.get("identity") or {}
    h = ident.get("horizon")
    files = set(manifest.get("files") or {})
    need = {f"h{h}_report.json", f"h{h}_predictions.parquet", "CODE_MANIFEST.json", "environment_resolved.txt", "config_resolved.json"}
    if require_analysis:
        need.add(f"h{h}_analysis.json")
    if not need <= files:
        return False
    try:
        report = json.loads((Path(run_dir) / f"h{h}_report.json").read_text(encoding="utf-8"))
        champion = report.get("champion")
        if not (report.get("schema") == REPORT_SCHEMA and report.get("status") == "complete" and report.get("contract_complete") is True and champion
                and report.get("horizon") == h and report.get("config_sha256") == ident.get("config_sha256") and f"h{h}_bundle_{champion}.joblib" in files):
            return False
        summary = validate_prediction_artifact(pd.read_parquet(Path(run_dir) / f"h{h}_predictions.parquet"), report, snap)
        if require_analysis:
            analysis = json.loads((Path(run_dir) / f"h{h}_analysis.json").read_text(encoding="utf-8"))
            if analysis.get("horizon") != h or analysis.get("champion") != champion or analysis.get("artifact_validation") is None:
                return False
            if snap is not None and analysis["artifact_validation"] != json.loads(json.dumps(summary)):
                return False
    except (OSError, ValueError, KeyError, ArtifactError):
        return False
    return True


def find_successful_run(root: Path | str, identity: dict[str, Any], *, require_analysis: bool, snap: pd.DataFrame | None = None) -> Path | None:
    return find_complete_run(root, identity, predicate=lambda m, d: training_success(m, d, require_analysis=require_analysis, snap=snap))


def environment_resolved_text() -> str:
    pkgs = sorted((d.metadata["Name"] or "?", d.version) for d in metadata.distributions() if d.metadata["Name"])
    return "\n".join([f"python=={sys.version.split()[0]}", f"platform=={platform.platform()}", *[f"{n}=={v}" for n, v in pkgs]]) + "\n"


def execute_horizon(cfg: dict[str, Any], loaded: LoadedDataset, out_root: Path | str, *, run_id: str, clock: Callable[[], float] = time.monotonic,
                    analysis: Callable[..., dict[str, Any]] | None = None, probe: dict[str, Any] | None = None, snap: pd.DataFrame | None = None) -> Path:
    """Resume theo CA HORIZON chi voi run THANH CONG cung identity (training_success); run diagnostic khong bi ghi de/xoa, chay lai dung run_id MOI.
    `analysis(result, loaded, cfg) -> dict` (tuy chon) ghi them h{K}_analysis.json."""
    import joblib
    out_root = Path(out_root)
    identity = run_identity(cfg, loaded)
    existing = find_successful_run(out_root, identity, require_analysis=analysis is not None, snap=snap)
    if existing is not None:
        return existing
    result = run_horizon(cfg, loaded, clock=clock)
    if probe is not None:
        result.report["ledger"]["probe"] = probe
    h = loaded.horizon
    tx = RunTransaction(out_root, run_id)
    try:
        tx.write_json(f"h{h}_report.json", result.report)
        if result.predictions is not None:
            result.predictions.to_parquet(tx.path(f"h{h}_predictions.parquet"), index=False)
        if result.bundle is not None:
            joblib.dump(result.bundle, tx.path(f"h{h}_bundle_{result.bundle['model_id']}.joblib"))
        if analysis is not None and result.predictions is not None:
            tx.write_json(f"h{h}_analysis.json", analysis(result, loaded, cfg))
        tx.write_json("CODE_MANIFEST.json", code_manifest(exclude_tests=True))
        tx.write_text("environment_resolved.txt", environment_resolved_text())
        tx.write_json("config_resolved.json", {k: v for k, v in cfg.items()})
        return tx.commit(identity, extra={"status": result.report["status"], "contract_complete": result.report["contract_complete"], "environment": environment_summary()})
    except Exception:
        tx.abort()
        raise
