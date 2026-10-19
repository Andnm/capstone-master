"""Ung vien (EVAL_PROTOCOL §3): lay mau co seed tu khong gian trong config, dung estimator, fit voi trong so fold-local, kiem hop dong xac suat va THIET BI THAT.

- Quota khoa (lr 6 / hgb 8 / xgb 8) = 2 x (3 / 4 / 4) cau hinh sieu tham so. **Cap trong so (paired_weight)**: ParameterSampler (co seed) chon 3/4/4 cau hinh KHONG gom trong so, moi cau hinh chay
  ca `none` va `balanced` => tuong phan trong so tren cung sieu tham so. `balanced` = sklearn `compute_sample_weight('balanced', y)` tren y cua TAP DANG FIT, khong bao gio tren validation/test.
- Khong early stopping, khong eval_set. Logistic: ConvergenceWarning => ung vien loi (khong coi la hop le).
- Thiet bi: sklearn (LR/HGB) la CPU. XGBoost: **thiet bi THAT doc tu Booster** (`get_booster().save_config()` -> learner.generic_param.device), khong tu `get_params()` (day chi la yeu cau cua wrapper;
  XGBoost co the am tham doi sang CPU khi khong thay GPU). `cuda` yeu cau chap nhan `cuda` / `cuda:N`; cau hinh thieu/hong/khong xac minh duoc => DeviceError (khong fallback ve yeu cau). `build_info().USE_CUDA` chi la nang luc cua binary.
- `ESTIMATOR_FACTORIES` la diem tiem phu thuoc (test thay xgb bang gia lap co Booster gia).
- Hop dong xac suat: shape (n, 3), huu han, trong [0, 1], tong hang == 1 trong `probability_tolerance`, `classes_ == [0, 1, 2]`; hoa argmax chon chi so lop nho nhat (np.argmax).
"""
from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.model_selection import ParameterSampler
from sklearn.utils.class_weight import compute_sample_weight


class CandidateError(RuntimeError):
    pass


class DeviceError(CandidateError):
    pass


class ProbabilityError(CandidateError):
    pass


@dataclass(frozen=True)
class Candidate:
    id: str
    family: str
    order: int                      # chi so toan cuc theo thu tu chay (hoa cuoi cung)
    params: dict[str, Any]          # gom ca `weight` va `pair` (chi so cau hinh sieu tham so dung chung giua none/balanced)


def _native(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    return value


def sample_candidates(cfg: dict[str, Any]) -> list[Candidate]:
    """Moi ho: ParameterSampler(space khong co `weight`, n_configs, seed) => cau hinh; moi cau hinh nhan lan luot cac `weights` => n_configs x len(weights) ung vien (quota khoa)."""
    tuning = cfg["tuning"]
    if tuning.get("sampling_strategy") != "paired_weight":
        raise CandidateError(f"sampling_strategy {tuning.get('sampling_strategy')!r} khong duoc ho tro (can paired_weight)")
    out: list[Candidate] = []
    for family in tuning["family_order"]:
        spec = tuning["families"][family]
        sampled = list(ParameterSampler(spec["space"], n_iter=int(spec["n_configs"]), random_state=int(tuning["sampler_seed"])))
        keys = [tuple(sorted((k, _native(v)) for k, v in s.items())) for s in sampled]
        if len(set(keys)) != len(keys):
            raise CandidateError(f"{family}: ParameterSampler tra ve cau hinh trung")
        n = 0
        for pair, params in enumerate(sampled):
            for weight in spec["weights"]:
                out.append(Candidate(id=f"{family}-{n:02d}", family=family, order=len(out), params={**{k: _native(v) for k, v in sorted(params.items())}, "weight": weight, "pair": pair}))
                n += 1
        if n != int(spec["n_candidates"]):
            raise CandidateError(f"{family}: {n} ung vien != quota {spec['n_candidates']}")
    return out


def candidate_set_sha256(cfg: dict[str, Any]) -> str:
    """Van tay tap ung vien da lay mau (id, ho, thu tu, tham so). Khop gia tri trong config => ParameterSampler/phien ban thu vien cho cung tap; lech => dung som (khong chay tap khac am tham)."""
    from .config import canonical_sha256
    return canonical_sha256([{"id": c.id, "family": c.family, "order": c.order, "params": c.params} for c in sample_candidates(cfg)])


def verify_candidate_set(cfg: dict[str, Any]) -> str:
    actual, expected = candidate_set_sha256(cfg), cfg["tuning"].get("candidate_set_sha256")
    if actual != expected:
        raise CandidateError(f"tap ung vien lay mau ({actual[:16]}...) khac van tay trong cau hinh ({str(expected)[:16]}...): phien ban scikit-learn/sampler khac hoac cau hinh bi sua")
    return actual


def sample_weights(y: np.ndarray, mode: str) -> np.ndarray:
    y = np.asarray(y)
    if mode == "none":
        return np.ones(len(y), dtype="float64")
    if mode == "balanced":
        if set(np.unique(y).tolist()) != {0, 1, 2}:
            raise CandidateError("balanced can du ca 3 lop trong tap dang fit")
        return compute_sample_weight("balanced", y).astype("float64")
    raise CandidateError(f"weight {mode!r} khong hop le")


def validate_probabilities(proba: Any, n_rows: int, *, tolerance: float) -> np.ndarray:
    p = np.asarray(proba, dtype="float64")
    if p.shape != (n_rows, 3):
        raise ProbabilityError(f"xac suat sai shape {p.shape}, can ({n_rows}, 3)")
    if not np.isfinite(p).all():
        raise ProbabilityError("xac suat co NaN/vo cuc")
    if (p < 0).any() or (p > 1).any():
        raise ProbabilityError("xac suat ngoai [0, 1]")
    if not np.allclose(p.sum(axis=1), 1.0, atol=tolerance, rtol=0):
        raise ProbabilityError(f"tong hang xac suat != 1 (sai lech toi da {float(np.abs(p.sum(axis=1) - 1).max()):.3g} > {tolerance})")
    return p


def make_lr(params: dict[str, Any], fixed: dict[str, Any], *, seed: int, device: str, n_jobs: int) -> Any:
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=float(params["C"]), max_iter=int(fixed["max_iter"]), solver=str(fixed["solver"]), random_state=seed)


