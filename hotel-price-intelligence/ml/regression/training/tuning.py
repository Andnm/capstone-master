"""Tinh chinh: RandomizedSearchCV de tim vung tot, roi GridSearchCV quanh ung vien (CLAUDE.md muc 3.3), CV theo thoi gian co purge,
chi tren tap TRAIN (khong bao gio dung validation/test de tune)."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV

from .models import make_tree_model


def refine_grid(best: dict[str, Any], space: dict[str, list]) -> dict[str, list]:
    """Luoi quanh `best`: voi moi sieu tham so lay gia tri best va hai gia tri LIEN KE trong danh sach roi rac cua khong gian tim kiem."""
    grid: dict[str, list] = {}
    for key, value in best.items():
        options = space[key]
        # Giu thu tu khai bao (co the chua null/None) - lien ke theo vi tri trong danh sach cau hinh.
        idx = options.index(value)
        grid[key] = [options[i] for i in sorted({max(idx - 1, 0), idx, min(idx + 1, len(options) - 1)})]
    return grid


def tune(name: str, X, y, folds: list[tuple[np.ndarray, np.ndarray]], cfg: dict[str, Any], seed: int) -> dict[str, Any]:
    spec = cfg["models"][name]["random_search"]
    space = spec["space"]
    base = make_tree_model(name, cfg, seed)
    rnd = RandomizedSearchCV(base, space, n_iter=int(spec["n_iter"]), cv=folds, scoring=cfg["scoring"], random_state=seed,
                             n_jobs=1, refit=False, error_score="raise")
    rnd.fit(X, y)
    grid_space = refine_grid(rnd.best_params_, space)
    grd = GridSearchCV(make_tree_model(name, cfg, seed), grid_space, cv=folds, scoring=cfg["scoring"], n_jobs=1, refit=False,
                       error_score="raise")
    grd.fit(X, y)
    # GridSearchCV bao gom chinh diem random best, nen ket qua cuoi khong te hon vung random da tim duoc.
    return {
        "random_best_params": rnd.best_params_, "random_best_score": float(rnd.best_score_), "random_n_iter": int(spec["n_iter"]),
        "grid_space": grid_space, "grid_best_params": grd.best_params_, "grid_best_score": float(grd.best_score_),
        "grid_n_candidates": int(len(grd.cv_results_["params"])), "n_folds": len(folds), "scoring": cfg["scoring"],
        "best_params": grd.best_params_,
    }
