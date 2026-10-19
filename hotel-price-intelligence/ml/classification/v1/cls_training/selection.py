"""CV purged -> finalist moi ho -> champion tren validation (EVAL_PROTOCOL §2). Khong dung validation/test trong CV; khong fit gi tren validation/test.

DAG: train(primary) --CV--> 1 finalist/ho (chi ho HOAN TAT) --fit tren toan train, cham validation--> champion --KHOA--> (test chi cham allowlist da khoa va XAC MINH).
- Diem CV cua ung vien = macro-F1 3 lop co dinh tu ma tran nham lan GOP (pooled out-of-fold) cua moi fold; tung fold van duoc bao (n, support). Bang chung tung fold da chay LUON duoc giu, ke ca khi ung vien sau do loi/aborted.
- Hoa trong `tie_atol` => MCC cao hon => (champion) ho don gian hon theo `family_order` / (finalist trong ho) chi so ung vien nho hon. Cung quy tac cho CV va validation.
- Ung vien loi (fit loi, thieu lop, xac suat khong hop le, ConvergenceWarning, device) => `failed` co ly do. **Han chot DUNG CHUNG** (`deadline` tuyet doi tinh tu dau horizon): kiem truoc MOI fit duoc len lich;
  het han => cac fit con lai KHONG bat dau, ung vien `aborted`, ho `incomplete` (KHONG co finalist du da co best tam). Ho khong co ung vien hop le => `no_valid_model`. Khong ho nao hop le => khong champion.
- Ban ghi khoa tach hai tap: `cv_selected_finalists` (chon bang CV, de audit) va `finalists` (da duoc refit + danh gia tren validation thanh cong; chinh tap nay + A1/A2 la allowlist). Refit loi/bo qua do het han
  khong lam ban ghi khoa no; chi tap da danh gia duoc phep vao allowlist, khong bao gio bo sung model bang test.
- `verify_freeze`: bat buoc truoc khi mo test: tinh lai hash than ban ghi khoa, config, champion/finalist/allowlist/vai tro/trung lap.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import metrics as M
from .candidates import Candidate, fit_estimator, predict_validated
from .config import canonical_sha256
from .features import make_matrix

FREEZE_ROLES = ("champion", "finalist", "ablation_a1", "ablation_a2")
MAX_ALLOWLIST = 5


class SelectionError(RuntimeError):
    pass


class FreezeError(RuntimeError):
    pass


def _kind(cfg: dict[str, Any], family: str) -> str:
    return str(cfg["tuning"]["families"][family]["kind"])


def _pick(rows: list[dict[str, Any]], score: Callable[[dict[str, Any]], tuple[float, float]], tie_atol: float, tiebreak: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Chon theo (macro_f1 cao nhat; hoa trong tie_atol => MCC cao hon => tiebreak nho hon)."""
    top = max(score(r)[0] for r in rows)
    tied = [r for r in rows if top - score(r)[0] <= tie_atol]
    tied.sort(key=lambda r: (-score(r)[1], tiebreak(r)))
    return tied[0]