def make_hgb(params: dict[str, Any], fixed: dict[str, Any], *, seed: int, device: str, n_jobs: int) -> Any:
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(loss=str(fixed["loss"]), early_stopping=bool(fixed["early_stopping"]), random_state=seed, max_depth=int(params["max_depth"]),
                                          learning_rate=float(params["learning_rate"]), max_iter=int(params["max_iter"]), min_samples_leaf=int(params["min_samples_leaf"]),
                                          l2_regularization=float(params["l2_regularization"]))


def make_xgb(params: dict[str, Any], fixed: dict[str, Any], *, seed: int, device: str, n_jobs: int) -> Any:
    try:
        import xgboost
    except ImportError as exc:                                                  # noqa: BLE001
        raise CandidateError("xgboost chua cai (Colab co san; may chinh khong train that)") from exc
    if device == "cuda" and not _cuda_capable(xgboost):
        raise DeviceError("cau hinh yeu cau device=cuda nhung xgboost khong co CUDA - khong am tham chay CPU")
    return xgboost.XGBClassifier(objective=str(fixed["objective"]), tree_method=str(fixed["tree_method"]), device=device, n_estimators=int(params["n_estimators"]),
                                 max_depth=int(params["max_depth"]), learning_rate=float(params["learning_rate"]), min_child_weight=float(params["min_child_weight"]),
                                 subsample=float(params["subsample"]), colsample_bytree=float(params["colsample_bytree"]), reg_lambda=float(params["reg_lambda"]),
                                 random_state=seed, n_jobs=n_jobs, verbosity=0, early_stopping_rounds=None)


def _cuda_capable(xgboost_module: Any) -> bool:
    """Nang luc cua BINARY (khong chung minh co GPU luc chay)."""
    try:
        return bool(xgboost_module.build_info().get("USE_CUDA"))
    except Exception:                                                           # noqa: BLE001
        return False


ESTIMATOR_FACTORIES: dict[str, Callable[..., Any]] = {"lr": make_lr, "hgb": make_hgb, "xgb": make_xgb}


def build_estimator(candidate: Candidate, cfg: dict[str, Any]) -> Any:
    spec = cfg["tuning"]["families"][candidate.family]
    return ESTIMATOR_FACTORIES[candidate.family](candidate.params, spec["fixed"], seed=int(cfg["seed"]), device=str(cfg["run"]["device"]), n_jobs=int(cfg["run"]["n_jobs"]))


