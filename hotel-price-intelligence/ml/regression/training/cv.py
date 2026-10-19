"""Cross-validation THEO THOI GIAN co purge (khong random split - CLAUDE.md muc 6.3).

Mau co `vn_observation_date = d` dung nhan o `d + h`. Fold k: validation = mot khoi ngay lien tiep bat dau tai `s`; train = cac ngay `d` voi
`d + h < s` (tuc `d <= s - h - 1`), nen KHONG mau train nao co nhan nam trong/hoac sau khoi validation. Cac khoi validation khong chong nhau,
tang dan, nen train luon la qua khu cua validation (expanding window).
"""
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