def run_cv(candidates: list[Candidate], frame: pd.DataFrame, y: np.ndarray, folds: list[tuple[np.ndarray, np.ndarray]], columns: list[str], cfg: dict[str, Any],
           *, clock: Callable[[], float] = time.monotonic, deadline: float | None = None) -> dict[str, Any]:
    started = clock()
    if deadline is None:
        deadline = started + float(cfg["run"]["wall_clock_soft_cap_seconds"])
    cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
    results: list[dict[str, Any]] = []
    exhausted = False
    devices: set[str] = set()
    attempts = {"scheduled": len(candidates) * len(folds), "completed": 0, "failed": 0, "skipped": 0}
    for cand in candidates:
        rec: dict[str, Any] = {"id": cand.id, "family": cand.family, "order": cand.order, "params": dict(cand.params), "status": "ok", "reason": None, "per_fold": [], "pooled": None,
                               "failed_fold": None, "aborted_fold": None, "seconds": 0.0}
        t_c = clock()
        pooled = np.zeros((3, 3))
        for k, (tr, va) in enumerate(folds):
            if rec["status"] != "ok":
                attempts["skipped"] += 1
                continue
            if exhausted or clock() >= deadline:
                exhausted = True
                rec.update(status="aborted", reason="wall_clock_soft_cap_exceeded", aborted_fold=k)
                attempts["skipped"] += 1
                continue
            try:
                kind = _kind(cfg, cand.family)
                if (kind, k) not in cache:
                    matrix = make_matrix(kind, columns).fit(frame.iloc[tr])
                    cache[(kind, k)] = (matrix.transform(frame.iloc[tr]), matrix.transform(frame.iloc[va]))
                x_tr, x_va = cache[(kind, k)]
                estimator, device = fit_estimator(cand, cfg, x_tr, y[tr])
                devices.add(device["actual"])
                pred = M.argmax_class(predict_validated(estimator, cfg, x_va))
                s = M.summarize(y[va], pred)
                if s["macro_f1"] is None:
                    raise SelectionError(f"fold {k}: thieu lop trong validation-fold ({s['null_reason']})")
                pooled += np.asarray(s["confusion"])
                rec["per_fold"].append({"fold": k, "n_train": int(len(tr)), "n_val": int(len(va)), "macro_f1": s["macro_f1"], "mcc": s["mcc"], "support": s["support"], "device": device["actual"]})
                attempts["completed"] += 1
            except Exception as exc:  # noqa: BLE001 - ung vien loi co ly do, GIU bang chung cac fold da chay, khong dung ca run
                rec.update(status="failed", reason=f"{type(exc).__name__}: {exc}"[:500], failed_fold=k)
                attempts["failed"] += 1
        if rec["status"] == "ok":
            rec["pooled"] = {"macro_f1": M.macro_f1(pooled), "mcc": M.mcc(pooled), "balanced_accuracy": M.balanced_accuracy(pooled), "confusion": pooled.tolist(), "n_val": int(pooled.sum())}
        rec["seconds"] = round(clock() - t_c, 3)
        results.append(rec)
    return {"candidates": results, "budget_exhausted": exhausted, "elapsed_seconds": round(clock() - started, 3), "devices": sorted(devices), "n_folds": len(folds), "attempts": attempts}


