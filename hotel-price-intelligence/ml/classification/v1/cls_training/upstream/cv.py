# BAN COPY NGUYEN VAN (khong sua tay) tu `ml/regression/training/cv.py`.
# SHA-256 (chuan hoa LF) cua nguon luc copy: 9e994343c257a1eed73076a39d2cb0abcbae1b3966be05eb534471c2797e117a
# Sinh/kiem bang `cls_training.upstream_sync` (UPSTREAM_MANIFEST.json). Sua nguon => `verify()` bao drift; muon cap nhat phai sinh lai co chu y.
# Pham vi: CVError, PurgedExpandingWindowSplit.
from __future__ import annotations

from typing import Iterator

import numpy as np
import pandas as pd


class CVError(ValueError):
    pass


class PurgedExpandingWindowSplit:
    def __init__(self, n_splits: int, gap_days: int, min_train_days: int):
        if n_splits < 2:
            raise CVError("n_splits phai >= 2")
        self.n_splits, self.gap_days, self.min_train_days = int(n_splits), int(gap_days), int(min_train_days)

    def split(self, dates: pd.Series) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        d = pd.to_datetime(pd.Series(dates).reset_index(drop=True))
        unique = np.array(sorted(d.dt.normalize().unique()))
        # Train toi thieu can `min_train_days` ngay + gap truoc khoi validation dau tien; con lai chia cho n_splits khoi.
        usable = len(unique) - self.min_train_days - self.gap_days
        if usable < self.n_splits:
            raise CVError(
                f"qua it ngay quan sat ({len(unique)}) cho {self.n_splits} fold voi min_train_days={self.min_train_days}, "
                f"gap={self.gap_days}: can >= {self.min_train_days + self.gap_days + self.n_splits} ngay (dataset dang 'exploratory').")
        first_val = self.min_train_days + self.gap_days
        blocks = np.array_split(np.arange(first_val, len(unique)), self.n_splits)
        day_values = d.dt.normalize().to_numpy()
        for block in blocks:
            val_start, val_end = unique[block[0]], unique[block[-1]]
            train_cut = val_start - np.timedelta64(self.gap_days, "D")  # train: day < val_start - gap  <=>  d + h < s
            train_idx = np.flatnonzero(day_values < train_cut)
            val_idx = np.flatnonzero((day_values >= val_start) & (day_values <= val_end))
            if len(train_idx) == 0 or len(val_idx) == 0:
                raise CVError("fold rong - dataset qua mong cho cau hinh cv")
            yield train_idx, val_idx
