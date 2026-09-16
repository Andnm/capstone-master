"""UTC -> ngay Viet Nam bang offset CO DINH +07:00 (EDA_CURATED_PLAN.md muc 5 quy tac 5).

KHONG dung timezone he thong, KHONG dung `zoneinfo`/`pytz` "Asia/Ho_Chi_Minh" - offset co dinh, khong
DST tu 1975, la quy uoc da chot toan du an (CLAUDE.md muc 4.3; xem memory
`project_mysql_timestamp_tz_pitfall`: script ad-hoc tung lech 7h vi khong pin time_zone luc doc DB).

Moi datetime doc tu warehouse qua `eda/src/db.py` la NAIVE UTC (session da pin
`time_zone='+00:00'` o `app.warehouse.connection`). Module nay GIA DINH dung dieu do va FAIL CLOSED
neu nhan phai gia tri tz-aware - khong tu doan hay im lang bo qua.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

VN_OFFSET = dt.timedelta(hours=7)
VN_OFFSET_LABEL = "+07:00"


def to_vn_date(observed_at_utc: dt.datetime) -> dt.date:
    """1 gia tri `datetime` UTC (naive) -> 1 ngay lich Viet Nam."""
    if not isinstance(observed_at_utc, dt.datetime):
        raise TypeError(f"can datetime.datetime, nhan {type(observed_at_utc)!r}")
    if observed_at_utc.tzinfo is not None:
        raise ValueError(
            f"observed_at_utc phai la NAIVE datetime (quy uoc: da la UTC) - nhan tzinfo="
            f"{observed_at_utc.tzinfo!r}. Neu gia tri that su tz-aware, strip tz truoc va tu xac nhan "
            f"no dung la UTC, khong goi thang ham nay."
        )
    return (observed_at_utc + VN_OFFSET).date()


def to_vn_date_series(observed_at_utc: "pd.Series") -> "pd.Series":
    """Vector hoa cho 1 cot pandas datetime64 (naive, UTC theo quy uoc). Tra ve Series[object] cua
    `datetime.date` (khop kieu voi cac cot DATE khac doc thang tu MySQL qua mysql-connector)."""
    if not pd.api.types.is_datetime64_any_dtype(observed_at_utc):
        raise TypeError(f"can cot datetime64, nhan dtype={observed_at_utc.dtype!r}")
    if isinstance(observed_at_utc.dtype, pd.DatetimeTZDtype):
        raise ValueError(
            "cot dang tz-aware - EDA quy uoc datetime tu warehouse la NAIVE UTC, khong duoc gan tz o day."
        )
    if observed_at_utc.isna().any():
        raise ValueError(
            "cot co gia tri NULL/NaT - observed_at la NOT NULL trong warehouse; NULL o day nghia la "
            "query/join da lam mat dong, phai dieu tra truoc khi tinh ngay VN."
        )
    return (observed_at_utc + VN_OFFSET).dt.date


def lead_time_days(checkin_date: dt.date, observed_at_utc: dt.datetime) -> int:
    """Tinh lai `lead_time` = checkin_date - ngay VN cua observed_at, DOC LAP voi cot `lead_time` da
    luu san trong DB (dung de doi chieu o quality_findings.csv - muc 7.11: "lead time luu san khac
    lead time tinh lai")."""
    return (checkin_date - to_vn_date(observed_at_utc)).days


def lead_time_days_series(checkin_date: "pd.Series", observed_at_utc: "pd.Series") -> "pd.Series":
    vn_date = to_vn_date_series(observed_at_utc)
    checkin_normalized = pd.to_datetime(checkin_date).dt.date
    return pd.Series(
        [(c - v).days for c, v in zip(checkin_normalized, vn_date)],
        index=checkin_date.index, dtype="int64",
    )