def select_finalists(cv: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    tie = float(cfg["panel"]["tie_atol"])
    families: dict[str, Any] = {}
    finalists: list[str] = []
    for fam in cfg["tuning"]["family_order"]:
        rows = [r for r in cv["candidates"] if r["family"] == fam]
        ok = [r for r in rows if r["status"] == "ok"]
        n_aborted = sum(r["status"] == "aborted" for r in rows)
        info: dict[str, Any] = {"scheduled": len(rows), "ok": len(ok), "failed": sum(r["status"] == "failed" for r in rows), "aborted": n_aborted, "finalist": None}
        if n_aborted:
            info["status"] = "incomplete"
        elif not ok:
            info["status"] = "no_valid_model"
        else:
            info["status"] = "complete"
            best = _pick(ok, lambda r: (r["pooled"]["macro_f1"], r["pooled"]["mcc"]), tie, lambda r: r["order"])
            info["finalist"] = best["id"]
            info["cv_pooled_macro_f1"] = best["pooled"]["macro_f1"]
            finalists.append(best["id"])
        families[fam] = info
    return {"families": families, "finalists": finalists}


@dataclass
class FittedFinalist:
    candidate: Candidate
    matrix: Any
    estimator: Any
    device: str
    device_info: dict[str, Any]
    columns: list[str]


def fit_on_train(candidate: Candidate, train: pd.DataFrame, y_train: np.ndarray, columns: list[str], cfg: dict[str, Any]) -> FittedFinalist:
    matrix = make_matrix(_kind(cfg, candidate.family), columns).fit(train)
    estimator, info = fit_estimator(candidate, cfg, matrix.transform(train), y_train)
    return FittedFinalist(candidate, matrix, estimator, info["actual"], info, list(columns))


def predict_frame(fitted: FittedFinalist, frame: pd.DataFrame, cfg: dict[str, Any]) -> np.ndarray:
    return predict_validated(fitted.estimator, cfg, fitted.matrix.transform(frame))


def choose_champion(finalists: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any] | None:
    """`finalists[i]` co `id`, `family`, `order`, `validation` (summarize). Champion = macro-F1 validation cao nhat; hoa => MCC => ho don gian hon => order. None neu rong."""
    if not finalists:
        return None
    rank = {fam: i for i, fam in enumerate(cfg["tuning"]["family_order"])}
    return _pick(finalists, lambda r: (r["validation"]["macro_f1"], r["validation"]["mcc"]), float(cfg["panel"]["tie_atol"]), lambda r: (rank[r["family"]], r["order"]))


def freeze_record(cv: dict[str, Any], selection: dict[str, Any], finalist_scores: list[dict[str, Any]], champion: dict[str, Any] | None, cfg: dict[str, Any],
                  allowlist: list[dict[str, Any]]) -> dict[str, Any]:
    """Ban ghi khoa TRUOC khi tao ma tran test: danh sach finalist, champion, diem validation, allowlist (model_id + vai tro). Hash chuan tac cua THAN ban ghi (khong gom hash)."""
    body = {"config_sha256": cfg["config_sha256"], "families": selection["families"], "cv_selected_finalists": list(selection["finalists"]),
            "finalists": [r["id"] for r in finalist_scores],
            "finalist_validation": [{"id": r["id"], "family": r["family"], "macro_f1": r["validation"]["macro_f1"], "mcc": r["validation"]["mcc"]} for r in finalist_scores],
            "champion": None if champion is None else champion["id"], "test_allowlist": allowlist,
            "cv_candidates": [{"id": c["id"], "status": c["status"], "macro_f1": None if c["pooled"] is None else c["pooled"]["macro_f1"]} for c in cv["candidates"]]}
    return {**body, "freeze_sha256": canonical_sha256(body)}


def verify_freeze(freeze: Any, cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """Xac minh ban ghi khoa (goi khi mo test): tinh lai hash than, config, champion/finalists/allowlist/vai tro/trung lap. Tra {model_id: role}. Loi => FreezeError."""
    if not isinstance(freeze, dict):
        raise FreezeError("ban ghi khoa khong phai mapping")
    claimed = freeze.get("freeze_sha256")
    body = {k: v for k, v in freeze.items() if k != "freeze_sha256"}
    try:
        recomputed = canonical_sha256(body)
    except (TypeError, ValueError) as exc:
        raise FreezeError(f"khong tinh duoc hash ban ghi khoa: {exc}") from exc
    if not isinstance(claimed, str) or claimed != recomputed:
        raise FreezeError("freeze_sha256 khong khop than ban ghi khoa (bi sua hoac gia mao)")
    if cfg is not None and body.get("config_sha256") != cfg.get("config_sha256"):
        raise FreezeError("config_sha256 trong ban ghi khoa khac cau hinh dang chay")
    allow = body.get("test_allowlist")
    if not isinstance(allow, list) or not allow or len(allow) > MAX_ALLOWLIST:
        raise FreezeError(f"allowlist phai la danh sach 1..{MAX_ALLOWLIST} muc")
    roles: dict[str, str] = {}
    for item in allow:
        if not isinstance(item, dict) or not isinstance(item.get("model_id"), str) or not item["model_id"] or item.get("role") not in FREEZE_ROLES:
            raise FreezeError(f"muc allowlist khong hop le: {item!r}")
        if item["model_id"] in roles:
            raise FreezeError(f"model_id trung trong allowlist: {item['model_id']!r}")
        roles[item["model_id"]] = item["role"]
    champs = [m for m, r in roles.items() if r == "champion"]
    champion = body.get("champion")
    if len(champs) != 1 or champs[0] != champion:
        raise FreezeError(f"phai co dung 1 champion trong allowlist va trung voi ban ghi (allowlist {champs}, ban ghi {champion!r})")
    finalists, selected = body.get("finalists"), body.get("cv_selected_finalists")
    if not isinstance(finalists, list) or champion not in finalists or not set(finalists) <= set(roles) or sum(r in ("champion", "finalist") for r in roles.values()) != len(finalists):
        raise FreezeError("finalists (da danh gia tren validation) khong khop allowlist")
    if not isinstance(selected, list) or not set(finalists) <= set(selected):
        raise FreezeError("finalists da danh gia phai la tap con cua cv_selected_finalists (khong bo sung model ngoai CV)")
    for model_id, role in roles.items():
        if role in ("ablation_a1", "ablation_a2") and not model_id.startswith(f"{champion}+"):
            raise FreezeError(f"ablation {model_id!r} khong xuat phat tu champion {champion!r}")
    return roles