def booster_device(estimator: Any) -> str:
    """Thiet bi NOI BO cua Booster sau fit. Thieu/hong/khong xac minh duoc => DeviceError (khong quay ve get_params)."""
    try:
        config = json.loads(estimator.get_booster().save_config())
        device = config["learner"]["generic_param"]["device"]
    except Exception as exc:  # noqa: BLE001 - moi nguyen nhan => khong xac minh duoc
        raise DeviceError(f"khong xac minh duoc device that cua Booster ({type(exc).__name__}: {exc})") from exc
    if not isinstance(device, str) or not device:
        raise DeviceError(f"device that cua Booster khong hop le: {device!r}")
    return device


def _normalise(device: str) -> str:
    return "cuda" if device == "cuda" or (device.startswith("cuda:") and device[5:].isdigit()) else device


def verify_device(candidate: Candidate, estimator: Any, requested: str) -> dict[str, Any]:
    """sklearn => cpu (khong the khac). XGBoost => device THAT tu Booster, chuan hoa `cuda:N` -> `cuda`, phai bang yeu cau. Tra {requested, actual, normalised}."""
    if candidate.family != "xgb":
        return {"requested": requested, "actual": "cpu", "normalised": "cpu", "source": "sklearn"}
    actual = booster_device(estimator)
    if _normalise(actual) != requested:
        raise DeviceError(f"device that cua Booster {actual!r} khac yeu cau {requested!r} (XGBoost co the da am tham doi sang CPU)")
    return {"requested": requested, "actual": actual, "normalised": _normalise(actual), "source": "booster.save_config"}


def fit_estimator(candidate: Candidate, cfg: dict[str, Any], x_fit: np.ndarray, y_fit: np.ndarray) -> tuple[Any, dict[str, Any]]:
    """Fit voi trong so tinh tu CHINH y_fit; tra (estimator da fit, thong tin thiet bi that). ConvergenceWarning => loi."""
    estimator = build_estimator(candidate, cfg)
    weights = sample_weights(y_fit, str(candidate.params["weight"]))
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        estimator.fit(x_fit, np.asarray(y_fit, dtype=np.int64), sample_weight=weights)
    classes = np.asarray(getattr(estimator, "classes_", []))
    if classes.shape != (3,) or not np.array_equal(classes.astype(np.int64), np.array([0, 1, 2])):
        raise ProbabilityError(f"classes_={classes.tolist()} khac [0, 1, 2]")
    return estimator, verify_device(candidate, estimator, str(cfg["run"]["device"]))


def predict_validated(estimator: Any, cfg: dict[str, Any], x_pred: np.ndarray) -> np.ndarray:
    return validate_probabilities(estimator.predict_proba(x_pred), len(x_pred), tolerance=float(cfg["run"]["probability_tolerance"]))


def fit_predict_proba(candidate: Candidate, cfg: dict[str, Any], x_fit: np.ndarray, y_fit: np.ndarray, x_pred: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    estimator, device = fit_estimator(candidate, cfg, x_fit, y_fit)
    return predict_validated(estimator, cfg, x_pred), device


def probe_device(cfg: dict[str, Any]) -> dict[str, Any]:
    """Probe THIET BI tren du lieu TONG HOP: neu cau hinh yeu cau cuda, fit mot XGBClassifier nho tren cuda va doc device THAT cua Booster (cung ham verify nhu moi fit XGB).
    Khong co GPU => DeviceError (dung ca run ngay). Ghi vao ledger rieng, KHONG tinh vao quota fit. device=cpu => bo qua."""
    requested = str(cfg["run"]["device"])
    if requested != "cuda":
        return {"requested": requested, "probe": "skipped", "actual": "cpu"}
    spec = cfg["tuning"]["families"]["xgb"]
    cand = Candidate("probe-xgb", "xgb", -1, {"max_depth": 2, "learning_rate": 0.1, "n_estimators": 2, "min_child_weight": 1, "subsample": 1.0, "colsample_bytree": 1.0, "reg_lambda": 1.0,
                                                "weight": "none", "pair": -1})
    estimator = ESTIMATOR_FACTORIES["xgb"](cand.params, spec["fixed"], seed=int(cfg["seed"]), device=requested, n_jobs=1)
    rng = np.random.default_rng(0)
    x = rng.normal(size=(60, 4))
    y = np.tile(np.array([0, 1, 2]), 20)
    try:
        estimator.fit(x, y)
    except Exception as exc:  # noqa: BLE001
        raise DeviceError(f"probe CUDA that bai: {type(exc).__name__}: {exc}") from exc
    info = verify_device(cand, estimator, requested)
    return {"requested": requested, "probe": "synthetic_fit_ok", "actual": info["actual"], "normalised": info["normalised"], "source": info["source"]}
